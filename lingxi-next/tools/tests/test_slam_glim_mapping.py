"""Synthetic serialization contracts, separate from actual private-map evidence."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[2]
try:
    import numpy as np
except ModuleNotFoundError:
    np = None

if np is not None and sys.platform == "linux":
    spec = importlib.util.spec_from_file_location(
        "run_glim_mapping_trial", ROOT / "tools/run_glim_mapping_trial.py",
    )
    mapping = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(mapping)


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy environment")
class MapPersistence(unittest.TestCase):
    def synthetic_map(self, root):
        root.mkdir()
        for name in ("graph.bin", "values.bin"):
            (root / name).write_bytes(b"synthetic-not-real-gtsam")
        (root / "graph.txt").write_text("num_submaps: 1\nnum_all_frames: 2\nnum_matching_cost_factors: 0\n")
        for name in ("odom_lidar.txt", "traj_lidar.txt", "odom_imu.txt", "traj_imu.txt"):
            (root / name).write_text("1000 0 0 0 0 0 0 1\n1001 1 0 0 0 0 0 1\n")
        (root / "000000").mkdir()
        (root / "000000/data.txt").write_text("synthetic-not-a-real-submap\n")
        (root / "000000/imu_rate.txt").write_text("")
        return root

    def test_complete_ordered_inventory_is_hashed_and_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.synthetic_map(Path(directory) / "map")
            value = mapping.inventory_map(root)
            self.assertEqual(1, value["num_submaps"])
            self.assertEqual(2, value["num_all_frames"])
            self.assertEqual(9, value["fileCount"])
            self.assertEqual(value, mapping.inventory_map(root))

    def test_existing_output_is_preserved_before_any_replay(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(mapping, "_run") as runner:
            with self.assertRaisesRegex(ValueError, "existing_mapping_trial"):
                mapping.run(NS(output=Path(directory)))
            runner.assert_not_called()

    def test_failure_is_not_reported_as_persistence_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new-trial"
            def failed(_):
                output.mkdir()
                raise RuntimeError("synthetic_failure")
            with patch.object(mapping, "_run", side_effect=failed):
                with self.assertRaisesRegex(RuntimeError, "synthetic_failure"):
                    mapping.run(NS(output=output))
            report = json.loads((output / "mapping-report.json").read_text())
            self.assertEqual("failed_or_interrupted", report["result"])
            self.assertFalse(report["mapPersistencePassed"])

    def test_missing_graph_and_unexpected_submap_are_rejected(self):
        for change in ("missing", "extra"):
            with tempfile.TemporaryDirectory() as directory:
                root = self.synthetic_map(Path(directory) / "map")
                if change == "missing":
                    (root / "values.bin").unlink()
                else:
                    (root / "000001").mkdir()
                with self.assertRaisesRegex(ValueError, "incomplete_global|complete_numbered"):
                    mapping.inventory_map(root)

    def test_empty_nonempty_required_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.synthetic_map(Path(directory) / "map")
            (root / "graph.bin").write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "empty_map"):
                mapping.inventory_map(root)

    def test_symlink_is_not_followed_into_unrelated_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.synthetic_map(Path(directory) / "map")
            (root / "unexpected").symlink_to(root / "values.bin")
            with self.assertRaisesRegex(ValueError, "indirect_map"):
                mapping.inventory_map(root)

    def test_nonfinite_reordered_or_missing_saved_trajectory_is_rejected(self):
        for rows in ("1000 0 0 0 0 0 0 1\n", "1001 0 0 0 0 0 0 1\n1000 0 0 0 0 0 0 1\n",
                     "1000 nan 0 0 0 0 0 1\n1001 0 0 0 0 0 0 1\n"):
            with tempfile.TemporaryDirectory() as directory:
                root = self.synthetic_map(Path(directory) / "map")
                (root / "traj_lidar.txt").write_text(rows)
                with self.assertRaisesRegex(ValueError, "complete_finite_ordered"):
                    mapping.inventory_map(root)

    def test_real_float32_serialization_error_is_compared_not_bitwise_equal(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a", Path(directory) / "b"
            values = np.array([[1.12345678, -2.23456789, 3.34567891]], dtype="<f8")
            values.tofile(first)
            values.astype(np.float32).astype("<f8").tofile(second)
            value = mapping.compare_points(first, second, 1)
            self.assertGreater(value["maximumPointDisplacementRawUnits"], 0)
            self.assertFalse(value["geometricAccuracyValidated"])

    def test_missing_count_nonfinite_and_outside_tolerance_are_rejected(self):
        for values in (np.zeros(2), np.array([np.nan, 0, 0]), np.array([0.001, 0, 0])):
            with tempfile.TemporaryDirectory() as directory:
                first, second = Path(directory) / "a", Path(directory) / "b"
                np.zeros(3, dtype="<f8").tofile(first)
                values.astype("<f8").tofile(second)
                with self.assertRaises(ValueError):
                    mapping.compare_points(first, second, 1)

    def test_map_order_change_is_not_silently_treated_as_a_geometric_match(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a", Path(directory) / "b"
            values = np.arange(6, dtype="<f8").reshape(2, 3)
            values.tofile(first)
            values[::-1].tofile(second)
            with self.assertRaisesRegex(ValueError, "round_trip"):
                mapping.compare_points(first, second, 2)

    def test_native_mapping_error_is_propagated(self):
        library = NS(**{name: Mock(return_value=0) for name in (
            "lingxi_mapping_open", "lingxi_mapping_finish", "lingxi_mapping_shutdown", "lingxi_mapping_reload",
        )}, lingxi_mapping_error=Mock(return_value=b"synthetic_mapping_error"))
        library.lingxi_mapping_finish.return_value = -1
        with patch.object(mapping.ctypes, "CDLL", return_value=library):
            native = mapping.MappingLibrary("synthetic.so")
            with self.assertRaisesRegex(RuntimeError, "synthetic_mapping_error"):
                native.call("finish")

    def exporter(self):
        spec = importlib.util.spec_from_file_location(
            "mapping_exporter", ROOT / "tools/export_glim_mapping_evidence.py",
        )
        exporter = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
            spec.loader.exec_module(exporter)
        return exporter

    def test_error_or_automatic_graph_recovery_is_not_clean_persistence(self):
        exporter = self.exporter()
        for text in ("[error] failed to deserialize", "recovering factor graph", "corrupted covariances"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "synthetic.log"
                path.write_text(text)
                with self.assertRaisesRegex(ValueError, "error_or_recovery"):
                    exporter.check_reload_log(path)

    def test_default_parameter_warning_is_distinct_from_load_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.log"
            path.write_text("[warning] param global_mapping/submap_voxel_resolution not found\n")
            self.exporter().check_reload_log(path)

    def test_nested_export_whitelist_cannot_leak_new_point_or_profile_fields(self):
        exporter = self.exporter()
        report = {name: 1 for name in exporter.REPORT_FIELDS}
        for group, fields in exporter.NESTED_FIELDS.items():
            report[group] = {name: 1 for name in fields}
            report[group]["privatePointCoordinates"] = [[123, 456, 789]]
        report["privateSensorProfile"] = {"T_lidar_imu": [1, 2, 3]}
        exported = json.dumps(exporter.aggregate_report(report))
        self.assertNotIn("privatePointCoordinates", exported)
        self.assertNotIn("privateSensorProfile", exported)


class MappingBoundaries(unittest.TestCase):
    def test_mapping_extension_has_no_ros_network_or_motion_api(self):
        text = (ROOT / "tools/glim-mapping/mapping.cpp").read_text()
        for token in ("rclcpp", "cmd_vel", "create_publisher", "McLocomotion", "socket("):
            self.assertNotIn(token, text)
        tree = ast.parse((ROOT / "tools/run_glim_mapping_trial.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module]
                for name in names:
                    self.assertNotIn(name.split(".")[0], {"rclpy", "socket", "requests", "paramiko"})

    def test_callback_ownership_and_eof_do_not_double_feed(self):
        text = (ROOT / "tools/glim-mapping/mapping.cpp").read_text()
        self.assertIn("on_marginalized_frames.add", text)
        self.assertIn("on_marginalized_frames.remove", text)
        self.assertIn("frame->id <= last_frame_id", text)
        self.assertIn("saved_frames != frame_count", text)
        self.assertNotIn("odometry->get_remaining_frames", text)
        self.assertEqual(1, text.count("submit_end_of_sequence()"))

    def test_historical_odometry_provenance_does_not_follow_new_runner(self):
        spec = importlib.util.spec_from_file_location(
            "historical_glim_exporter", ROOT / "tools/export_glim_trial_evidence.py",
        )
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        old = json.loads((ROOT / "docs/slam-glim-offline-evidence.json").read_text())
        for label, run in old["runs"].items():
            if label != "initialStaticStart":
                self.assertEqual(exporter.HISTORICAL_FINAL_RUNNER, run["runnerSha256"])

    def test_public_map_evidence_keeps_four_runs_and_acceptance_boundaries(self):
        evidence = json.loads((ROOT / "docs/slam-glim-mapping-evidence.json").read_text())
        self.assertEqual({"staticStart", "staticEnd", "dynamicStart", "dynamicEnd"}, set(evidence["runs"]))
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        for run in evidence["runs"].values():
            self.assertTrue(all(value is True for value in run["checks"].values()))
            self.assertEqual(run["mapping"]["points"], run["roundTrip"]["pointCount"])
            self.assertEqual(run["mapping"]["savedFrames"], run["scanAccounting"]["estimatedPoses"])
            self.assertLess(run["roundTrip"]["maximumPointDisplacementRawUnits"],
                            run["roundTrip"]["predeclaredToleranceRawUnits"])
            self.assertLess(run["mapping"]["submapTravel"], run["mapping"]["minimumLoopTravel"])
            self.assertFalse(run["boundaries"]["relocalizationValidated"])


if __name__ == "__main__":
    unittest.main()
