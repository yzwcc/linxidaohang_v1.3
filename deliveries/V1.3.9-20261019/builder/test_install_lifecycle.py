"""Run the release's actual installers in a temporary Linux root, without services."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def make_release(root, version):
    root.mkdir()
    for name in ("install.sh", "configure.sh", "control.sh", "quick_install.sh"):
        shutil.copy2(HERE / name, root / name)
    for name in ("scripts", "templates", "systemd"):
        shutil.copytree(HERE / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    files = {
        "console/dist/index.js": b"console.log('fixture');\n",
        "runtime/node": b"fixture-node\n",
        "runtime/java/bin/java": b"fixture-java\n",
        "runtime/java/bin/keytool": b"fixture-keytool\n",
        "runtime/java/lib/jspawnhelper": b"fixture-helper\n",
        "navigation/robot_local_navigation_v3/lingxi_nav_bridge.py": b"pass\n",
        "java/robot-local-navigation-java-all.jar": b"fixture-jar\n",
    }
    entries = []
    for relative, data in files.items():
        path = root / "payload" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entries.append({"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    (root / "payload-manifest.json").write_text(json.dumps({"format": "sparkrobot-internal-payload-v1", "files": entries}))
    (root / "release-info.json").write_text(json.dumps({"version": version}))
    return root


@unittest.skipIf(os.name == "nt", "Run with Linux/WSL root for real symlink and permission behavior")
class InstallerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.geteuid(), 0, "The isolated installer requires Linux root")
        self.temp = tempfile.TemporaryDirectory(prefix="lingxi-r4-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "root"
        self.root.mkdir()
        self.env = dict(os.environ, SPARKROBOT_INSTALL_ROOT=str(self.root),
                        SPARKROBOT_INSTALL_TEST_MODE="true", SPARKROBOT_ALLOW_NON_ARM64="true")
        self.v1 = make_release(Path(self.temp.name) / "v1", "1.0.21-fixture")
        self.v2 = make_release(Path(self.temp.name) / "v2", "1.0.22-fixture")
        self.secrets = Path(self.temp.name) / "fixture.env"
        self.secrets.write_text(
            "LINGXI_APP_ID=TEST_ONLY\nLINGXI_APP_KEY=fixture-key\n"
            "LINGXI_APP_SECRET=fixture-secret\nDOUBAO_API_KEY=fixture-api\nSOC2_HOST=10.0.1.99\n")
        self.base = self.root / "opt/sparkrobot/lingxi-nav"
        self.state = self.root / "var/lib/sparkrobot/lingxi-nav"
        self.config = self.root / "etc/sparkrobot/lingxi-nav"

    def run_script(self, script, *args, ok=True):
        result = subprocess.run(["bash", str(script), *map(str, args)], env=self.env,
                                capture_output=True, text=True, timeout=60)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, "Unexpected installer success")
        return result

    def test_install_upgrade_reinstall_verify_and_rollback(self):
        old = self.root / "home/agi/robot_local_navigation_v3"
        (old / "maps").mkdir(parents=True)
        (old / "maps/customer.json").write_text('{"loop_count":3,"customer":"fixture"}')
        previous = {"app": {"doubao_api_key": "old"}, "navigation": {"default_max_forward_speed": 0.22},
                    "maps": [{"name": "customer"}], "points": [{"id": "p1"}],
                    "exports": {"tour_tasks": [{"name": "customer-task", "loop_count": 3}]}}
        (old / "unified_nav_config.json").write_text(json.dumps(previous))
        self.run_script(self.v1 / "install.sh", "--secrets", self.secrets)
        self.assertEqual((self.base / "current").resolve().name, "1.0.21-fixture")
        console_before = (self.config / "console.env").read_bytes()
        props_before = (self.config / "lingxi-nav.properties").read_bytes()
        duplicate = self.run_script(self.v1 / "install.sh", ok=False)
        self.assertIn("--reinstall", duplicate.stderr)
        self.assertEqual(console_before, (self.config / "console.env").read_bytes())
        (self.config / "lingxi-nav.properties").chmod(0o600)
        self.run_script(self.v2 / "install.sh")
        self.assertEqual((self.base / "current").resolve().name, "1.0.22-fixture")
        self.assertEqual(props_before, (self.config / "lingxi-nav.properties").read_bytes())
        self.assertEqual((self.config / "lingxi-nav.properties").stat().st_mode & 0o777, 0o640)
        self.assertIn("SOC2_HOST=10.0.1.99", (self.config / "runtime.env").read_text())
        config = json.loads((self.state / "unified_nav_config.json").read_text())
        for key in ("maps", "points", "exports"):
            self.assertEqual(config[key], previous[key])
        self.assertEqual(config["navigation"]["default_max_forward_speed"], 0.22)
        self.assertIs(config["navigation"]["dense_crowd"]["enabled"], True)
        self.assertEqual(config["navigation"]["dense_crowd"]["max_forward_speed"], 0.3)
        self.assertEqual(config["navigation"]["task_failure_policy"]["max_retries_per_step"], 1)
        self.assertEqual((self.state / "maps/customer.json").read_text(), '{"loop_count":3,"customer":"fixture"}')
        self.run_script(self.v2 / "install.sh", "--reinstall")
        self.assertEqual(len(list((self.base / "reinstall-backups").iterdir())), 1)
        control = self.root / "usr/local/sbin/sparkrobot-lingxi-internal"
        self.run_script(control, "verify")
        (self.base / "current/runtime/node").write_text("tampered")
        self.run_script(control, "verify", ok=False)
        self.run_script(control, "rollback")
        self.assertEqual((self.base / "current").resolve().name, "1.0.21-fixture")
        self.run_script(control, "verify")
        self.assertEqual(json.loads((self.state / "unified_nav_config.json").read_text())["exports"], previous["exports"])

    def test_payload_tamper_rejected_before_creating_installation(self):
        (self.v1 / "payload/runtime/node").write_text("tampered")
        self.run_script(self.v1 / "install.sh", "--secrets", self.secrets, ok=False)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_every_release_shell_script_parses(self):
        scripts = sorted(HERE.rglob("*.sh"))
        self.assertTrue(scripts)
        for path in scripts:
            result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, str(path) + result.stderr)


if __name__ == "__main__":
    unittest.main()
