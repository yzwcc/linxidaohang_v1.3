from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTIZATION = ROOT / "productization"
BASE_ARCHIVE = PRODUCTIZATION / "dist" / "sparkrobot-lingxi-nav-1.0.0-test.1-fef658bf.tar.gz"
PRIVATE_KEY = PRODUCTIZATION / "build" / "test-vendor-keys" / "license-signing-private.pem"
PUBLIC_KEY = PRODUCTIZATION / "build" / "test-vendor-keys" / "license-signing-public.pem"
PRODUCT_BASE = Path("opt/sparkrobot/lingxi-nav/releases")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def openssl() -> str:
    for candidate in (shutil.which("openssl"), r"C:\Program Files\Git\usr\bin\openssl.exe"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise unittest.SkipTest("OpenSSL 不可用")


def write_lf(path: Path, source: Path) -> None:
    path.write_text(source.read_text(encoding="utf-8").replace("\r\n", "\n"), encoding="utf-8", newline="\n")
    path.chmod(0o755)


def prepare_release(source: Path, destination: Path, version: str) -> Path:
    shutil.copytree(source, destination)
    old_manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    old_version = str(old_manifest["version"])
    old_root = destination / "rootfs" / PRODUCT_BASE / old_version
    version_root = destination / "rootfs" / PRODUCT_BASE / version
    old_root.rename(version_root)

    write_lf(destination / "install_product.sh", PRODUCTIZATION / "runtime" / "install_product.sh")
    verify_dir = version_root / "system" / "usr" / "lib" / "sparkrobot-license"
    for script_name in ("verify_license.py", "verify_manifest.py", "verify_stable.py"):
        write_lf(verify_dir / script_name, PRODUCTIZATION / "bootstrap" / script_name)
    write_lf(
        version_root / "system" / "usr" / "local" / "sbin" / "sparkrobot-lingxi-product",
        PRODUCTIZATION / "runtime" / "product_control.sh",
    )

    stable_entries = []
    system_root = version_root / "system"
    for path in sorted(item for item in system_root.rglob("*") if item.is_file()):
        stable_entries.append({
            "path": path.relative_to(system_root).as_posix(),
            "sha256": digest(path),
            "size": path.stat().st_size,
        })
    (version_root / "stable-files.json").write_text(
        json.dumps({"format": "sparkrobot-stable-files-v1", "files": stable_entries}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    files = []
    rootfs = destination / "rootfs"
    for path in sorted(item for item in rootfs.rglob("*") if item.is_file()):
        files.append({
            "path": path.relative_to(rootfs).as_posix(),
            "sha256": digest(path),
            "size": path.stat().st_size,
        })
    manifest = {
        **old_manifest,
        "version": version,
        "strict_roots": [(PRODUCT_BASE / version).as_posix()],
        "files": files,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    subprocess.run(
        [openssl(), "dgst", "-sha256", "-sign", str(PRIVATE_KEY), "-out", str(destination / "manifest.sig"), str(manifest_path)],
        check=True,
        capture_output=True,
    )
    return destination


def wsl_path(path: Path) -> str:
    resolved = path.resolve().as_posix()
    if len(resolved) < 3 or resolved[1:3] != ":/":
        raise ValueError(f"不是可转换的 Windows 绝对路径：{resolved}")
    return f"/mnt/{resolved[0].lower()}/{resolved[3:]}"


class InstallLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.name != "nt" or not shutil.which("wsl.exe"):
            raise unittest.SkipTest("该隔离生命周期测试需要 Windows WSL")
        if not BASE_ARCHIVE.is_file() or not PRIVATE_KEY.is_file() or not PUBLIC_KEY.is_file():
            raise unittest.SkipTest("缺少完整测试归档或测试签名密钥")

    def test_first_install_upgrade_preserves_state_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            temp = Path(name)
            unpacked = temp / "base"
            with tarfile.open(BASE_ARCHIVE, "r:gz") as archive:
                archive.extractall(unpacked, filter="data")
            base = next(path for path in unpacked.iterdir() if path.is_dir())
            release_v1 = prepare_release(base, temp / "release-v1", "1.0.0-lifecycle")
            release_v2 = prepare_release(base, temp / "release-v2", "1.0.1-lifecycle")
            secrets = temp / "secrets.env"
            secrets.write_text(
                "LINGXI_APP_ID=APP_TEST\n"
                "LINGXI_APP_KEY=test-app-key\n"
                "LINGXI_APP_SECRET=test-app-secret\n"
                "DOUBAO_API_KEY=test-doubao-key\n",
                encoding="utf-8",
                newline="\n",
            )

            v1 = shlex.quote(wsl_path(release_v1))
            v2 = shlex.quote(wsl_path(release_v2))
            secrets_path = shlex.quote(wsl_path(secrets))
            script = f"""
set -euxo pipefail
root="$(mktemp -d /tmp/sparkrobot-install-lifecycle.XXXXXX)"
trap 'rm -rf -- "$root"' EXIT
export SPARKROBOT_INSTALL_ROOT="$root"
export SPARKROBOT_INSTALL_TEST_MODE=true
export SPARKROBOT_ALLOW_NON_ARM64=true

bash {v1}/install_product.sh --release-dir {v1} --secrets {secrets_path}
test "$(basename "$(readlink -f "$root/opt/sparkrobot/lingxi-nav/current")")" = "1.0.0-lifecycle"
test -f "$root/etc/systemd/system/lingxi-nav-console.service"
test -f "$root/etc/systemd/system/lingxi-java-agent-robot.service"
test "$(stat -c %a "$root/etc/sudoers.d/sparkrobot-lingxi-nav")" = "440"

printf '%s\n' '{{"map":"customer-map"}}' > "$root/var/lib/sparkrobot/lingxi-nav/maps/customer-map.json"
python3 - "$root/var/lib/sparkrobot/lingxi-nav/unified_nav_config.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["maps"] = [{{"name": "customer-map"}}]
data["points"] = [{{"id": "point-1", "name": "展厅"}}]
data["exports"] = {{"tour_tasks": [{{"name": "客户讲解路线"}}]}}
data["navigation"]["default_max_forward_speed"] = 0.22
path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
PY
properties_before="$(sha256sum "$root/etc/sparkrobot/lingxi-nav/lingxi-nav.properties" | cut -d' ' -f1)"

bash {v2}/install_product.sh --release-dir {v2}
test "$(basename "$(readlink -f "$root/opt/sparkrobot/lingxi-nav/current")")" = "1.0.1-lifecycle"
test "$(cat "$root/var/lib/sparkrobot/lingxi-nav/previous-version")" = "1.0.0-lifecycle"
test -f "$root/var/lib/sparkrobot/lingxi-nav/maps/customer-map.json"
test "$properties_before" = "$(sha256sum "$root/etc/sparkrobot/lingxi-nav/lingxi-nav.properties" | cut -d' ' -f1)"
python3 - "$root/var/lib/sparkrobot/lingxi-nav/unified_nav_config.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["maps"] == [{{"name": "customer-map"}}]
assert data["points"][0]["id"] == "point-1"
assert data["exports"]["tour_tasks"][0]["name"] == "客户讲解路线"
assert data["navigation"]["default_max_forward_speed"] == 0.22
PY

control="$root/usr/local/sbin/sparkrobot-lingxi-product"
bash "$control" verify
bash "$control" rollback
test "$(basename "$(readlink -f "$root/opt/sparkrobot/lingxi-nav/current")")" = "1.0.0-lifecycle"
test -f "$root/var/lib/sparkrobot/lingxi-nav/maps/customer-map.json"
"""
            script_file = temp / "run-lifecycle.sh"
            script_file.write_text(script.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
            result = subprocess.run(
                ["wsl.exe", "-u", "root", "--", "bash", wsl_path(script_file)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)


if __name__ == "__main__":
    unittest.main()
