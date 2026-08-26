from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PRODUCTIZATION = ROOT / "productization"
COMMON = PRODUCTIZATION / "common"
BOOTSTRAP = PRODUCTIZATION / "bootstrap"
VENDOR = PRODUCTIZATION / "vendor"
sys.path.insert(0, str(COMMON))
sys.path.insert(0, str(BOOTSTRAP))

import device_identity  # noqa: E402
import verify_license  # noqa: E402
import verify_manifest  # noqa: E402
import verify_stable  # noqa: E402


def load_build_release():
    spec = importlib.util.spec_from_file_location("build_release", VENDOR / "build_release.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def openssl() -> str:
    for candidate in (
        shutil.which("openssl"),
        r"C:\Program Files\Git\usr\bin\openssl.exe",
    ):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise unittest.SkipTest("OpenSSL 不可用")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProductizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.openssl_patch = mock.patch.dict(os.environ, {"OPENSSL_BIN": openssl()})
        cls.openssl_patch.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.openssl_patch.stop()

    def test_device_fingerprint_prefers_hardware_serial(self) -> None:
        values = {
            "/sys/firmware/devicetree/base/serial-number": b"SERIAL-001\x00",
            "/etc/machine-id": b"machine-a\n",
        }
        first = device_identity.device_fingerprint(reader=lambda path: values.get(path, b""), architecture="aarch64")
        values["/etc/machine-id"] = b"machine-b\n"
        second = device_identity.device_fingerprint(reader=lambda path: values.get(path, b""), architecture="aarch64")
        self.assertEqual(first, second)

    def test_device_fingerprint_accepts_duplicate_serial_path(self) -> None:
        first_values = {"/sys/firmware/devicetree/base/serial-number": b"SERIAL-002"}
        second_values = {"/proc/device-tree/serial-number": b"SERIAL-002"}
        first = device_identity.device_fingerprint(reader=lambda path: first_values.get(path, b""), architecture="aarch64")
        second = device_identity.device_fingerprint(reader=lambda path: second_values.get(path, b""), architecture="aarch64")
        self.assertEqual(first, second)

    def test_license_rejects_wrong_device_expiry_and_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            private_key = root / "private.pem"
            public_key = root / "public.pem"
            subprocess.run([openssl(), "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private_key)], check=True, capture_output=True)
            subprocess.run([openssl(), "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)], check=True, capture_output=True)
            license_path = root / "license.json"
            signature = root / "license.sig"
            now = datetime.now(timezone.utc)
            payload = {
                "format": "sparkrobot-license-v1",
                "product_id": device_identity.PRODUCT_ID,
                "license_id": "test-license",
                "customer": "test",
                "device_fingerprint": "expected-device",
                "architecture": "aarch64",
                "features": ["navigation"],
                "not_before": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }

            def sign() -> None:
                license_path.write_text(json.dumps(payload), encoding="utf-8")
                subprocess.run([openssl(), "dgst", "-sha256", "-sign", str(private_key), "-out", str(signature), str(license_path)], check=True, capture_output=True)

            sign()
            with mock.patch.object(verify_license, "device_fingerprint", return_value="expected-device"):
                verified = verify_license.verify_license(license_path, signature, public_key, product_id=device_identity.PRODUCT_ID, required_features=["navigation"])
            self.assertEqual(verified["license_id"], "test-license")

            with mock.patch.object(verify_license, "device_fingerprint", return_value="another-device"):
                with self.assertRaisesRegex(RuntimeError, "不匹配"):
                    verify_license.verify_license(license_path, signature, public_key, product_id=device_identity.PRODUCT_ID, required_features=[])

            payload["expires_at"] = (now - timedelta(seconds=1)).isoformat()
            sign()
            with mock.patch.object(verify_license, "device_fingerprint", return_value="expected-device"):
                with self.assertRaisesRegex(RuntimeError, "已过期"):
                    verify_license.verify_license(license_path, signature, public_key, product_id=device_identity.PRODUCT_ID, required_features=[])

            license_path.write_text(license_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "签名无效"):
                verify_license.verify_signature(license_path, signature, public_key)

    def test_manifest_rejects_tamper_and_unsigned_extra_file(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root = base / "root"
            protected = root / "opt" / "product"
            protected.mkdir(parents=True)
            artifact = protected / "app.bin"
            artifact.write_bytes(b"signed-content")
            private_key = base / "private.pem"
            public_key = base / "public.pem"
            subprocess.run([openssl(), "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private_key)], check=True, capture_output=True)
            subprocess.run([openssl(), "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)], check=True, capture_output=True)
            manifest = base / "manifest.json"
            signature = base / "manifest.sig"
            manifest.write_text(json.dumps({
                "format": "sparkrobot-release-manifest-v1",
                "version": "test",
                "strict_roots": ["opt/product"],
                "files": [{"path": "opt/product/app.bin", "sha256": digest(artifact), "size": artifact.stat().st_size}],
            }), encoding="utf-8")
            subprocess.run([openssl(), "dgst", "-sha256", "-sign", str(private_key), "-out", str(signature), str(manifest)], check=True, capture_output=True)
            verify_manifest.verify_manifest(root, manifest, signature, public_key)
            artifact.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "校验失败"):
                verify_manifest.verify_manifest(root, manifest, signature, public_key)
            artifact.write_bytes(b"signed-content")
            (protected / "injected.py").write_text("print('injected')", encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "未签名文件"):
                verify_manifest.verify_manifest(root, manifest, signature, public_key)

    def test_stable_file_verifier_detects_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "etc" / "service.conf"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"trusted")
            file_list = root / "stable.json"
            file_list.write_text(json.dumps({
                "format": "sparkrobot-stable-files-v1",
                "files": [{"path": "etc/service.conf", "sha256": digest(target), "size": 7}],
            }), encoding="utf-8")
            self.assertEqual(verify_stable.verify_stable(root, file_list), 1)
            target.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "已被修改"):
                verify_stable.verify_stable(root, file_list)

    def test_release_scan_rejects_business_source_and_secret(self) -> None:
        build_release = load_build_release()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            package = root / "navigation" / "robot_local_navigation_v3"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="ascii")
            (package / "business.py").write_text("VALUE = 1", encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "业务源码"):
                build_release.scan_release(root)
            (package / "business.py").unlink()
            (root / "console").mkdir()
            (root / "console" / "app.mjs").write_text("const token='sk-abcdefghijklmnopqrstuvwxyz123456';", encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "密钥或令牌"):
                build_release.scan_release(root)

    def test_release_requires_java_17_runtime(self) -> None:
        build_release = load_build_release()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            package = root / "navigation" / "robot_local_navigation_v3"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="ascii")
            (package / "module.cpython-310-aarch64-linux-gnu.so").write_bytes(b"not-an-elf")
            node = root / "node"
            node.write_bytes(b"not-an-elf")
            java_runtime = root / "java"
            (java_runtime / "bin").mkdir(parents=True)
            (java_runtime / "bin" / "java").write_bytes(b"not-an-elf")
            (java_runtime / "release").write_text('JAVA_VERSION="11.0.30"\n', encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "JRE 17"):
                build_release.validate_native_inputs(
                    root / "navigation",
                    node,
                    java_runtime,
                    allow_non_arm64=True,
                )
            (java_runtime / "release").write_text('JAVA_VERSION="17.0.20.1"\n', encoding="ascii")
            build_release.validate_native_inputs(
                root / "navigation",
                node,
                java_runtime,
                allow_non_arm64=True,
            )

    def test_runtime_dependency_copy_keeps_imported_doc_directory(self) -> None:
        build_release = load_build_release()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / "source"
            target = root / "target"
            (source / "yaml" / "dist" / "compose").mkdir(parents=True)
            (source / "yaml" / "dist" / "doc").mkdir(parents=True)
            (source / "yaml" / "dist" / "compose" / "composer.js").write_text(
                "require('../doc/directives.js')\n", encoding="ascii"
            )
            (source / "yaml" / "dist" / "doc" / "directives.js").write_text(
                "module.exports = {}\n", encoding="ascii"
            )
            build_release.copy_runtime_dependencies(source, target)
            self.assertTrue((target / "yaml" / "dist" / "doc" / "directives.js").is_file())

    def test_product_scripts_do_not_embed_credentials(self) -> None:
        files = [path for path in PRODUCTIZATION.rglob("*") if path.is_file() and "tests" not in path.parts and "__pycache__" not in path.parts]
        forbidden = [b"ROBOT_SSH_PASSWORD=1", b"REAL_PASSWORD_DO_NOT_COMMIT"]
        for path in files:
            data = path.read_bytes()
            for value in forbidden:
                self.assertNotIn(value, data, str(path))


if __name__ == "__main__":
    unittest.main()
