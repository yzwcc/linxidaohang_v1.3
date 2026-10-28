"""Synthetic fields and poses, never robot coordinates or fabricated benchmarks."""
import copy
import ast
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
try:
    import numpy as np
except ModuleNotFoundError:
    np = None

if np is not None and sys.platform == "linux":
    spec = importlib.util.spec_from_file_location("run_kiss_offline_trial", ROOT / "tools/run_kiss_offline_trial.py")
    replay = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(replay)


@unittest.skipIf(np is None or sys.platform != "linux", "run in the isolated Linux NumPy diagnostic environment")
class OfflineKissInputs(unittest.TestCase):
    def cloud(self, *, big=False, organized=False):
        width, height = (6, 2) if organized else (12, 1)
        row_step = width * 32 + (16 if organized else 0)
        data = bytearray(height * row_step)
        for index in range(12):
            row, col = divmod(index, width)
            offset = row * row_step + col * 32
            struct.pack_into((">" if big else "<") + "fff", data, offset, index + 1, 2, 3)
            struct.pack_into((">" if big else "<") + "d", data, offset + 24, index * 1_000_000)
        return NS(
            width=width, height=height, point_step=32, row_step=row_step,
            is_bigendian=big, data=bytes(data),
            fields=[NS(name=name, offset=offset, datatype=datatype, count=1)
                    for name, offset, datatype in (("x", 0, 7), ("y", 4, 7), ("z", 8, 7), ("timestamp", 24, 8))],
        )

    def profile(self):
        return json.loads((ROOT / "docs/kiss-offline-profile.json").read_text())

    def test_little_endian_and_padded_big_endian_have_the_same_values(self):
        for big, organized in ((False, False), (True, False), (False, True), (True, True)):
            points, times, dropped = replay.cloud_arrays(self.cloud(big=big, organized=organized))
            np.testing.assert_array_equal(points[:, 0], np.arange(1, 13))
            np.testing.assert_allclose(times, np.arange(12) * 0.001)
            self.assertEqual(0, dropped)

    def test_coordinate_filter_keeps_point_times_in_the_same_order(self):
        message = self.cloud()
        data = bytearray(message.data)
        struct.pack_into("<f", data, 3 * 32, float("nan"))
        message.data = data
        points, times, dropped = replay.cloud_arrays(message)
        self.assertEqual(1, dropped)
        self.assertEqual(len(points), len(times))
        np.testing.assert_allclose(times, (points[:, 0] - 1) * 0.001)

    def test_missing_or_wrong_time_type_is_not_fabricated(self):
        message = self.cloud()
        message.fields[-1].datatype = 7
        with self.assertRaisesRegex(ValueError, "unsupported_recorded_cloud_schema"):
            replay.cloud_arrays(message)

    def test_nonfinite_or_zero_span_times_are_rejected(self):
        for value in (float("nan"), 0.0, 1e18):
            message = self.cloud()
            data = bytearray(message.data)
            for index in range(12):
                struct.pack_into("<d", data, index * 32 + 24, value)
            message.data = data
            with self.assertRaises(ValueError):
                replay.cloud_arrays(message)

    def test_invalid_dimensions_or_overlapping_fields_are_rejected(self):
        message = self.cloud(organized=True)
        message.row_step = 1
        with self.assertRaisesRegex(ValueError, "invalid_cloud_dimensions"):
            replay.cloud_arrays(message)
        message = self.cloud()
        message.fields[-1].offset = 4
        with self.assertRaisesRegex(ValueError, "overlapping_cloud_fields"):
            replay.cloud_arrays(message)

    def test_profile_is_explicit_and_never_claims_calibration(self):
        parameters = replay.validate_profile(self.profile())
        self.assertEqual(2, parameters["registration"]["max_num_threads"])
        profile = self.profile()
        profile["robotCalibrationValidated"] = True
        with self.assertRaisesRegex(ValueError, "unapproved_offline"):
            replay.validate_profile(profile)

    def test_bad_or_missing_parameter_is_not_replaced_by_defaults(self):
        for change in ("missing", "nan", "boolean", "threads"):
            profile = copy.deepcopy(self.profile())
            if change == "missing":
                del profile["parameters"]["mapping"]["voxel_size"]
            elif change == "nan":
                profile["parameters"]["mapping"]["voxel_size"] = float("nan")
            elif change == "boolean":
                profile["parameters"]["registration"]["max_num_iterations"] = True
            else:
                profile["parameters"]["registration"]["max_num_threads"] = 0
            with self.assertRaises(ValueError):
                replay.validate_profile(profile)

    def test_pose_must_be_a_finite_rigid_transform(self):
        replay.valid_pose(np.eye(4))
        for value in (np.zeros((4, 4)), np.full((4, 4), float("nan")), np.eye(3)):
            with self.assertRaisesRegex(ValueError, "invalid_estimated_pose"):
                replay.valid_pose(value)

    def test_ablated_profile_changes_only_deskew(self):
        profile = self.profile()
        ablation = json.loads((ROOT / "docs/kiss-offline-no-deskew-profile.json").read_text())
        self.assertFalse(ablation["parameters"]["data"]["deskew"])
        ablation["parameters"]["data"]["deskew"] = True
        self.assertEqual(profile, ablation)

    def test_existing_trial_directory_is_preserved(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(replay.metadata, "version", side_effect=replay.VERSIONS.__getitem__),
              patch.object(replay, "verify") as verify):
            root = Path(directory)
            marker = root / "synthetic-marker"
            marker.write_text("SYNTHETIC DO NOT REPLACE")
            with self.assertRaisesRegex(ValueError, "existing_trial_results"):
                replay.run("unused", root, "static", "unused", "unused", root)
            self.assertEqual("SYNTHETIC DO NOT REPLACE", marker.read_text())
            verify.assert_not_called()

    def test_wrong_package_version_is_rejected_before_recording_access(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(replay.metadata, "version", return_value="synthetic_wrong_version"),
              patch.object(replay, "verify") as verify):
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "unexpected_diagnostic_package_version"):
                replay.run("unused", root, "static", "unused", "unused", root / "new")
            verify.assert_not_called()
            self.assertFalse((root / "new").exists())


class OfflineSourceBoundaries(unittest.TestCase):
    def test_runner_never_initializes_ros_network_or_subprocess_interfaces(self):
        source = (ROOT / "tools/run_kiss_offline_trial.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module]
                self.assertFalse(
                    any(name.split(".")[0] in {"rclpy", "rclcpp", "socket", "requests", "subprocess"}
                        for name in names)
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ("create_publisher", "create_client", "create_node", "publish"))

    def test_actual_summary_keeps_every_run_and_no_quality_acceptance(self):
        evidence = json.loads((ROOT / "docs/slam-kiss-offline-evidence.json").read_text())
        capture = json.loads((ROOT / "docs/slam-capture-evidence.json").read_text())
        self.assertEqual(
            {"initialStatic", "initialDynamic", "staticDeskew", "dynamicDeskew", "dynamicNoDeskew"},
            set(evidence["runs"]),
        )
        self.assertEqual("not_accepted_pending_calibration_and_glim_comparison", evidence["selectionStatus"])
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        self.assertEqual(24, len(evidence["runtime"]["wheelInventory"]))
        for run in evidence["runs"].values():
            self.assertEqual(capture["datasets"][run["dataset"]]["topicCounts"]["cloud"], run["processedFrames"])
            self.assertEqual(0, run["nonfiniteCoordinatesDropped"])
            self.assertNotIn("poses", run)
            self.assertNotIn("stamps_ns", run)
        self.assertNotIn("runnerSha256", evidence["runs"]["initialStatic"])
        for name in ("staticDeskew", "dynamicDeskew", "dynamicNoDeskew"):
            self.assertRegex(evidence["runs"][name]["runnerSha256"], r"^[0-9a-f]{64}$")

    def test_exporter_cannot_promote_a_running_synthetic_report(self):
        spec = importlib.util.spec_from_file_location(
            "export_kiss_trial_evidence", ROOT / "tools/export_kiss_trial_evidence.py",
        )
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "explicit-synthetic-running-report.json"
            report.write_text(json.dumps({
                "dataset": "static", "result": "running", "syntheticFixture": True,
            }))
            with self.assertRaisesRegex(ValueError, "completed_diagnostic_reports"):
                exporter.summarize({"initialStatic": report})


if __name__ == "__main__":
    unittest.main()
