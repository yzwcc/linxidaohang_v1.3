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
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INTERNAL = ROOT / "internal_deployment"
RUNTIME = INTERNAL / "runtime"
FIELD_JAR = INTERNAL / "build" / "robot-local-navigation-java-all.jar"
FIELD_JAR_SHA256 = "d3858827d71aa54dc4b7cc794a0d8446cf0168d65e4cca0a2e686b6f7012dd04"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def wsl_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if len(value) < 3 or value[1:3] != ":/":
        raise ValueError(f"无法转换 Windows 路径：{value}")
    return f"/mnt/{value[0].lower()}/{value[3:]}"


def make_release(destination: Path, version: str) -> Path:
    destination.mkdir(parents=True)
    for name in ("install.sh", "configure.sh", "control.sh", "quick_install.sh"):
        shutil.copy2(RUNTIME / name, destination / name)
    for name in ("scripts", "templates", "systemd"):
        shutil.copytree(RUNTIME / name, destination / name)

    payload = destination / "payload"
    files = {
        "console/dist/index.js": b"console.log('test')\n",
        "runtime/node": b"test-node\n",
        "runtime/java/bin/java": b"test-java\n",
        "runtime/java/bin/keytool": b"test-keytool\n",
        "runtime/java/lib/jspawnhelper": b"test-helper\n",
        "navigation/robot_local_navigation_v3/lingxi_nav_bridge.py": b"pass\n",
        "java/robot-local-navigation-java-all.jar": b"PK\x03\x04test\n",
    }
    for relative, data in files.items():
        path = payload / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    entries = []
    for path in sorted(item for item in payload.rglob("*") if item.is_file()):
        entries.append({
            "path": path.relative_to(payload).as_posix(),
            "sha256": digest(path),
            "size": path.stat().st_size,
        })
    (destination / "payload-manifest.json").write_text(
        json.dumps({"format": "sparkrobot-internal-payload-v1", "files": entries}),
        encoding="utf-8",
    )
    (destination / "release-info.json").write_text(
        json.dumps({
            "format": "sparkrobot-internal-plaintext-release-v1",
            "version": version,
            "architecture": "aarch64",
            "encrypted": False,
            "device_bound": False,
        }),
        encoding="utf-8",
    )
    return destination


class InternalReleaseTest(unittest.TestCase):
    def test_built_archives_are_complete_and_linux_ready(self) -> None:
        name = "sparkrobot-lingxi-nav-internal-1.0.3-20260828"
        tar_path = INTERNAL / "dist" / f"{name}.tar.gz"
        zip_path = INTERNAL / "dist" / f"{name}.zip"
        if not tar_path.is_file() or not zip_path.is_file():
            self.skipTest("最终归档尚未构建")
        for archive in (tar_path, zip_path):
            sidecar = archive.with_suffix(archive.suffix + ".sha256")
            self.assertTrue(sidecar.is_file())
            self.assertEqual(sidecar.read_text(encoding="ascii").split()[0], digest(archive))

        with tarfile.open(tar_path, "r:gz") as archive:
            names = set(archive.getnames())
            self.assertIn(f"{name}/install.sh", names)
            self.assertIn(f"{name}/quick_install.sh", names)
            self.assertIn(f"{name}/scripts/robot_local_navigation_v3.sh", names)
            self.assertIn(f"{name}/source/server/src/index.ts", names)
            self.assertIn(f"{name}/payload/console/dist/public/index.html", names)
            self.assertIn(f"{name}/source/java/src/main/java/com/agibot/aiem/nav/LingxiNavAgentApp.java", names)
            self.assertIn(f"{name}/source/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py", names)
            self.assertTrue(archive.getmember(f"{name}/install.sh").mode & 0o111)
            self.assertTrue(archive.getmember(f"{name}/quick_install.sh").mode & 0o111)
            self.assertTrue(archive.getmember(f"{name}/payload/runtime/node").mode & 0o111)
            temp_parent = Path(tar_path.anchor) if os.name == "nt" else None
            with tempfile.TemporaryDirectory(prefix=".internal-audit-", dir=temp_parent) as temp_name:
                archive.extractall(temp_name, filter="data")
                root = Path(temp_name) / name
                lines = (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
                for line in lines:
                    expected, relative = line.split("  ", 1)
                    self.assertEqual(digest(root / relative), expected, relative)
                subprocess.run(
                    [
                        "python",
                        str(root / "scripts" / "verify_payload.py"),
                        "--root",
                        str(root / "payload"),
                        "--manifest",
                        str(root / "payload-manifest.json"),
                    ],
                    check=True,
                    capture_output=True,
                )
                info = json.loads((root / "release-info.json").read_text(encoding="utf-8"))
                self.assertFalse(info["encrypted"])
                self.assertFalse(info["device_bound"])
                jar = root / "payload" / "java" / "robot-local-navigation-java-all.jar"
                self.assertTrue(zipfile.is_zipfile(jar))
                self.assertEqual(digest(jar), FIELD_JAR_SHA256)
                for executable in (
                    root / "payload" / "runtime" / "node",
                    root / "payload" / "runtime" / "java" / "bin" / "java",
                ):
                    data = executable.read_bytes()[:20]
                    self.assertEqual(data[:4], b"\x7fELF", executable)
                    self.assertEqual(int.from_bytes(data[18:20], "little"), 183, executable)
                file_names = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
                self.assertNotIn("secrets.env", file_names)
                self.assertFalse(any(name.endswith((".pem", ".key")) for name in file_names))
                for script in root.rglob("*.sh"):
                    self.assertNotIn(b"\r", script.read_bytes(), str(script.relative_to(root)))

        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            self.assertIn(f"{name}/install.sh", names)
            self.assertIn(f"{name}/quick_install.sh", names)
            self.assertIn(f"{name}/docs/内部部署与命令手册.md", names)
            mode = archive.getinfo(f"{name}/install.sh").external_attr >> 16
            self.assertTrue(mode & 0o111)

    def test_required_delivery_files_and_field_jar(self) -> None:
        required = (
            "README.md",
            "build_internal_release.py",
            "runtime/install.sh",
            "runtime/configure.sh",
            "runtime/control.sh",
            "runtime/quick_install.sh",
            "docs/内部部署与命令手册.md",
            "docs/新机器人验收清单.md",
            "docs/命令速查表.md",
        )
        for relative in required:
            self.assertTrue((INTERNAL / relative).is_file(), relative)
        self.assertTrue(FIELD_JAR.is_file())
        self.assertEqual(digest(FIELD_JAR), FIELD_JAR_SHA256)
        self.assertEqual(FIELD_JAR.read_bytes()[:4], b"PK\x03\x04")

    def test_shell_scripts_parse(self) -> None:
        scripts = sorted(RUNTIME.rglob("*.sh"))
        self.assertTrue(scripts)
        if os.name == "nt":
            if not shutil.which("wsl.exe"):
                self.skipTest("Windows WSL 不可用")
            command = "set -e\n" + "\n".join(
                f"bash -n {shlex.quote(wsl_path(path))}" for path in scripts
            )
            result = subprocess.run(
                ["wsl.exe", "-u", "root", "--", "bash", "-lc", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        else:
            result = subprocess.run(
                ["bash", "-n", *map(str, scripts)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shell_scripts_are_normalized_to_linux_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "input.sh"
            target = Path(name) / "output.sh"
            source.write_bytes(b"#!/bin/bash\r\nset -e\r\necho ok\r\n")
            from internal_deployment.build_internal_release import copy_file

            copy_file(source, target, 0o755)
            self.assertEqual(target.read_bytes(), b"#!/bin/bash\nset -e\necho ok\n")

    def test_plaintext_runtime_has_no_license_or_embedded_password_dependency(self) -> None:
        checked = [
            INTERNAL / "build_internal_release.py",
            *RUNTIME.rglob("*"),
        ]
        forbidden = (
            b"verify_license.py",
            b"manifest.sig",
            b"device_fingerprint",
            b"ROBOT_SSH_PASSWORD=1",
            b"St87591540",
        )
        for path in checked:
            if not path.is_file():
                continue
            data = path.read_bytes()
            for marker in forbidden:
                self.assertNotIn(marker, data, str(path))

    def test_reboot_safe_voice_and_ros_permissions(self) -> None:
        setup_soc2 = (RUNTIME / "scripts" / "setup_soc2_access.sh").read_text(encoding="utf-8")
        self.assertIn('chown run:run "${KEY}"', setup_soc2)
        self.assertIn('chmod 0600 "${KEY}"', setup_soc2)

        for name in ("navigation_control.sh", "robot_voice_bridge_control.sh"):
            script = (RUNTIME / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("umask 0007", script)

        console_unit = (RUNTIME / "systemd" / "lingxi-nav-console.service").read_text(encoding="utf-8")
        self.assertIn("ExecStartPre=/usr/bin/install -d -m 0770 -o agi -g sparkrobot-nav /var/log/sparkrobot/lingxi-nav/ros", console_unit)

        java_unit = (RUNTIME / "systemd" / "lingxi-java-agent-robot.service").read_text(encoding="utf-8")
        self.assertIn("ExecStartPre=+/usr/bin/chown run:sparkrobot-nav", java_unit)

    def test_payload_verifier_detects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            release = make_release(Path(name) / "release", "1.0.0-test")
            command = [
                "python",
                str(release / "scripts" / "verify_payload.py"),
                "--root",
                str(release / "payload"),
                "--manifest",
                str(release / "payload-manifest.json"),
            ]
            subprocess.run(command, check=True, capture_output=True)
            (release / "payload" / "runtime" / "node").write_bytes(b"tampered")
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("被修改", result.stderr)

    def test_first_install_upgrade_preserves_customer_data_and_rolls_back(self) -> None:
        if os.name != "nt" or not shutil.which("wsl.exe"):
            self.skipTest("隔离生命周期测试需要 Windows WSL")
        with tempfile.TemporaryDirectory() as name:
            temp = Path(name)
            release_v1 = make_release(temp / "release-v1", "1.0.0-lifecycle")
            release_v2 = make_release(temp / "release-v2", "1.0.1-lifecycle")
            secrets = temp / "secrets.env"
            secrets.write_text(
                "LINGXI_APP_ID=APP_TEST\n"
                "LINGXI_APP_KEY=test-app-key\n"
                "LINGXI_APP_SECRET=test-app-secret\n"
                "DOUBAO_API_KEY=test-doubao-key\n"
                "SOC2_HOST=10.0.1.99\n",
                encoding="utf-8",
                newline="\n",
            )
            script = f"""
set -euxo pipefail
root="$(mktemp -d /tmp/sparkrobot-internal-test.XXXXXX)"
trap 'rm -rf -- "$root"' EXIT
export SPARKROBOT_INSTALL_ROOT="$root"
export SPARKROBOT_INSTALL_TEST_MODE=true
export SPARKROBOT_ALLOW_NON_ARM64=true
mkdir -p "$root/home/agi/robot_local_navigation_v3/maps"
printf '%s\n' '{{"map":"legacy-map"}}' > "$root/home/agi/robot_local_navigation_v3/maps/legacy-map.json"
cat > "$root/home/agi/robot_local_navigation_v3/unified_nav_config.json" <<'JSON'
{{"schema_version":2,"kind":"robot_local_nav.unified_config","app":{{"doubao_api_key":"legacy"}},"navigation":{{"default_max_forward_speed":0.22}},"maps":[{{"name":"legacy-map"}}],"points":[{{"id":"p1","name":"展厅"}}],"exports":{{"tour_tasks":[{{"name":"客户路线"}}]}}}}
JSON
cp -a {shlex.quote(wsl_path(release_v1))} "$root/release-v1"
cp -a {shlex.quote(wsl_path(release_v2))} "$root/release-v2"
bash "$root/release-v1/install.sh" --secrets {shlex.quote(wsl_path(secrets))}
test "$(basename "$(readlink -f "$root/opt/sparkrobot/lingxi-nav/current")")" = 1.0.0-lifecycle
test -f "$root/var/lib/sparkrobot/lingxi-nav/maps/legacy-map.json"
grep -Fx 'SOC2_HOST=10.0.1.99' "$root/etc/sparkrobot/lingxi-nav/runtime.env"
python3 - "$root/var/lib/sparkrobot/lingxi-nav/unified_nav_config.json" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]); d = json.loads(p.read_text(encoding='utf-8'))
assert d['points'][0]['name'] == '展厅'
assert d['exports']['tour_tasks'][0]['name'] == '客户路线'
d['exports']['tour_tasks'].append({{'name': '升级保留任务'}})
p.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
PY
bash "$root/release-v2/install.sh"
test "$(basename "$(readlink -f "$root/opt/sparkrobot/lingxi-nav/current")")" = 1.0.1-lifecycle
python3 - "$root/var/lib/sparkrobot/lingxi-nav/unified_nav_config.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
assert d['exports']['tour_tasks'][-1]['name'] == '升级保留任务'
assert d['navigation']['default_max_forward_speed'] == 0.22
PY
bash "$root/usr/local/sbin/sparkrobot-lingxi-internal" verify
bash "$root/usr/local/sbin/sparkrobot-lingxi-internal" rollback
test "$(basename "$(readlink -f "$root/opt/sparkrobot/lingxi-nav/current")")" = 1.0.0-lifecycle
test -f "$root/var/lib/sparkrobot/lingxi-nav/maps/legacy-map.json"
"""
            script_path = temp / "lifecycle.sh"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            result = subprocess.run(
                ["wsl.exe", "-u", "root", "--", "bash", wsl_path(script_path)],
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
