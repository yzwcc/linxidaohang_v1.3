"""Synthetic sequencing checks for the actual-target recorded-data path."""
import ast
import copy
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
try:
    import numpy as np
except ModuleNotFoundError:
    np = None


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(value)
    return value


launcher = load("run_glim_target_replays")
if np is not None and sys.platform == "linux":
    replay = load("glim_target_replay")
    builder = load("build_glim_target_replay")
    exporter = load("export_glim_target_replay_evidence")


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy")
class RecordedTargetEvidence(unittest.TestCase):
    def fixture(self):
        poses = np.repeat(np.eye(4)[None], 2, axis=0)
        poses[1, 0, 3] = 0.1
        measures = np.array([[1, 4, 60, 0], [2, 3, 50, 1], [3, 2, 70, 2]], dtype=float)
        arrays = {
            "poses": poses, "header_stamps_ns": np.array([100_000_000, 200_000_000], dtype=np.int64),
            "algorithm_stamps_relative_seconds": np.array([1000.1, 1000.2]),
            "preprocess_odometry_pointcount_marginalized": measures,
        }
        report = {
            "recordedScans": 4, "processedScans": 3, "skippedUnbracketedScans": 1,
            "initializationScans": 1, "estimatedPoses": 2, "imuSamplesInserted": 7,
            "preprocessMillisecondsP50P95P99": np.percentile(measures[:, 0], [50, 95, 99]).tolist(),
            "odometryMillisecondsP50P95P99": np.percentile(measures[:, 1], [50, 95, 99]).tolist(),
            "combinedMillisecondsMaximum": 5.0, "processingWallSeconds": 0.2,
            "processCpuSeconds": 0.3, "peakResidentMiB": 40.0,
            "estimatedMotionNotGroundTruth": {
                "accumulatedPositionStepsRawUnits": 0.1, "endToStartDistanceRawUnits": 0.1,
                "maximumConsecutivePositionStepRawUnits": 0.1,
            },
        }
        expected = {"topicCounts": {"cloud": 4, "imu": 10}}
        return report, arrays, expected

    def test_recompute_uses_per_frame_total_not_sum_of_percentiles(self):
        report, arrays, expected = self.fixture()
        aggregate = exporter.recompute(report, arrays, expected)
        self.assertEqual(3, aggregate["counts"]["processedScans"])
        self.assertEqual(5.0, aggregate["timingsNotRealtimeAcceptance"]["combinedMillisecondsP50P95P99"][1])

    def test_missing_skipped_or_initialized_scan_accounting_is_rejected(self):
        for name in ("recordedScans", "skippedUnbracketedScans", "initializationScans", "estimatedPoses"):
            report, arrays, expected = self.fixture()
            report[name] += 1
            with self.assertRaisesRegex(ValueError, "accounted_for"):
                exporter.recompute(report, arrays, expected)

    def test_changed_aggregate_cannot_be_published(self):
        report, arrays, expected = self.fixture()
        report["odometryMillisecondsP50P95P99"][1] += 0.01
        with self.assertRaisesRegex(ValueError, "aggregates_do_not_match"):
            exporter.recompute(report, arrays, expected)

    def test_nonfinite_or_unordered_trajectories_are_rejected(self):
        for name, index, value in (
            ("header_stamps_ns", 1, 100_000_000),
            ("algorithm_stamps_relative_seconds", 1, 1000.1),
            ("poses", (1, 0, 3), float("nan")),
        ):
            report, arrays, expected = self.fixture()
            arrays[name][index] = value
            with self.assertRaises(ValueError):
                exporter.recompute(report, arrays, expected)

    def test_nonfinite_negative_or_fractional_measurements_are_rejected(self):
        for index, value in (((0, 0), float("inf")), ((0, 1), -1), ((0, 2), 49), ((0, 3), 1.5)):
            report, arrays, expected = self.fixture()
            arrays["preprocess_odometry_pointcount_marginalized"][index] = value
            with self.assertRaisesRegex(ValueError, "private_arrays"):
                exporter.recompute(report, arrays, expected)

    def test_bad_resource_measurements_are_not_published_as_performance(self):
        for name in ("processCpuSeconds", "processingWallSeconds", "peakResidentMiB"):
            report, arrays, expected = self.fixture()
            report[name] = float("inf")
            with self.assertRaisesRegex(ValueError, "resource_measurements"):
                exporter.recompute(report, arrays, expected)

    def test_nested_unknown_fields_and_raw_arrays_are_not_exported(self):
        report, arrays, expected = self.fixture()
        report["privateProfile"] = "SYNTHETIC_PRIVATE_MARKER"
        report["estimatedMotionNotGroundTruth"]["rawCoordinates"] = "SYNTHETIC_PRIVATE_MARKER"
        result = exporter.recompute(copy.deepcopy(report), arrays, expected)
        self.assertEqual({"counts", "timingsNotRealtimeAcceptance", "estimatedMotionNotGroundTruth"}, set(result))
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", str(result))
        self.assertNotIn("poses", result)


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy")
class PrivateBundleDependencies(unittest.TestCase):
    def test_transitive_local_imports_must_be_declared_before_build(self):
        builder.validate_python_sources(ROOT / "tools")
        missing = tuple(name for name in builder.PYTHON_SOURCES if name != "slam_preflight.py")
        with self.assertRaisesRegex(ValueError, "missing_private_bundle_dependency_slam_preflight.py"):
            builder.validate_python_sources(ROOT / "tools", missing)

    def test_isolated_interpreter_imports_only_the_staged_python_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in builder.PYTHON_SOURCES:
                shutil.copyfile(ROOT / "tools" / name, root / name)
            command = [sys.executable, "-I", "-B", "-c",
                       "import sys; sys.path.insert(0, sys.argv[1]); import glim_target_replay",
                       str(root)]
            subprocess.run(command, cwd=root, check=True, capture_output=True, text=True)
            (root / "slam_preflight.py").unlink()
            failed = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertNotEqual(0, failed.returncode)
            self.assertIn("No module named 'slam_preflight'", failed.stderr)


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy")
class RecordedTargetSequencing(unittest.TestCase):
    def fixture(self):
        base = 1_700_000_000 * 10**9
        def header(offset, frame):
            stamp = base + offset
            return NS(frame_id=frame, stamp=NS(sec=stamp // 10**9, nanosec=stamp % 10**9))
        imu = [NS(header=header(index * 50_000_000, "lidar_imu_chest_front"),
                  orientation=NS(x=0, y=0, z=0, w=1),
                  linear_acceleration=NS(x=0, y=0, z=9.81),
                  angular_velocity=NS(x=0, y=0, z=0)) for index in range(11)]
        cloud = [NS(header=header(offset, "lidar_chest_front"))
                 for offset in (-100_000_000, 100_000_000, 200_000_000, 300_000_000)]
        class SyntheticNative:
            def __init__(self):
                self.last_imu = None
                self.stamps = []
            def imu(self, stamp, acceleration, angular):
                self.last_imu = stamp
            def frame(self, stamp, points, offsets):
                if self.last_imu < stamp + offsets.max():
                    raise AssertionError("scan_not_bracketed")
                self.stamps.append(stamp)
                return (None if len(self.stamps) == 1 else np.eye(4)), np.array([1, 2, 12, 0])
        native = SyntheticNative()
        expected = {"topicCounts": {"imu": len(imu), "cloud": len(cloud)}}
        def messages(_, topic):
            return iter(imu if topic == replay.IMU else cloud)
        return native, expected, messages, imu, cloud

    def run_fixture(self, hypothesis, fixture=None):
        native, expected, messages, imu, cloud = fixture or self.fixture()
        with patch.object(replay, "cloud_arrays", return_value=(
            np.ones((12, 3)), np.linspace(0, 0.05, 12), 0,
        )):
            result = replay.replay_recording(Path("/synthetic-not-a-real-bag"), expected,
                                             hypothesis, native, messages=messages)
        return native, result

    def test_complete_counts_and_initialization_are_not_fabricated(self):
        native, (report, poses, stamps, algorithm, measures) = self.run_fixture("header_is_scan_start")
        self.assertEqual((4, 3, 1, 1, 2), tuple(report[name] for name in (
            "recordedScans", "processedScans", "skippedUnbracketedScans",
            "initializationScans", "estimatedPoses",
        )))
        self.assertEqual((2, 4, 4), poses.shape)
        self.assertEqual((3, 4), measures.shape)
        self.assertEqual(np.int64, stamps.dtype)
        self.assertAlmostEqual(1000.1, native.stamps[0])
        self.assertAlmostEqual(1000.2, algorithm[0])

    def test_scan_start_and_end_remain_explicit_separate_hypotheses(self):
        start, _ = self.run_fixture("header_is_scan_start")
        end, _ = self.run_fixture("header_is_scan_end")
        np.testing.assert_allclose(np.asarray(start.stamps) - np.asarray(end.stamps), 0.05, atol=1e-12)
        with self.assertRaisesRegex(ValueError, "explicit_timing"):
            self.run_fixture("auto")

    def test_missing_imu_count_fails_before_processing_cloud(self):
        fixture = self.fixture()
        fixture[1]["topicCounts"]["imu"] += 1
        with self.assertRaisesRegex(ValueError, "complete_recorded_imu"):
            self.run_fixture("header_is_scan_start", fixture)
        self.assertEqual([], fixture[0].stamps)

    def test_out_of_order_clouds_are_not_hidden(self):
        fixture = self.fixture()
        fixture[4][-1].header = fixture[4][-2].header
        with self.assertRaisesRegex(ValueError, "ordered_expected"):
            self.run_fixture("header_is_scan_start", fixture)

    def test_wrong_sensor_frame_is_not_transformed_by_guessing(self):
        for kind in ("imu", "cloud"):
            fixture = self.fixture()
            (fixture[3] if kind == "imu" else fixture[4])[0].header.frame_id = "unknown"
            with self.assertRaisesRegex(ValueError, "recorded_imu_frame|recorded_cloud"):
                self.run_fixture("header_is_scan_start", fixture)

    def test_recording_must_not_end_before_declared_cloud_count(self):
        fixture = self.fixture()
        fixture[1]["topicCounts"]["cloud"] += 1
        with self.assertRaisesRegex(ValueError, "complete_capture"):
            self.run_fixture("header_is_scan_start", fixture)


class TargetReplayBoundary(unittest.TestCase):
    def test_public_evidence_has_exact_aggregate_only_nested_fields(self):
        evidence = json.loads((ROOT / "docs/slam-arm64-recorded-evidence.json").read_text())
        self.assertEqual({
            "schemaVersion", "observedDate", "purpose", "result", "sourceCommits",
            "captureManifestSha256", "replayManifestSha256", "privateCollectionReportSha256",
            "adapterBuildReportSha256", "adapterSha256", "replaySourceSha256",
            "toolSourcesAtExport", "runs", "originalCapturesUnchangedAtExport", "boundaries",
        }, set(evidence))
        self.assertEqual({entry[0] for entry in launcher.RUNS}, set(evidence["runs"]))
        for run in evidence["runs"].values():
            self.assertEqual({
                "dataset", "timingHypothesis", "privateReportSha256", "privateTrajectorySha256",
                "privateNativeLogSha256", "target", "counts", "timingsNotRealtimeAcceptance",
                "estimatedMotionNotGroundTruth",
            }, set(run))
            self.assertEqual({"machine", "python", "numpy"}, set(run["target"]))
            self.assertEqual({
                "recordedScans", "processedScans", "skippedUnbracketedScans",
                "initializationScans", "estimatedPoses", "imuSamplesInserted",
            }, set(run["counts"]))
            self.assertEqual({
                "preprocessMillisecondsP50P95P99", "odometryMillisecondsP50P95P99",
                "combinedMillisecondsMaximum", "combinedMillisecondsP50P95P99",
                "processingWallSeconds", "processCpuSeconds", "peakResidentMiB",
            }, set(run["timingsNotRealtimeAcceptance"]))
            self.assertEqual({
                "accumulatedPositionStepsRawUnits", "endToStartDistanceRawUnits",
                "maximumConsecutivePositionStepRawUnits",
            }, set(run["estimatedMotionNotGroundTruth"]))

    def test_public_evidence_accounts_for_initialization_without_physical_acceptance(self):
        evidence = json.loads((ROOT / "docs/slam-arm64-recorded-evidence.json").read_text())
        self.assertTrue(evidence["originalCapturesUnchangedAtExport"])
        self.assertGreater(len(evidence["boundaries"]), 10)
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        for label, dataset, hypothesis in launcher.RUNS:
            run = evidence["runs"][label]
            self.assertEqual((dataset, hypothesis), (run["dataset"], run["timingHypothesis"]))
            self.assertEqual("aarch64", run["target"]["machine"])
            counts = run["counts"]
            self.assertEqual(counts["recordedScans"], counts["processedScans"] + counts["skippedUnbracketedScans"])
            self.assertEqual(counts["processedScans"], counts["initializationScans"] + counts["estimatedPoses"])

    def test_four_trials_are_predeclared_not_selected_after_results(self):
        self.assertEqual(4, len(launcher.RUNS))
        self.assertEqual({"static", "dynamic"}, {entry[1] for entry in launcher.RUNS})
        self.assertEqual({"header_is_scan_start", "header_is_scan_end"}, {entry[2] for entry in launcher.RUNS})

    def test_command_is_clean_bounded_low_priority_and_home_scoped(self):
        home = PurePosixPath("/home/synthetic")
        root = home / "lingxi-next-validation-arm64-real-synthetic"
        core = home / "lingxi-next-validation-arm64-synthetic"
        command = shlex.split(launcher.replay_command(
            root, core, home, "static", "header_is_scan_start", root / "result-static",
        ))
        self.assertEqual(["timeout", "--signal=INT", "--kill-after=10s", "480s", "nice", "-n", "10"], command[:7])
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", command)
        self.assertIn("AMENT_PREFIX_PATH=/opt/ros/humble", command)
        self.assertNotIn("ROS_DISTRO=humble", command)
        self.assertIn("OMP_NUM_THREADS=2", command)
        with self.assertRaises(ValueError):
            launcher.replay_command(root, core, home, "static", "auto", root / "result")
        with self.assertRaises(ValueError):
            launcher.replay_command(root, core, home, "static", "header_is_scan_start", home / "outside")

    def test_reader_uses_serialization_without_creating_ros_node_or_publishers(self):
        text = (ROOT / "tools/glim_target_replay.py").read_text()
        tree = ast.parse(text)
        self.assertIn("rosbag2_py.SequentialReader()", text)
        self.assertIn("deserialize_message(payload, message_type)", text)
        for token in ("rclpy.init", "create_node", "create_publisher", "create_subscription",
                      "cmd_vel", "McLocomotion", "publish("):
            self.assertNotIn(token, text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name, {"socket", "requests", "paramiko"})


if __name__ == "__main__":
    unittest.main()
