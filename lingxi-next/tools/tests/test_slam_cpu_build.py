"""Build-plan checks; these do not substitute for running a real compiler."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("build_slam_cpu_trial", ROOT / "tools/build_slam_cpu_trial.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class CpuBuildPlan(unittest.TestCase):
    def test_cpu_libraries_are_built_in_dependency_order(self):
        plan = build.commands(Path("/source"), Path("/trial"), 2)
        self.assertEqual(
            [name + suffix for name in build.PINS for suffix in ("_configure", "_build", "_install")],
            [stage for stage, _ in plan],
        )
        self.assertEqual(9, len(plan))

    def test_every_install_uses_the_explicit_user_prefix_without_sudo(self):
        plan = build.commands(Path("/source"), Path("/trial"), 1)
        for stage, command in plan:
            self.assertNotIn("sudo", command)
            self.assertNotIn("apt-get", command)
            self.assertNotIn("ros2", command)
            if stage.endswith("_configure"):
                self.assertIn("-DCMAKE_INSTALL_PREFIX=" + str(Path("/trial") / "prefix"), command)
                self.assertIn("-DCMAKE_CXX_FLAGS_RELEASE=-O1 -DNDEBUG", command)

    def test_required_unstable_library_is_enabled_and_cuda_viewers_are_off(self):
        stages = dict(build.commands(Path("/source"), Path("/trial"), 1))
        self.assertIn("-DGTSAM_BUILD_UNSTABLE=ON", stages["gtsam_configure"])
        self.assertIn("-DGTSAM_WITH_TBB=OFF", stages["gtsam_configure"])
        for name in ("gtsam_points", "glim"):
            self.assertIn("-DBUILD_WITH_CUDA=OFF", stages[name + "_configure"])
            self.assertIn("-DBUILD_WITH_MARCH_NATIVE=OFF", stages[name + "_configure"])
        self.assertIn("-DBUILD_WITH_VIEWER=OFF", stages["glim_configure"])
        self.assertIn("-DBUILD_WITH_OPENCV=OFF", stages["glim_configure"])

    def test_build_concurrency_has_a_bounded_explicit_value(self):
        for value in (0, 5, True, 1.5, None):
            with self.assertRaises(ValueError):
                build.commands(Path("/source"), Path("/trial"), value)

    def test_wrong_source_revision_is_rejected(self):
        with patch.object(build.subprocess, "check_output", return_value="0" * 40):
            with self.assertRaisesRegex(ValueError, "source_revision"):
                build.verify_sources(Path("unused"))

    def test_dirty_sources_are_rejected(self):
        with patch.object(
            build.subprocess, "check_output",
            side_effect=[build.PINS["gtsam"], " M CMakeLists.txt\n"],
        ):
            with self.assertRaisesRegex(ValueError, "dirty_upstream"):
                build.verify_sources(Path("unused"))

    def test_non_linux_execution_is_rejected_before_writing(self):
        with patch.object(build.platform, "system", return_value="Windows"):
            with self.assertRaisesRegex(ValueError, "linux_build"):
                build.run(Path("unused"), Path("unused"), Path("unused"), 1)

    def test_existing_work_is_never_removed_or_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            marker = work / "existing.txt"
            marker.write_text("preserve")
            with patch.object(build.platform, "system", return_value="Linux"):
                with self.assertRaisesRegex(ValueError, "existing_results_are_preserved"):
                    build.run(work / "sources", work / "packages", work, 1)
            self.assertEqual("preserve", marker.read_text())

    def test_work_inside_an_input_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with patch.object(build.platform, "system", return_value="Linux"):
                with self.assertRaisesRegex(ValueError, "outside_input_caches"):
                    build.run(cache, cache, cache / "new-work", 1)
            self.assertFalse((cache / "new-work").exists())

    def test_package_inventory_records_exact_bytes_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            data = b"SYNTHETIC ARCHIVE METADATA FIXTURE, NOT A REAL PACKAGE"
            (cache / "fixture.deb").write_bytes(data)
            with patch.object(build.subprocess, "check_output", return_value="fixture\t1\tamd64"):
                result = build.package_inventory(cache)
            self.assertEqual(1, len(result))
            self.assertEqual("fixture", result[0]["package"])
            self.assertEqual("1", result[0]["version"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), result[0]["sha256"])

    def test_empty_package_cache_is_not_a_successful_prerequisite_check(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "no_downloaded_packages"):
                build.package_inventory(Path(directory))

    def test_multiple_versions_of_one_package_cannot_silently_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / "first.deb").write_bytes(b"SYNTHETIC")
            (cache / "second.deb").write_bytes(b"SYNTHETIC")
            with patch.object(build.subprocess, "check_output",
                              side_effect=["fixture\t1\tamd64", "fixture\t2\tamd64"]):
                with self.assertRaisesRegex(ValueError, "duplicate_package"):
                    build.package_inventory(cache)

    def test_wrong_package_architecture_creates_no_work_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(build.platform, "system", return_value="Linux"), \
                    patch.object(build, "verify_sources"), \
                    patch.object(build, "package_inventory", return_value=[{"architecture": "arm64"}]), \
                    patch.object(build.subprocess, "check_output", return_value="amd64"):
                with self.assertRaisesRegex(ValueError, "architecture"):
                    build.run(root / "sources", root / "packages", root / "work", 1)
            self.assertFalse((root / "work").exists())

    def test_failed_configure_is_recorded_without_claiming_robot_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = {"file": "synthetic.deb", "architecture": "amd64"}
            with patch.object(build.platform, "system", return_value="Linux"), \
                    patch.object(build.platform, "freedesktop_os_release", return_value={"ID": "fixture"}), \
                    patch.object(build, "verify_sources"), \
                    patch.object(build, "package_inventory", return_value=[package]), \
                    patch.object(build.subprocess, "check_output",
                                 side_effect=["amd64", "x86_64-linux-gnu"]), \
                    patch.object(build.subprocess, "run",
                                 side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)]), \
                    patch.dict(build.os.environ, {"ROS_VERSION": "2", "ROS_DISTRO": "fixture"}), \
                    patch("builtins.print"):
                with self.assertRaisesRegex(RuntimeError, "gtsam_configure_failed"):
                    build.run(root / "sources", root / "packages", root / "work", 1)
                environment = build.subprocess.run.call_args.kwargs["env"]
                self.assertNotIn("ROS_VERSION", environment)
                self.assertNotIn("ROS_DISTRO", environment)
            report = json.loads((root / "work" / "build-report.json").read_text())
            self.assertEqual("failed_or_interrupted", report["result"])
            self.assertEqual("failed", report["stages"][0]["result"])
            self.assertEqual(1, report["stages"][0]["exitCode"])
            for key in ("systemPackagesInstalled", "targetArm64Validated",
                        "rosWrapperValidated", "robotCalibrationValidated", "navigationValidated"):
                self.assertFalse(report[key])


if __name__ == "__main__":
    unittest.main()
