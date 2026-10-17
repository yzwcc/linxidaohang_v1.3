"""Exercise installer decisions in temporary roots, never the real AIMDK."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
BUNDLE = Path(os.environ.get("R15_BUNDLE", HERE / "bundle"))


@unittest.skipUnless(os.name == "posix", "requires Bash")
class AimdkInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="r15-aimdk-")
        self.root = Path(self.temp.name)
        self.agi = self.root / "agi"
        self.agi.mkdir()
        self.ros = self.root / "ros"
        self.ros.mkdir()
        (self.ros / "setup.bash").write_text(":\n")
        self.modules = self.root / "modules"
        (self.modules / "aimdk_msgs").mkdir(parents=True)
        (self.modules / "rclpy.py").touch()
        (self.modules / "aimdk_msgs/__init__.py").touch()
        (self.modules / "aimdk_msgs/msg.py").touch()

    def rewrite(self, relative):
        text = (BUNDLE / relative).read_text()
        text = text.replace("/home/agi", str(self.agi)).replace("/opt/ros/humble", str(self.ros))
        path = self.root / Path(relative).name
        path.write_text(text)
        return path

    def test_generic_aimdk_import_is_not_enough_for_navigation_and_motion(self):
        install = self.agi / "aimdk/install"
        example = install / "examples/lib/examples/navigation"
        example.parent.mkdir(parents=True)
        example.touch(); example.chmod(0o755)
        (install / "setup.bash").write_text(f'export PYTHONPATH="{self.modules}"\n')
        script = self.rewrite("install_all.sh")
        harness = '''source "$1" --help >/dev/null
sudo() { shift 2; "$@"; }
verify_aimdk_runtime
'''
        result = subprocess.run(["bash", "-c", harness, "qa", str(script)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "missing mandatory message/service types must fail preflight")

    def test_required_aimdk_interfaces_pass_preflight(self):
        for name, types in {
            "msg": "MessageHeader PncTaskRequest TtsPriorityLevel CommonState McActionCommand McCommonState McControlArea McFsmState McInputAction McPresetMotion RequestHeader",
            "srv": "PlayTts PlayEmoji ExecuteActionResource SetMcAction SetMcInputSource SetMcPresetMotion",
        }.items():
            (self.modules / f"aimdk_msgs/{name}.py").write_text("\n".join(f"class {value}: pass" for value in types.split()))
        install = self.agi / "aimdk/install"
        example = install / "examples/lib/examples/navigation"
        example.parent.mkdir(parents=True)
        example.touch(); example.chmod(0o755)
        (install / "setup.bash").write_text(f'export PYTHONPATH="{self.modules}"\n')
        script = self.rewrite("install_all.sh")
        result = subprocess.run(["bash", "-c", 'source "$1" --help >/dev/null; sudo() { shift 2; "$@"; }; verify_aimdk_runtime',
                                 "qa", str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failed_aimdk_switch_restores_original_installation(self):
        old = self.agi / "aimdk"
        old.mkdir()
        marker = old / "original-customer-runtime"
        marker.write_text("must-survive")
        (self.agi / "aimdk-aarch64-1bde262f-artifacts.7z").touch()
        script = self.rewrite("scripts/01-replace-aimdk.sh")
        harness = '''id() { echo agi; }
sha256sum() { echo "f13147a175bc3fb621fb118698aae0353c097adda38bba640f9a39c01433cc98  archive"; }
7z() {
  mkdir -p "$NEW_ROOT/src" "$NEW_ROOT/install/examples/lib/examples"
  echo test-version >"$NEW_ROOT/version"
  printf 'export PYTHONPATH="%s"\\n' "$QA_MODULES" >"$NEW_ROOT/install/setup.bash"
  touch "$NEW_ROOT/install/examples/lib/examples/navigation"
  chmod +x "$NEW_ROOT/install/examples/lib/examples/navigation"
}
colcon() { :; }
ros2() { echo aimdk_msgs; }
mv() {
  [[ "$1" == -- ]] && shift
  if [[ "$1" == "$NEW_ROOT" ]]; then echo SIMULATED_SWITCH_FAILURE >&2; return 23; fi
  command mv -- "$@"
}
source "$1"
'''
        result = subprocess.run(["bash", "-c", harness, "qa", str(script)],
                                env=os.environ | {"QA_MODULES": str(self.modules)}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "simulated replacement must fail")
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("SIMULATED_SWITCH_FAILURE", result.stderr)
        self.assertTrue(marker.exists(), "failed replacement must keep the previously usable AIMDK")
        self.assertEqual(marker.read_text(), "must-survive")

    def tearDown(self):
        self.temp.cleanup()


if __name__ == "__main__":
    unittest.main()
