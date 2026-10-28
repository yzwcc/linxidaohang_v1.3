"""Isolated replay contracts using synthetic inputs, not calibration claims."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import zipfile


ROOT = Path(__file__).parents[2]
try:
    import numpy as np
except ModuleNotFoundError:
    np = None

if np is not None and sys.platform == "linux":
    spec = importlib.util.spec_from_file_location("run_glim_offline_trial", ROOT / "tools/run_glim_offline_trial.py")
    replay = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(replay)


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy environment")
class GlimReplay(unittest.TestCase):
    def inputs(self):
        transform = [0.01, 0.02, 0.03, 0, 0, 0, 1]
        profile = {
            "purpose": "offline_glim_trial", "inputClassification": "target_sensor_profile",
            "reviewedForOfflineTrial": True, "timingHypothesesOnlyNotCalibration": True,
            "accelerationScale": 1.0, "angularVelocityScale": 1.0,
            "imuTimeOffsetSeconds": 0.0, "cloudTimeOffsetSeconds": 0.0, "T_lidar_imu": transform,
        }
        diagnostic = {
            "purpose": "recorded_sensor_semantics_diagnostic_not_calibration",
            "datasets": {name: {"staticExtrinsicObservationNotCalibration": {"T_lidar_imu": transform.copy()}}
                         for name in ("static", "dynamic")},
            "boundaries": {"navigationValidated": False, "robotCalibrationValidated": False},
        }
        return profile, diagnostic

    def test_both_explicit_hypotheses_are_diagnostic_only(self):
        profile, diagnostic = self.inputs()
        for hypothesis in replay.HYPOTHESES:
            replay.explicit_diagnostic_profile(profile, diagnostic, hypothesis)
        with self.assertRaisesRegex(ValueError, "explicit_scan_time"):
            replay.explicit_diagnostic_profile(profile, diagnostic, "automatic")

    def test_unreviewed_scaled_or_corrected_inputs_are_not_silently_accepted(self):
        for name, value in (("reviewedForOfflineTrial", False),
                            ("timingHypothesesOnlyNotCalibration", False),
                            ("angularVelocityScale", 0.0174533), ("accelerationScale", None),
                            ("cloudTimeOffsetSeconds", -0.1), ("imuTimeOffsetSeconds", 0.1)):
            profile, diagnostic = self.inputs()
            profile[name] = value
            with self.assertRaisesRegex(ValueError, "explicit_reviewed"):
                replay.explicit_diagnostic_profile(profile, diagnostic, replay.HYPOTHESES[0])

    def test_extrinsic_must_match_both_static_and_dynamic_recordings(self):
        profile, diagnostic = self.inputs()
        diagnostic["datasets"]["dynamic"]["staticExtrinsicObservationNotCalibration"]["T_lidar_imu"][0] += 1
        with self.assertRaisesRegex(ValueError, "both_recordings"):
            replay.explicit_diagnostic_profile(profile, diagnostic, replay.HYPOTHESES[0])

    def test_diagnostic_must_not_claim_calibration_acceptance(self):
        profile, diagnostic = self.inputs()
        diagnostic["boundaries"]["robotCalibrationValidated"] = True
        with self.assertRaisesRegex(ValueError, "unapproved_aggregate"):
            replay.explicit_diagnostic_profile(profile, diagnostic, replay.HYPOTHESES[0])

    def native_library(self, status):
        def frame(stamp, count, points, offsets, destination, statistics):
            for index, value in enumerate(np.eye(4).ravel()):
                destination[index] = value
            for index, value in enumerate([1.0, 2.0, 100.0, 0.0]):
                statistics[index] = value
            return status
        return NS(lingxi_error=Mock(return_value=b"synthetic_native_error"),
                  lingxi_open=Mock(return_value=0), lingxi_imu=Mock(return_value=0),
                  lingxi_frame=Mock(side_effect=frame), lingxi_close=Mock(return_value=5))

    def test_initializer_does_not_fabricate_a_pose_and_success_is_checked(self):
        for status in (0, 1):
            library = self.native_library(status)
            with patch.object(replay.ctypes, "CDLL", return_value=library):
                native = replay.Native("synthetic.so", "synthetic-config", "synthetic-module")
                pose, statistics = native.frame(1000, np.ones((12, 3)), np.linspace(0, 0.1, 12))
                self.assertEqual(status == 0, pose is None)
                if pose is not None:
                    np.testing.assert_array_equal(np.eye(4), pose)
                np.testing.assert_array_equal([1, 2, 100, 0], statistics)
                self.assertEqual(5, native.close())

    def test_native_error_remains_an_error_not_a_missing_pose(self):
        with patch.object(replay.ctypes, "CDLL", return_value=self.native_library(-1)):
            native = replay.Native("synthetic.so", "synthetic-config", "synthetic-module")
            with self.assertRaisesRegex(RuntimeError, "synthetic_native_error"):
                native.frame(1000, np.ones((12, 3)), np.linspace(0, 0.1, 12))

    def test_build_environment_does_not_inherit_ros_or_other_library_paths(self):
        with patch.dict(replay.os.environ, {"ROS_DISTRO": "synthetic", "LD_LIBRARY_PATH": "/unrelated"}):
            env = replay.library_environment(Path("/synthetic"))
        self.assertNotIn("ROS_DISTRO", env)
        self.assertNotIn("/unrelated", env["LD_LIBRARY_PATH"])
        self.assertEqual("2", env["OMP_NUM_THREADS"])


class GlimReplayBoundary(unittest.TestCase):
    def test_replay_does_not_import_ros_or_robot_network_apis(self):
        tree = ast.parse((ROOT / "tools/run_glim_offline_trial.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module]
                for name in names:
                    self.assertNotIn(name.split(".")[0], {"rclpy", "rclcpp", "socket", "requests", "paramiko"})
        native = (ROOT / "tools/glim-replay/offline.cpp").read_text()
        for token in ("rclcpp/", "rcl/", "McLocomotion", "cmd_vel", "create_publisher"):
            self.assertNotIn(token, native)

    def exporter(self):
        spec = importlib.util.spec_from_file_location(
            "export_glim_trial_evidence", ROOT / "tools/export_glim_trial_evidence.py",
        )
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        return exporter

    def test_sdk_evidence_checks_actual_member_content(self):
        exporter = self.exporter()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "synthetic-sdk-not-a-real-vendor-package.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("synthetic/src/examples/src/hal/echo_imu_data.cpp",
                                "angular_velocity (rad/s) linear_accel (m/s^2) "
                                "/aima/hal/sensor/lidar_chest_front/imu")
                source.writestr("synthetic/docs/cn/_build/html/zh-cn/dev/Interface/hal/sensor.html",
                                "PointXYZIRT lidar_imu_chest_front "
                                "\u8ddd\u79bb\u5355\u4f4d : \u7c73")
            evidence = exporter.sdk_evidence(archive)
            self.assertEqual("metres", evidence["cloudDistanceUnitDeclared"])
            self.assertTrue(evidence["declarationsAreNotMeasuredCalibration"])
            self.assertFalse(evidence["scanHeaderReferenceSpecifiedInReviewedMaterial"])

    def test_wrong_sdk_example_cannot_be_replaced_by_an_assumed_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "synthetic.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("synthetic/src/examples/src/hal/echo_imu_data.cpp", "no units")
            with self.assertRaisesRegex(ValueError, "no_longer_matches_review"):
                self.exporter().sdk_evidence(archive)

    def test_export_contract_contains_only_aggregate_fields(self):
        exporter = self.exporter()
        self.assertNotIn("poses", exporter.FIELDS)
        self.assertNotIn("header_stamps_ns", exporter.FIELDS)
        self.assertNotIn("T_lidar_imu", exporter.FIELDS)
        self.assertEqual(5, len(exporter.RUNS))
        self.assertIn("initialStaticStart", exporter.RUNS)

    def test_recorded_evidence_keeps_all_hypotheses_and_does_not_claim_acceptance(self):
        evidence = json.loads((ROOT / "docs/slam-glim-offline-evidence.json").read_text())
        self.assertEqual(set(self.exporter().RUNS), set(evidence["runs"]))
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        for run in evidence["runs"].values():
            self.assertEqual(run["recordedScans"], run["processedScans"] + run["skippedUnbracketedScans"])
            self.assertEqual(run["processedScans"], run["initializationScans"] + run["estimatedPoses"])
            self.assertEqual("online_lidar_poses_not_final_smoothed_or_loop_optimized", run["trajectoryKind"])
        for dataset in evidence["sensorAggregates"].values():
            self.assertEqual(dataset["identityImuOrientationSamples"], dataset["imuOrientationSamples"])


if __name__ == "__main__":
    unittest.main()
