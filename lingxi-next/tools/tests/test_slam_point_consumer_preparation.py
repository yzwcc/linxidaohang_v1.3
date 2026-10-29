"""Clearly synthetic nonexecutable Node headers and package files only."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import prepare_glim_point_consumer as consumer


def bundle_fixture(root):
    files = {
        "node/bin/node": b"\x7fELF\x02\x01" + b"\0" * 12 + (183).to_bytes(2, "little") + b"synthetic not executable",
        "node/LICENSE": b"synthetic license",
        "consumer.mjs": b"// synthetic consumer, not the actual API\n",
        "package.json": json.dumps({"name": "synthetic", "dependencies": {"fixture": "1.0.0"}}).encode(),
        "package-lock.json": json.dumps({"packages": {
            "": {"dependencies": {"fixture": "1.0.0"}},
            "node_modules/fixture": {"version": "1.0.0"},
        }}).encode(),
        "node_modules/fixture/package.json": b'{"name":"fixture","version":"1.0.0"}',
        "node_modules/fixture/index.js": b"// synthetic production fixture\n",
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {
        "schemaVersion": 1, "purpose": consumer.PURPOSE, "inputMode": "recorded",
        "nodeVersion": consumer.VERSION, "nodeArchiveSha256": consumer.NODE_ARCHIVE_SHA256,
        "packageLockSha256": consumer.sha256(root / "package-lock.json"),
        "consumerBundleSha256": consumer.sha256(root / "consumer.mjs"),
        "sourceSha256": {"synthetic.ts": "a" * 64}, "referenceEvidenceSha256": "b" * 64,
        "installedPackages": {"node_modules/fixture": "1.0.0"},
        "files": consumer.inventory(root), "boundaries": dict.fromkeys(consumer.BOUNDARIES, False),
    }
    return manifest


def save_manifest(root, manifest):
    (root / consumer.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return consumer.sha256(root / consumer.MANIFEST)


class ConsumerPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = bundle_fixture(self.root)
        self.digest = save_manifest(self.root, self.manifest)

    def test_verification_never_executes_synthetic_arm64_header(self):
        with patch.object(consumer.subprocess, "run", side_effect=AssertionError("must not execute")):
            self.assertEqual(self.manifest, consumer.verify_bundle(self.root, self.digest))

    def test_external_manifest_digest_and_changed_binary_are_rejected(self):
        with self.assertRaises(ValueError):
            consumer.verify_bundle(self.root, "0" * 64)
        (self.root / "node/bin/node").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            consumer.verify_bundle(self.root, self.digest)

    def test_wrong_architecture_rejected_even_when_file_inventory_is_updated(self):
        path = self.root / "node/bin/node"
        data = bytearray(path.read_bytes())
        data[18:20] = (62).to_bytes(2, "little")
        path.write_bytes(data)
        self.manifest["files"] = consumer.inventory(self.root)
        digest = save_manifest(self.root, self.manifest)
        with self.assertRaisesRegex(ValueError, "aarch64"):
            consumer.verify_bundle(self.root, digest)

    def test_bundle_cannot_claim_live_or_target_execution(self):
        original = deepcopy(self.manifest)
        for key, value in (("inputMode", "live"), ("nodeVersion", "other"),
                           ("schemaVersion", True), ("nodeArchiveSha256", "0" * 64)):
            manifest = {**deepcopy(original), key: value}
            digest = save_manifest(self.root, manifest)
            with self.subTest(key=key), self.assertRaises(ValueError):
                consumer.verify_bundle(self.root, digest)
        for key in consumer.BOUNDARIES:
            manifest = deepcopy(original)
            manifest["boundaries"][key] = True
            digest = save_manifest(self.root, manifest)
            with self.subTest(key=key), self.assertRaises(ValueError):
                consumer.verify_bundle(self.root, digest)

    def test_file_inventory_boolean_counter_alias_or_wrong_mode_rejected(self):
        for key, value in (("bytes", True), ("executable", True), ("sha256", "bad")):
            manifest = deepcopy(self.manifest)
            manifest["files"]["consumer.mjs"][key] = value
            digest = save_manifest(self.root, manifest)
            with self.subTest(key=key), self.assertRaises(ValueError):
                consumer.verify_bundle(self.root, digest)

    def test_unexpected_file_and_native_addon_are_rejected(self):
        extra = self.root / "undeclared"
        extra.write_bytes(b"synthetic extra")
        with self.assertRaises(ValueError):
            consumer.verify_bundle(self.root, self.digest)
        extra.unlink()
        (self.root / "node_modules/fixture/addon.node").write_bytes(b"synthetic native addon")
        with self.assertRaises(ValueError):
            consumer.inventory(self.root)

    def test_missing_required_package_or_changed_version_rejected(self):
        lock = json.loads((self.root / "package-lock.json").read_text())
        lock["packages"]["node_modules/missing"] = {"version": "2.0.0"}
        with self.assertRaises(ValueError):
            consumer.installed_packages(self.root, lock)
        (self.root / "node_modules/fixture/package.json").write_text('{"version":"2.0.0"}')
        with self.assertRaises(ValueError):
            consumer.installed_packages(self.root, lock)

    def test_development_and_install_script_dependencies_rejected(self):
        for key in ("dev", "hasInstallScript"):
            lock = json.loads((self.root / "package-lock.json").read_text())
            lock["packages"]["node_modules/fixture"][key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                consumer.installed_packages(self.root, lock)

    def test_npm_environment_cannot_read_host_credentials_or_share_config_files(self):
        environment = consumer.npm_environment(PurePosixPath("/new/private"))
        self.assertEqual("/new/private", environment["HOME"])
        self.assertNotEqual(environment["npm_config_userconfig"], environment["npm_config_globalconfig"])
        self.assertEqual("/new/private/private-npm-cache", environment["npm_config_cache"])
        for name in ("NPM_TOKEN", "NODE_AUTH_TOKEN", "LD_PRELOAD", "SSH_AUTH_SOCK", "PYTHONPATH"):
            self.assertNotIn(name, environment)

    def test_consumer_source_or_bundle_identity_mismatch_rejected(self):
        public = {
            "consumerBundleSha256": consumer.sha256(self.root / "consumer.mjs"),
            "transportSourceSha256": {"synthetic.ts": "a" * 64},
        }
        with patch.object(consumer.Path, "read_text", return_value=json.dumps(public)), \
                patch.object(consumer.pipe, "source_hashes", return_value=public["transportSourceSha256"]):
            self.assertEqual(public, consumer.verified_consumer(self.root / "consumer.mjs"))
            (self.root / "consumer.mjs").write_bytes(b"changed synthetic")
            with self.assertRaises(ValueError):
                consumer.verified_consumer(self.root / "consumer.mjs")

    def test_wrong_preparation_host_rejected_before_input_reads_or_install(self):
        with patch.object(consumer.platform, "system", return_value="Windows"), \
                patch.object(consumer.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                consumer.prepare(argparse.Namespace())
            run.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "Owned symlink fixture requires POSIX")
    def test_linked_dependency_is_not_accepted_as_a_direct_bundle_member(self):
        (self.root / "node_modules/fixture/link.js").symlink_to("index.js")
        with self.assertRaises(ValueError):
            consumer.inventory(self.root)


if __name__ == "__main__":
    unittest.main()
