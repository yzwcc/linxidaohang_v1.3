"""Synthetic contracts for native preprocessing observations, not mapping acceptance."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ModuleNotFoundError:
    np = None

ROOT = Path(__file__).parents[2]
if np is not None and sys.platform == "linux":
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec = importlib.util.spec_from_file_location("preprocess_trial", ROOT / "tools/run_glim_preprocess_trial.py")
        trial = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(trial)
        spec = importlib.util.spec_from_file_location("preprocess_exporter", ROOT / "tools/export_glim_preprocess_evidence.py")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy environment")
class PreprocessContract(unittest.TestCase):
    def test_twelve_conditions_are_declared_before_observation(self):
        self.assertEqual(12, len(trial.CONDITIONS))
        self.assertEqual(12, len(set(trial.CONDITIONS)))
        for dataset in ("static", "dynamic"):
            for threads in (1, 2):
                self.assertEqual([1, 2, 3], [repeat for kind, count, repeat in trial.CONDITIONS
                                           if kind == dataset and count == threads])

    def test_output_cannot_overwrite_or_enter_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "preserved"):
                trial.new_output(root, ())
            with self.assertRaisesRegex(ValueError, "outside_input"):
                trial.new_output(root / "new", (root,))
            trial.new_output(root / "new", (root / "input",))
            self.assertFalse((root / "new").exists())

    def test_build_pins_have_actual_content_hashes(self):
        self.assertEqual(4, len(trial.SOURCE_PINS))
        for digest in trial.SOURCE_PINS.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_private_evidence_writer_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.json"
            trial.save(path, {"synthetic": True})
            with self.assertRaises(FileExistsError):
                trial.save(path, {"synthetic": False})
            self.assertIn("true", path.read_text())

    def observation(self):
        points = np.array([[1, 2, 3, 0.01], [4, 5, 6, 0.02],
                           [7, 8, 9, 0.01], [10, 11, 12, 0.02]], dtype="<f8")
        return {
            "index": {
                "header_stamps_ns": np.array([1000, 2000], dtype=np.int64),
                "algorithm_stamps_relative_seconds": np.array([1000., 1001.]),
                "input_cloud_sha256": np.array(["a" * 64, "b" * 64]),
                "counts": np.array([[12000, 2], [12000, 2]], dtype=np.int64),
            },
            "points": points, "offsets": np.array([0, 2, 4]),
            "neighbors": np.array([[0] * 10, [1] * 10] * 2, dtype="<i4"),
        }

    def test_same_complete_point_rows_and_neighbors_have_no_difference(self):
        value = self.observation()
        result = exporter.compare_observations(value, value)
        self.assertEqual(2, result["processedFrames"])
        for key in ("preparedPointCountDifferentFrames", "orderedXyztBitSequenceDifferentFrames",
                    "sameExactXyztMultisetButDifferentOrderFrames", "exactXyztMultisetDifferentFrames",
                    "neighborIndexSequenceDifferentFramesAmongIdenticalOrderedXyzt"):
            self.assertEqual(0, result[key])

    def test_order_only_and_selection_differences_are_distinct(self):
        first, second = self.observation(), self.observation()
        second["points"][:2] = second["points"][[1, 0]]
        result = exporter.compare_observations(first, second)
        self.assertEqual(1, result["sameExactXyztMultisetButDifferentOrderFrames"])
        self.assertEqual(0, result["exactXyztMultisetDifferentFrames"])
        second["points"][0, 0] += 0.01
        result = exporter.compare_observations(first, second)
        self.assertEqual(0, result["sameExactXyztMultisetButDifferentOrderFrames"])
        self.assertEqual(1, result["exactXyztMultisetDifferentFrames"])

    def test_neighbor_differences_are_only_compared_for_identical_ordered_points(self):
        first, second = self.observation(), self.observation()
        second["neighbors"][0, 0] = 1
        result = exporter.compare_observations(first, second)
        self.assertEqual(1, result["neighborIndexSequenceDifferentFramesAmongIdenticalOrderedXyzt"])
        second["points"][0, 0] += 0.01
        result = exporter.compare_observations(first, second)
        self.assertEqual(0, result["neighborIndexSequenceDifferentFramesAmongIdenticalOrderedXyzt"])

    def test_different_point_counts_remain_a_selection_difference(self):
        first, second = self.observation(), self.observation()
        second["points"] = np.delete(second["points"], 1, axis=0)
        second["neighbors"] = np.delete(second["neighbors"], 1, axis=0)
        second["index"]["counts"][0, 1] = 1
        second["offsets"] = np.array([0, 1, 3])
        result = exporter.compare_observations(first, second)
        self.assertEqual(1, result["preparedPointCountDifferentFrames"])
        self.assertEqual(1, result["exactXyztMultisetDifferentFrames"])
        self.assertEqual(0, result["sameExactXyztMultisetButDifferentOrderFrames"])

    def test_different_inputs_times_and_raw_sizes_are_not_repeatability(self):
        for name in ("header_stamps_ns", "algorithm_stamps_relative_seconds", "input_cloud_sha256", "counts"):
            first, second = self.observation(), self.observation()
            if name == "input_cloud_sha256":
                second["index"][name][0] = "c" * 64
            elif name == "counts":
                second["index"][name][0, 0] += 1
            else:
                second["index"][name][0] += 1
            with self.assertRaisesRegex(ValueError, "identical_"):
                exporter.compare_observations(first, second)

    def test_float_bit_patterns_including_signed_zero_are_compared_exactly(self):
        first = np.array([[0, 1, 2, 3], [-0., 1, 2, 3]], dtype="<f8")
        second = first[::-1].copy()
        self.assertFalse(exporter.same_bit_rows(first, second))
        self.assertTrue(exporter.same_bit_rows(first, second, ignore_order=True))
        self.assertFalse(exporter.same_bit_rows(first, second[:1], ignore_order=True))

    def fixture(self, path):
        path.mkdir()
        frames, count = 2, 50
        points = np.zeros((frames * count, 4), dtype="<f8")
        neighbors = np.zeros((frames * count, 10), dtype="<i4")
        points.tofile(path / "private-prepared-xyzt.f64")
        neighbors.tofile(path / "private-neighbors.i32")
        (path / "private-native.log").write_text("synthetic fixture, not real native execution\n")
        np.savez_compressed(path / "private-index.npz",
                            counts=np.array([[100, count]] * frames, dtype=np.int64),
                            header_stamps_ns=np.array([1000, 2000], dtype=np.int64),
                            algorithm_stamps_relative_seconds=np.array([1000., 1001.]),
                            input_cloud_sha256=np.array(["a" * 64, "b" * 64]))
        report = {
            "result": "complete_native_preprocess_recording_saved_not_slam_acceptance",
            "dataset": "static", "diagnosticThreads": 2, "hostMachine": "x86_64", "pid": 1,
            "runnerSourceSha256": trial.sha256(ROOT / "tools/run_glim_preprocess_trial.py"),
            "boundaries": {name: False for name in trial.BOUNDARIES},
            "originalCapturesUnchanged": True, "recordedScans": 3,
            "processedScans": frames, "skippedUnbracketedScans": 1, "totalPreparedPoints": count * frames,
            "filesSha256": {name: trial.sha256(path / name) for name in exporter.FILES},
        }
        trial.save(path / "report.json", report)
        return {"exitCode": 0, "reportSha256": trial.sha256(path / "report.json")}

    def test_whole_output_files_are_validated_and_not_only_report_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic"
            collection = self.fixture(path)
            value = exporter.load_observation(path, ("static", 2, 1), collection)
            self.assertEqual((100, 4), value["points"].shape)
            self.assertEqual((100, 10), value["neighbors"].shape)
            with (path / "private-prepared-xyzt.f64").open("ab") as stream:
                stream.write(b"extra")
            with self.assertRaisesRegex(ValueError, "digest_mismatch"):
                exporter.load_observation(path, ("static", 2, 1), collection)

    def test_metrics_do_not_export_added_private_values(self):
        first, second = self.observation(), self.observation()
        first["privateExtrinsic"] = "synthetic_private_marker"
        text = json.dumps(exporter.compare_observations(first, second))
        for token in ("synthetic_private_marker", "privateExtrinsic", "input_cloud_sha256", '"points"', '"neighbors"'):
            self.assertNotIn(token, text)


class PreprocessBoundary(unittest.TestCase):
    def test_native_adapter_only_calls_preprocessing(self):
        text = (ROOT / "tools/glim-preprocess/preprocess.cpp").read_text()
        for token in ("rclcpp", "socket(", "cmd_vel", "McLocomotion", "OdometryEstimation", "GlobalMapping"):
            self.assertNotIn(token, text)
        self.assertIn("CloudPreprocessor", text)
        self.assertIn("params.num_threads = diagnostic_threads", text)
        self.assertIn("prepared->neighbors", text)

    def test_runner_has_no_robot_connector_or_ros_initialization(self):
        text = (ROOT / "tools/run_glim_preprocess_trial.py").read_text()
        for token in ("paramiko", "rosbag2_py", "rclpy", "create_node", "create_publisher",
                      "cmd_vel", "ssh", "sudo", "apt-get"):
            self.assertNotIn(token, text)
        tree = ast.parse(text)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "run"]
        self.assertTrue(calls)
        self.assertTrue(all(any(keyword.arg == "timeout" for keyword in node.keywords) for node in calls))

    def test_public_evidence_distinguishes_repetition_from_accuracy(self):
        evidence = json.loads((ROOT / "docs/slam-preprocess-repeatability-evidence.json").read_text())
        for field, name in (("runnerSourceSha256", "run_glim_preprocess_trial.py"),
                            ("exporterSourceSha256", "export_glim_preprocess_evidence.py")):
            self.assertEqual(hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest(),
                             evidence[field])
        for name in ("preprocess.cpp", "CMakeLists.txt"):
            self.assertEqual(hashlib.sha256((ROOT / "tools/glim-preprocess" / name).read_bytes()).hexdigest(),
                             evidence["adapterSourcesSha256"][name])
        self.assertEqual(12, evidence["freshProcessCount"])
        self.assertEqual(12, len(evidence["runs"]))
        self.assertEqual(12, len(evidence["withinConditionPairs"]))
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        self.assertTrue(evidence["controlIsNotProductionConfiguration"])
        self.assertTrue(evidence["allPrivateOutputsUnchangedAtExport"])
        for label, pair in evidence["withinConditionPairs"].items():
            self.assertTrue(pair["allRawInputContentAndTimesIdentical"])
            if "-threads1-" in label:
                self.assertEqual(0, pair["orderedXyztBitSequenceDifferentFrames"])
                self.assertEqual(0, pair["neighborIndexSequenceDifferentFramesAmongIdenticalOrderedXyzt"])
            else:
                self.assertGreater(pair["exactXyztMultisetDifferentFrames"], 0)
        for token in ('"pid"', '"header_stamps_ns"', '"T_lidar_imu"', '"input_cloud_sha256"',
                      '"password"', '"username"', '"hostname"', '"points"', '"neighbors"'):
            self.assertNotIn(token, json.dumps(evidence))


if __name__ == "__main__":
    unittest.main()
