"""Installed-interpreter contracts with synthetic imports, never robot I/O."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check_installed_slam_python as probe


class InstalledPythonTests(unittest.TestCase):
    @contextmanager
    def target(self):
        with patch.object(probe.sys, "platform", "linux"), \
                patch.object(probe.platform, "machine", return_value="aarch64"), \
                patch.object(probe.sys, "version_info", (3, 10, 12)), \
                patch.object(probe.os, "geteuid", return_value=1001, create=True):
            yield

    @contextmanager
    def selected(self, root):
        prefix = root / "env"
        (prefix / "bin").mkdir(parents=True)
        executable = prefix / "bin/python"
        executable.write_text("synthetic executable, never executed")
        executable.chmod(0o755)
        (prefix / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
        with patch.object(probe.sys, "prefix", str(prefix)), \
                patch.object(probe.sys, "executable", str(executable)), \
                patch.object(probe.sys, "base_prefix", str(root / "system")), \
                patch.object(probe.sys, "flags", SimpleNamespace(no_user_site=1)), \
                patch.object(probe.site, "ENABLE_USER_SITE", False):
            yield prefix, executable

    def test_explicit_venv_selection_and_user_site_disabled(self):
        with tempfile.TemporaryDirectory() as directory, self.selected(Path(directory).resolve()) as (prefix, executable):
            self.assertEqual(prefix, probe.selection(str(executable)))
            for selected in ("python", str(executable.parent / "missing"), str(executable) + "/../python"):
                with self.subTest(selected=selected), self.assertRaises((ValueError, OSError)):
                    probe.selection(selected)
            with patch.object(probe.sys, "base_prefix", str(prefix)), self.assertRaises(ValueError):
                probe.selection(str(executable))
            with patch.object(probe.site, "ENABLE_USER_SITE", True), self.assertRaises(ValueError):
                probe.selection(str(executable))
            with patch.object(probe.sys, "flags", SimpleNamespace(no_user_site=0)), self.assertRaises(ValueError):
                probe.selection(str(executable))
            (prefix / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
            with self.assertRaises(ValueError):
                probe.selection(str(executable))

    @unittest.skipUnless(sys.platform == "linux", "POSIX interpreter symlinks")
    def test_venv_interpreter_symlink_allowed_but_parent_alias_rejected(self):
        with tempfile.TemporaryDirectory() as directory, self.selected(Path(directory).resolve()) as (prefix, executable):
            base = prefix.parent / "base-python"
            base.write_bytes(executable.read_bytes())
            base.chmod(0o755)
            executable.unlink()
            executable.symlink_to(base)
            self.assertEqual(prefix, probe.selection(str(executable)))
            alias = prefix.parent / "alias"
            alias.symlink_to(prefix, target_is_directory=True)
            with patch.object(probe.sys, "prefix", str(alias)), \
                    patch.object(probe.sys, "executable", str(alias / "bin/python")), self.assertRaises(ValueError):
                probe.selection(str(alias / "bin/python"))

    def imports(self, root):
        prefix = root / "env"
        prefix.mkdir()
        ros = root / "ros"
        ros.mkdir()
        loaded, distributions = {}, {}
        for name, module in probe.MODULES.items():
            file = prefix / (module + ".py")
            file.touch()
            loaded[module] = SimpleNamespace(__file__=str(file))
            distributions[name] = SimpleNamespace(version=probe.PINNED[name], locate_file=lambda _: prefix)
        for module in ("scipy.spatial", "scipy.spatial.transform", "rclpy", "tf2_ros"):
            file = ros / (module + ".py")
            file.touch()
            loaded[module] = SimpleNamespace(__file__=str(file))
        return prefix, ros, loaded, distributions

    def test_locked_imports_and_ros_pass_without_readiness_promotion(self):
        with tempfile.TemporaryDirectory() as directory, self.target():
            prefix, ros, loaded, distributions = self.imports(Path(directory).resolve())
            with patch.object(probe, "selection", return_value=prefix), \
                    patch.object(probe, "ROS_ROOT", ros), \
                    patch.object(probe, "import_module", side_effect=loaded.__getitem__) as imports, \
                    patch.object(probe.metadata, "distribution", side_effect=distributions.__getitem__):
                report = probe.check(str(prefix / "bin/python"))
            self.assertEqual(probe.PINNED, report["versions"])
            self.assertEqual("installed_slam_python_preflight_passed", report["result"])
            for name in ("navigationReady", "runtimeCompatibilityAccepted", "rosNodesCreated",
                         "sensorsSubscribed", "motionCommandsSent", "systemSitePackages", "userSiteEnabled"):
                self.assertFalse(report[name])
            self.assertEqual(9, imports.call_count)

    def test_mixed_modules_metadata_versions_ros_and_warnings_rejected(self):
        for change in ("module", "metadata", "version", "ros", "warning", "native-error"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory, self.target():
                root = Path(directory).resolve()
                prefix, ros, loaded, distributions = self.imports(root)
                if change == "module":
                    loaded["numpy"].__file__ = loaded["rclpy"].__file__
                elif change == "metadata":
                    distributions["numpy"].locate_file = lambda _: ros
                elif change == "version":
                    distributions["numpy"].version = "1.26.1"
                elif change == "ros":
                    loaded["rclpy"].__file__ = loaded["numpy"].__file__

                def imported(name):
                    if name == "small_gicp" and change == "warning":
                        warnings.warn("synthetic compatibility warning")
                    if name == "scipy.spatial" and change == "native-error":
                        raise ImportError("synthetic native import failure")
                    return loaded[name]

                with patch.object(probe, "selection", return_value=prefix), \
                        patch.object(probe, "ROS_ROOT", ros), \
                        patch.object(probe, "import_module", side_effect=imported), \
                        patch.object(probe.metadata, "distribution", side_effect=distributions.__getitem__), \
                        self.assertRaises((ValueError, ImportError)):
                    probe.check(str(prefix / "bin/python"))

    def test_wrong_platform_version_and_root_rejected_before_imports(self):
        for attribute, value in (("platform", "win32"), ("version_info", (3, 12))):
            with self.subTest(attribute=attribute), self.target(), \
                    patch.object(probe.sys, attribute, value), \
                    patch.object(probe, "import_module") as imported, self.assertRaises(ValueError):
                probe.check("/synthetic/env/bin/python")
            imported.assert_not_called()
        with self.target(), patch.object(probe.os, "geteuid", return_value=0), \
                patch.object(probe, "selection") as selected, self.assertRaises(ValueError):
            probe.check("/synthetic/env/bin/python")
        selected.assert_not_called()
        with self.target(), patch.object(probe.platform, "machine", return_value="x86_64"), self.assertRaises(ValueError):
            probe.check("/synthetic/env/bin/python")

    @unittest.skipIf(sys.platform == "linux" and os.uname().machine == "aarch64", "not a target-host refusal")
    def test_cli_on_non_target_returns_configuration_status_without_report(self):
        result = subprocess.run([sys.executable, "-s", "-B", str(ROOT / "tools/check_installed_slam_python.py"),
                                 "--python", sys.executable], text=True, capture_output=True)
        self.assertEqual(78, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertIn("isolated ARM64 3.10 venv", result.stderr)


if __name__ == "__main__":
    unittest.main()
