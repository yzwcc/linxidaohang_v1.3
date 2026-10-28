"""Synthetic whole-map control contracts; do not execute SLAM, ROS, or robot commands."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).parents[2]
AVAILABLE = (sys.platform == "linux" and importlib.util.find_spec("numpy") is not None
             and importlib.util.find_spec("json5") is not None)
if AVAILABLE:
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec = importlib.util.spec_from_file_location("controlled_map", ROOT / "tools/run_glim_controlled_mapping.py")
        control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(control)
        spec = importlib.util.spec_from_file_location("control_export", ROOT / "tools/export_glim_controlled_mapping.py")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        spec = importlib.util.spec_from_file_location("control_views", ROOT / "tools/render_glim_controlled_maps.py")
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy and pinned JSON5 parser")
class ControlledConfiguration(unittest.TestCase):
    def baseline(self, root, threads=2):
        root.mkdir()
        document = {"preprocess": {"num_threads": threads, "distance_far_thresh": 100.,
                                   "note": "a string containing // and /* is not a comment"}}
        (root / "config_preprocess.json").write_text("/* synthetic fixture only */\n" + json.dumps(document))
        (root / "config_sensors.json").write_text('{"synthetic_not_calibrated":true}\n')
        manifest = {"files": control.config_hashes(root), "deploymentApproved": False,
                    "robotCalibrationValidated": False, "navigationValidated": False}
        (root / "trial-manifest.json").write_text(json.dumps(manifest))
        return control.sha256(root / "config_preprocess.json")

    def test_structured_json5_edit_changes_only_declared_parameter_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, output = Path(directory) / "baseline", Path(directory) / "control"
            digest = self.baseline(baseline)
            before = control.config_hashes(baseline)
            with patch.object(control, "PREPROCESS_CONFIG_SHA256", digest):
                report = control.make_control(baseline, output)
            self.assertEqual(before, control.config_hashes(baseline))
            effective = json.loads((output / "config_preprocess.json").read_text())
            self.assertEqual(1, effective["preprocess"]["num_threads"])
            self.assertEqual(100., effective["preprocess"]["distance_far_thresh"])
            self.assertIn("// and /*", effective["preprocess"]["note"])
            self.assertEqual(report["semanticParameterChanges"],
                             [{"file": "config_preprocess.json", "path": ["preprocess", "num_threads"],
                               "before": 2, "after": 1}])
            self.assertEqual(report["effectiveFilesSha256"], control.config_hashes(output))
            manifest = json.loads((output / "trial-manifest.json").read_text())
            self.assertEqual(manifest["files"]["config_preprocess.json"],
                             control.sha256(output / "config_preprocess.json"))
            self.assertTrue(manifest["diagnosticControlOnly"])

    def test_existing_or_nested_control_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = self.baseline(root / "baseline")
            with patch.object(control, "PREPROCESS_CONFIG_SHA256", digest):
                for destination in (root, root / "baseline/new"):
                    with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, "separate_new"):
                        control.make_control(root / "baseline", destination)

    def test_changed_baseline_bytes_or_manifest_are_rejected(self):
        for field in ("hash", "manifest", "approval"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                baseline, output = Path(directory) / "base", Path(directory) / "control"
                digest = self.baseline(baseline)
                if field == "hash":
                    (baseline / "config_preprocess.json").write_text("{}")
                else:
                    path = baseline / "trial-manifest.json"
                    value = json.loads(path.read_text())
                    if field == "manifest":
                        value["files"]["config_sensors.json"] = "0" * 64
                    else:
                        value["deploymentApproved"] = True
                    path.write_text(json.dumps(value))
                with patch.object(control, "PREPROCESS_CONFIG_SHA256", digest):
                    with self.assertRaisesRegex(ValueError, "verified_unchanged"):
                        control.make_control(baseline, output)
                self.assertFalse(output.exists())

    def test_other_thread_counts_and_boolean_are_not_a_two_to_one_control(self):
        for threads in (1, 3, True):
            with self.subTest(threads=threads), tempfile.TemporaryDirectory() as directory:
                baseline, output = Path(directory) / "base", Path(directory) / "control"
                digest = self.baseline(baseline, threads)
                with patch.object(control, "PREPROCESS_CONFIG_SHA256", digest):
                    with self.assertRaisesRegex(ValueError, "two_to_one"):
                        control.make_control(baseline, output)

    def test_symlinked_config_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.baseline(root / "base")
            (root / "base/foreign").symlink_to(root / "outside")
            with self.assertRaisesRegex(ValueError, "direct_configuration"):
                control.config_hashes(root / "base")

    def test_conditions_keep_both_datasets_and_both_timing_hypotheses(self):
        self.assertEqual(12, len(control.CONDITIONS))
        self.assertEqual(12, len({control.label(*condition) for condition in control.CONDITIONS}))
        for dataset in ("static", "dynamic"):
            for hypothesis in control.replay.HYPOTHESES:
                self.assertEqual([1, 2, 3], [repeat for kind, timing, repeat in control.CONDITIONS
                                           if (kind, timing) == (dataset, hypothesis)])

    def test_existing_batch_or_trial_is_rejected_before_native_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = NS(output=root, data_root=root / "data", work=root / "work", adapter=root / "adapter",
                      mapping_adapter=root / "mapping", upstream=root / "upstream")
            with patch.object(control.mapping, "verify_extension") as native:
                for runner in (control.run, control.batch):
                    with self.assertRaisesRegex(ValueError, "preserved"):
                        runner(args)
                native.assert_not_called()

    def test_verified_control_preserves_baseline_and_effective_configuration_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = self.baseline(root / "config")
            baseline_manifest = control.sha256(root / "config/trial-manifest.json")
            with patch.object(control, "PREPROCESS_CONFIG_SHA256", digest):
                report = control.make_control(root / "config", root / "control-config")
            control.save(root / "control-manifest.json", report)
            self.assertEqual(report, exporter.verify_control(root, baseline_manifest))
            (root / "control-config/config_sensors.json").write_text('{"synthetic_changed":true}')
            with self.assertRaisesRegex(ValueError, "exactly_declared"):
                exporter.verify_control(root, baseline_manifest)

    def test_rehashed_undeclared_parameter_change_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = self.baseline(root / "config")
            baseline_manifest = control.sha256(root / "config/trial-manifest.json")
            with patch.object(control, "PREPROCESS_CONFIG_SHA256", digest):
                report = control.make_control(root / "config", root / "control-config")
            path = root / "control-config/config_preprocess.json"
            value = json.loads(path.read_text())
            value["preprocess"]["distance_far_thresh"] = 5
            path.write_text(json.dumps(value))
            report["effectiveFilesSha256"] = control.config_hashes(root / "control-config")
            control.save(root / "control-manifest.json", report)
            with self.assertRaisesRegex(ValueError, "undeclared_preprocessing"):
                exporter.verify_control(root, baseline_manifest)


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy and pinned JSON5 parser")
class ControlledComparison(unittest.TestCase):
    def value(self):
        import numpy as np
        return {"stamps": np.array([100, 200], dtype=np.int64),
                "algorithm": np.array([1000., 1001.]), "online": np.repeat(np.eye(4)[None], 2, axis=0),
                "saved": np.repeat(np.eye(4)[None], 2, axis=0), "members": [2],
                "scanCounters": np.array([[50, 0], [50, 1]]),
                "points": np.array([[1., 2., 3.], [4., 5., 6.]]),
                "privateSecret": "synthetic_private_marker"}

    def test_identical_complete_maps_are_bit_identical_without_accuracy_claim(self):
        value = self.value()
        result = exporter.comparison(value, value, True)
        self.assertTrue(result["fullOnlinePoseArraysBitIdentical"])
        self.assertTrue(result["completeSavedMapPoints"]["orderedCoordinateBitsIdentical"])
        self.assertTrue(result["completeSavedMapPoints"]["coordinateBitMultisetsIdentical"])
        self.assertFalse(result["onlineLidarPosesNotFinalSmoothed"]["accuracyAgainstTruthMeasured"])
        self.assertNotIn("synthetic_private_marker", json.dumps(result))

    def test_map_order_only_and_point_selection_difference_are_not_confused(self):
        first, second = self.value(), self.value()
        second["points"] = second["points"][::-1]
        result = exporter.comparison(first, second, True)["completeSavedMapPoints"]
        self.assertFalse(result["orderedCoordinateBitsIdentical"])
        self.assertTrue(result["coordinateBitMultisetsIdentical"])
        second["points"][0, 0] += .1
        self.assertFalse(exporter.comparison(first, second, True)["completeSavedMapPoints"]
                         ["coordinateBitMultisetsIdentical"])

    def test_point_count_changes_are_not_forced_into_point_correspondences(self):
        first, second = self.value(), self.value()
        second["points"] = second["points"][:1]
        result = exporter.comparison(first, second, True)["completeSavedMapPoints"]
        self.assertFalse(result["samePointCount"])
        self.assertFalse(result["coordinateBitMultisetsIdentical"])
        self.assertTrue(result["noNearestNeighborGeometryOrTruthAccuracyClaimed"])

    def test_different_time_hypotheses_remain_explicitly_not_same_scan_instants(self):
        first, second = self.value(), self.value()
        second["algorithm"] += .095
        result = exporter.comparison(first, second, False)
        self.assertTrue(result["differentHypothesisPosesReferToDifferentScanInstants"])
        self.assertGreater(result["algorithmTimeDifferenceSeconds"]["maximum"], .09)


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy and pinned JSON5 parser")
class ControlledLoader(unittest.TestCase):
    """All files below are isolated synthetic fixtures, never actual saved maps."""

    def fixture(self, root):
        import numpy as np

        root.mkdir()
        digest = ControlledConfiguration().baseline(root / "config")
        baseline = control.sha256(root / "config/trial-manifest.json")
        with patch.object(control, "PREPROCESS_CONFIG_SHA256", digest):
            config = control.make_control(root / "config", root / "control-config")
        control.save(root / "control-manifest.json", config)
        poses = np.repeat(np.eye(4)[None], 2, axis=0)
        poses[1, 0, 3] = 1
        arrays = {
            "poses": poses, "header_stamps_ns": np.array([100, 200], dtype=np.int64),
            "algorithm_stamps_relative_seconds": np.array([1000., 1001.]),
            "preprocess_odometry_pointcount_marginalized": np.array([[1., 2., 50, 0],
                                                                  [1., 2., 50, 1],
                                                                  [1., 2., 50, 1]]),
        }
        np.savez(root / "private-trajectory.npz", **arrays)
        for name in ("private-saved-points.f64", "private-reloaded-points.f64"):
            np.array([[1., 2., 3.], [4., 5., 6.]], dtype="<f8").tofile(root / name)
        for name in ("private-native.log", "private-reload.log"):
            (root / name).write_text("synthetic successful operation\n")
        map_root = root / "map"
        map_root.mkdir()
        for name in ("graph.bin", "values.bin"):
            (map_root / name).write_bytes(b"synthetic-not-real-gtsam")
        (map_root / "graph.txt").write_text(
            "num_submaps: 1\nnum_all_frames: 2\nnum_matching_cost_factors: 0\n")
        for name in ("odom_lidar.txt", "traj_lidar.txt", "odom_imu.txt", "traj_imu.txt"):
            (map_root / name).write_text("1000 0 0 0 0 0 0 1\n1001 1 0 0 0 0 0 1\n")
        submap = map_root / "000000"
        submap.mkdir()
        matrix = "1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1\n"
        metadata = "id: 0\n"
        for name in ("T_world_origin:", "T_origin_endpoint_L:", "T_origin_endpoint_R:", "T_lidar_imu:"):
            metadata += name + "\n" + matrix
        metadata += "imu_bias: 0 0 0 0 0 0\nframe_id: 2\nnum_frames: 2\n"
        for index in range(2):
            metadata += f"frame_{index}\nid: {index}\nstamp: {1000 + index}\n"
            metadata += "T_odom_lidar:\n" + matrix + "T_world_lidar:\n" + matrix
            metadata += "v_world_imu: 0 0 0\n"
        (submap / "data.txt").write_text(metadata)
        (root / "source-snapshot").mkdir()
        sources = {}
        for name in control.SOURCE_FILES:
            (root / "source-snapshot" / name).write_bytes((ROOT / "tools" / name).read_bytes())
            sources[name] = control.sha256(ROOT / "tools" / name)
        inventory = exporter.inventory_map(map_root)
        round_trip = exporter.compare_points(root / "private-saved-points.f64",
                                              root / "private-reloaded-points.f64", 2)
        boundaries = {name: False for name in control.BOUNDARIES}
        manifest_hash = control.sha256(root / "control-manifest.json")
        report = {
            "result": "controlled_full_map_saved_and_reloaded_not_geometry_acceptance",
            "dataset": "static", "timingHypothesis": "header_is_scan_start",
            "hostMachine": "x86_64", "pid": 101, "boundaries": boundaries,
            "allOriginalCapturesUnchangedBeforeAndAfter": True,
            "sourceVersions": {"numpy": "2.5.4", "rosbags": "0.11.9", "json5": "0.12.1"},
            "sourceSha256": sources, "controlManifestSha256": manifest_hash,
            "captureManifestSha256": "0" * 64,
            "mapping": {"finalizedFramesFedOnce": 2, "savedFrames": 2, "submaps": 1, "points": 2,
                        "submapTravel": 0., "minimumLoopTravel": 50.},
            "mapInventory": inventory, "roundTrip": round_trip, "totalWallSecondsIncludingReload": 2.,
        }
        base = {
            "effectiveConfigurationDirectory": "control-config", "baselineConfigurationDirectory": "config",
            "controlManifestSha256": manifest_hash,
            "privateTrajectorySha256": control.sha256(root / "private-trajectory.npz"),
            "privateNativeLogSha256": control.sha256(root / "private-native.log"),
            "result": "all_scans_accounted_for_not_quality_acceptance",
            "captureManifestSha256": "0" * 64, "processedScans": 3, "initializationScans": 1,
            "estimatedPoses": 2, "skippedUnbracketedScans": 1, "recordedScans": 4,
            "processingWallSeconds": 1., "processCpuSeconds": 2., "peakResidentMiB": 3.,
            "preprocessMillisecondsP50P95P99": [1., 2., 3.],
            "odometryMillisecondsP50P95P99": [1., 2., 3.], "combinedMillisecondsMaximum": 6.,
        }
        reloaded = {
            "pid": 102, "boundaries": boundaries.copy(), "mapInventory": inventory.copy(),
            "comparison": round_trip.copy(), "effectiveConfigFilesSha256": config["effectiveFilesSha256"],
        }
        return NS(path=root, report=report, base=base, reloaded=reloaded, baseline=baseline, arrays=arrays)

    def seal(self, value):
        control.save(value.path / "report.json", value.base)
        control.save(value.path / "reload-report.json", value.reloaded)
        value.report["filesSha256"] = {name: control.sha256(value.path / name) for name in (
            "report.json", "private-trajectory.npz", "private-native.log", "reload-report.json",
            "private-reload.log", "private-saved-points.f64", "private-reloaded-points.f64")}
        control.save(value.path / "controlled-mapping-report.json", value.report)
        return {"exitCode": 0, "reportSha256": control.sha256(value.path / "controlled-mapping-report.json")}

    def load(self, value, collection=None):
        return exporter.load_run(value.path, ("static", "header_is_scan_start", 1),
                                 self.seal(value) if collection is None else collection, value.baseline)

    def test_complete_synthetic_loader_recomputes_files_arrays_partition_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            value = self.fixture(Path(directory) / "run")
            loaded = self.load(value)
            self.assertEqual([2], loaded["members"])
            self.assertEqual((2, 3), loaded["points"].shape)
            self.assertEqual((3, 2), loaded["scanCounters"].shape)
            self.assertEqual(2, loaded["report"]["roundTrip"]["pointCount"])
            self.assertFalse(loaded["report"]["roundTrip"]["geometricAccuracyValidated"])

    def test_report_hash_and_every_artifact_hash_are_required(self):
        for kind in ("report", "trajectory", "source"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                value = self.fixture(Path(directory) / "run")
                collection = self.seal(value)
                if kind == "report":
                    collection["reportSha256"] = "f" * 64
                else:
                    path = (value.path / "private-trajectory.npz" if kind == "trajectory"
                            else value.path / "source-snapshot" / control.SOURCE_FILES[0])
                    with path.open("ab") as stream:
                        stream.write(b"synthetic changed bytes")
                with self.assertRaisesRegex(ValueError, "digest_mismatch"):
                    self.load(value, collection)

    def test_failed_wrong_condition_wrong_version_or_approved_report_is_rejected(self):
        mutations = (
            ("result", "failed_or_interrupted"), ("dataset", "dynamic"),
            ("hostMachine", "aarch64"), ("pid", 0), ("pid", True),
            ("allOriginalCapturesUnchangedBeforeAndAfter", False),
            ("sourceVersions", {"numpy": "unverified"}),
            ("boundaries", {name: True for name in control.BOUNDARIES}),
        )
        for field, content in mutations:
            with self.subTest(field=field, content=content), tempfile.TemporaryDirectory() as directory:
                value = self.fixture(Path(directory) / "run")
                value.report[field] = content
                with self.assertRaisesRegex(ValueError, "complete_controlled_mapping_report"):
                    self.load(value)

    def test_incomplete_artifact_hash_inventory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            value = self.fixture(Path(directory) / "run")
            self.seal(value)
            value.report["filesSha256"].pop("private-reload.log")
            (value.path / "controlled-mapping-report.json").write_text(json.dumps(value.report))
            collection = {"exitCode": 0, "reportSha256": control.sha256(value.path / "controlled-mapping-report.json")}
            with self.assertRaisesRegex(ValueError, "all_controlled_mapping_artifact_hashes"):
                self.load(value, collection)

    def test_scan_accounting_double_feed_same_process_and_control_swap_are_rejected(self):
        for kind in ("accounting", "double_feed", "same_process", "control_swap"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                value = self.fixture(Path(directory) / "run")
                if kind == "accounting":
                    value.base["recordedScans"] += 1
                elif kind == "double_feed":
                    value.report["mapping"]["finalizedFramesFedOnce"] += 1
                elif kind == "same_process":
                    value.reloaded["pid"] = value.report["pid"]
                else:
                    value.reloaded["effectiveConfigFilesSha256"] = {}
                with self.assertRaisesRegex(ValueError, "once_only_controlled_replay"):
                    self.load(value)

    def test_changed_map_or_claimed_round_trip_is_not_accepted(self):
        for kind in ("map", "round_trip"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                value = self.fixture(Path(directory) / "run")
                if kind == "map":
                    (value.path / "map/graph.bin").write_bytes(b"synthetic changed graph")
                else:
                    value.report["roundTrip"] = {**value.report["roundTrip"], "pointCount": 3}
                with self.assertRaisesRegex(ValueError, "round_trip_recomputation_mismatch"):
                    self.load(value)

    def test_rehashed_truncated_nonfinite_or_wrong_domain_arrays_are_rejected(self):
        import numpy as np

        for kind in ("missing", "unordered", "nan_pose", "truncated", "fractional_count"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                value = self.fixture(Path(directory) / "run")
                if kind == "missing":
                    del value.arrays["poses"]
                elif kind == "unordered":
                    value.arrays["header_stamps_ns"][:] = 100
                elif kind == "nan_pose":
                    value.arrays["poses"][0, 0, 3] = np.nan
                elif kind == "truncated":
                    value.arrays["algorithm_stamps_relative_seconds"] = np.array([1000.])
                else:
                    value.arrays["preprocess_odometry_pointcount_marginalized"][0, 2] = 1.5
                np.savez(value.path / "private-trajectory.npz", **value.arrays)
                value.base["privateTrajectorySha256"] = control.sha256(value.path / "private-trajectory.npz")
                with self.assertRaises(ValueError):
                    self.load(value)

    def test_summary_does_not_export_unrelated_private_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            value = self.fixture(Path(directory) / "run")
            loaded = self.load(value)
            for item in (loaded, loaded["report"], loaded["report"]["mapping"], loaded["base"]):
                item["privateProfile"] = "synthetic_private_marker"
            summary = exporter.run_summary(loaded)
            self.assertNotIn("synthetic_private_marker", json.dumps(summary))
            self.assertNotIn("privateProfile", json.dumps(summary))
            self.assertEqual(1., summary["estimatedMotionNotTruth"]["accumulated3dPositionStepsRawUnits"])

    def test_existing_public_output_is_preserved_without_running_export(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "export") as export:
            output = Path(directory) / "evidence.json"
            output.write_text("preserve synthetic prior evidence")
            args = ["export"]
            for name in ("results", "work", "adapter", "mapping-adapter", "data-root"):
                args += ["--" + name, directory]
            with patch.object(sys, "argv", args + ["--output", str(output)]):
                with self.assertRaisesRegex(ValueError, "existing_controlled_map_evidence"):
                    exporter.main()
            export.assert_not_called()
            self.assertEqual("preserve synthetic prior evidence", output.read_text())


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy and pinned JSON5 parser")
class ControlledViews(unittest.TestCase):
    def test_output_must_be_new_and_resolve_inside_ignored_local_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(renderer, "ROOT", Path(directory)):
            root = Path(directory)
            (root / ".local").mkdir()
            destination = root / ".local/new-preview"
            self.assertEqual(destination, renderer.new_private_output(destination))
            destination.mkdir()
            for output in (destination, root / "docs/preview", root / ".local/../../outside"):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, "new_ignored_private"):
                    renderer.new_private_output(output)
            (root / ".local/link").symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "new_ignored_private"):
                renderer.new_private_output(root / ".local/link/preview")

    def test_view_alignment_is_only_first_pose_rigid_transform_not_scale_fit(self):
        import numpy as np

        first = np.eye(4)
        first[:2, :2] = [[0, -1], [1, 0]]
        first[:3, 3] = [4, 5, 6]
        second = first.copy()
        second[:3, 3] += [0, 2, 0]
        points = np.array([[4., 5., 6.], [4., 8., 6.]])
        original = points.copy()
        aligned, poses = renderer.aligned_for_view({"saved": np.stack((first, second)), "points": points})
        np.testing.assert_allclose(aligned, [[0, 0, 0], [3, 0, 0]])
        np.testing.assert_allclose(poses, [[0, 0, 0], [2, 0, 0]])
        np.testing.assert_array_equal(points, original)

    def test_panel_receives_all_points_and_bounds_include_distant_returns_and_poses(self):
        import numpy as np

        points = np.array([[0., 0., 0.], [50., 60., 70.]])
        poses = np.array([[-10., -20., 0.], [0., 0., 0.]])
        bounds = renderer.padded_bounds([(points, poses)])
        self.assertLess(bounds[0][0], -10)
        self.assertLess(bounds[0][1], -20)
        self.assertGreater(bounds[1][0], 50)
        self.assertGreater(bounds[1][1], 60)
        axis = Mock()
        axis.spines = {}
        renderer.point_panel(axis, points, poses, (0, 1), bounds, (0, 70))
        np.testing.assert_array_equal(axis.scatter.call_args_list[0].args[0], points[:, 0])
        np.testing.assert_array_equal(axis.scatter.call_args_list[0].args[1], points[:, 1])
        np.testing.assert_array_equal(axis.scatter.call_args_list[0].kwargs["c"], points[:, 2])
        axis.set_aspect.assert_called_once_with("equal", adjustable="box")

    @unittest.skipUnless(importlib.util.find_spec("matplotlib"), "requires isolated matplotlib")
    def test_three_synthetic_figures_are_nonblank_and_have_stable_dimensions(self):
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        from PIL import Image

        poses = np.repeat(np.eye(4)[None], 2, axis=0)
        poses[1, 0, 3] = 1
        value = {"saved": poses, "points": np.array([[0., 0., 0.], [1., 2., 3.]]),
                 "report": {"mapping": {"points": 2, "submaps": 1}}}
        loaded = {control.label(*condition): value for condition in control.CONDITIONS}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for dataset in ("static", "dynamic"):
                renderer.render_repeatability(loaded, dataset, root / (dataset + ".png"))
            renderer.render_orthographic(loaded, root / "orthographic.png")
            for image_path in root.glob("*.png"):
                with Image.open(image_path) as image:
                    self.assertEqual((1950, 1300), image.size)
                    self.assertGreater(np.std(np.asarray(image.convert("RGB")).astype(float)), 5)
            self.assertEqual(3, len(list(root.glob("*.png"))))


class ControlledBoundaries(unittest.TestCase):
    def test_uses_existing_native_implementation_without_mutating_old_runner(self):
        text = (ROOT / "tools/run_glim_controlled_mapping.py").read_text()
        self.assertIn("class NativeControl(replay.Native)", text)
        self.assertIn("mapping.MappingLibrary", text)
        self.assertIn("replay.run(args, native_factory=NativeControl", text)
        self.assertIn("timeout=300", text)
        self.assertIn("timeout=180", text)
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                         else [node.module])
                self.assertTrue(all(name.split(".")[0] not in {"rclpy", "paramiko", "socket", "requests"}
                                    for name in names))
        evidence = json.loads((ROOT / "docs/slam-glim-mapping-evidence.json").read_text())
        for name, digest in evidence["adapterBuild"]["sources"].items():
            self.assertEqual(digest, hashlib.sha256((ROOT / "tools/glim-mapping" / name).read_bytes()).hexdigest())

    def test_exporter_and_renderer_do_not_invoke_network_ros_or_native_execution(self):
        for name in ("export_glim_controlled_mapping.py", "render_glim_controlled_maps.py"):
            text = (ROOT / "tools" / name).read_text()
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = ([item.name for item in node.names] if isinstance(node, ast.Import)
                               else [node.module])
                    for module in modules:
                        self.assertNotIn(module.split(".")[0],
                                         {"subprocess", "ctypes", "rclpy", "socket", "paramiko", "requests"})
            for token in ("cmd_vel", "McLocomotion", "create_publisher", "ctypes.CDLL"):
                self.assertNotIn(token, text)

    def test_public_evidence_keeps_all_conditions_pairs_and_unaccepted_boundaries(self):
        evidence = json.loads((ROOT / "docs/slam-controlled-mapping-evidence.json").read_text())
        names = {f"{dataset}-{hypothesis}-repeat{repeat}"
                 for dataset in ("static", "dynamic") for hypothesis in ("start", "end")
                 for repeat in (1, 2, 3)}
        self.assertEqual(names, set(evidence["runs"]))
        self.assertEqual(12, evidence["freshProcessCount"])
        self.assertEqual(12, len(evidence["withinConditionPairs"]))
        self.assertEqual(18, len(evidence["crossTimingHypothesisPairsNotSameScanInstants"]))
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        self.assertTrue(evidence["allOriginalCapturesAndCompleteOutputsUnchangedAtExport"])
        self.assertEqual([{"file": "config_preprocess.json", "path": ["preprocess", "num_threads"],
                           "before": 2, "after": 1}], evidence["semanticParameterChanges"])
        for value in evidence["withinConditionPairs"].values():
            self.assertTrue(value["sameTimingHypothesis"])
            self.assertTrue(value["savedSubmapPartition"]["identicalPartition"])
            self.assertEqual(0, value["recordedPreprocessingCounters"]["preparedPointCountDifferentScans"])
            self.assertTrue(value["recordedPreprocessingCounters"]["equalCountsAreNotProofOfIdenticalPreparedPoints"])
            self.assertFalse(value["fullOnlinePoseArraysBitIdentical"])
            self.assertEqual(0, value["savedGlobalLidarPosesRoundedToSixDecimals"]
                             ["unadjusted"]["positionDifferenceRawUnits"]["maximum"])
            self.assertFalse(value["completeSavedMapPoints"]["orderedCoordinateBitsIdentical"])
            self.assertFalse(value["completeSavedMapPoints"]["coordinateBitMultisetsIdentical"])
        for value in evidence["crossTimingHypothesisPairsNotSameScanInstants"].values():
            self.assertTrue(value["differentHypothesisPosesReferToDifferentScanInstants"])
            self.assertGreater(value["algorithmTimeDifferenceSeconds"]["maximum"], .09)
        for value in evidence["runs"].values():
            self.assertEqual(value["mapping"]["points"], value["roundTrip"]["pointCount"])
            self.assertLess(value["roundTrip"]["maximumPointDisplacementRawUnits"],
                            value["roundTrip"]["predeclaredToleranceRawUnits"])

    def test_public_evidence_source_digests_and_privacy(self):
        evidence = json.loads((ROOT / "docs/slam-controlled-mapping-evidence.json").read_text())
        for filename, key in (("run_glim_controlled_mapping.py", "runnerSourceSha256"),
                              ("export_glim_controlled_mapping.py", "exporterSourceSha256")):
            self.assertEqual(hashlib.sha256((ROOT / "tools" / filename).read_bytes()).hexdigest(),
                             evidence[key])
        for filename, digest in evidence["sourceSha256"].items():
            self.assertEqual(hashlib.sha256((ROOT / "tools" / filename).read_bytes()).hexdigest(), digest)
        for filename, digest in evidence["nativeMappingSourcesSha256"].items():
            self.assertEqual(hashlib.sha256((ROOT / "tools/glim-mapping" / filename).read_bytes()).hexdigest(), digest)
        text = json.dumps(evidence)
        for token in ('"poses"', '"online"', '"saved"', '"header_stamps_ns"', '"T_lidar_imu"',
                      '"password"', '"hostname"', '"credential"', "/home/", "/mnt/", "E:\\\\"):
            self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
