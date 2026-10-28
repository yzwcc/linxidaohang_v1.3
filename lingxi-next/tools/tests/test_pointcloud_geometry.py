"""Synthetic distances and target reports, never real clouds or claimed robot results."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
AVAILABLE = (sys.platform == "linux" and all(importlib.util.find_spec(name) is not None
                                           for name in ("numpy", "scipy", "json5")))


def load(name, path=None):
    spec = importlib.util.spec_from_file_location(name, path or ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(module)
    return module


if AVAILABLE:
    import numpy as np
    geometry = load("compare_pointcloud_geometry")
    exporter = load("export_glim_controlled_target")
    fixtures = load("controlled_synthetic_fixtures", ROOT / "tools/tests/test_slam_controlled_mapping.py")


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy, SciPy and JSON5")
class PointGeometry(unittest.TestCase):
    def test_all_query_distances_agree_with_complete_brute_force_synthetic_arrays(self):
        first = np.array([[0., 0., 0.], [1., 2., 3.], [9., -2., 1.], [4., 5., 6.]])
        second = np.array([[1., 1., 1.], [10., -2., 2.], [4., 4., 4.]])
        distances = np.linalg.norm(first[:, None] - second[None, :], axis=2)
        result = geometry.compare_clouds(first, second)
        for key, values in (("referenceToCandidateRawUnits", distances.min(axis=1)),
                            ("candidateToReferenceRawUnits", distances.min(axis=0))):
            self.assertEqual(len(values), result[key]["pointsCompared"])
            self.assertAlmostEqual(float(values.max()), result[key]["maximum"])
            self.assertAlmostEqual(float(np.percentile(values, 95)), result[key]["p95"])
            self.assertAlmostEqual(float(np.percentile(values, 99)), result[key]["p99"])
            self.assertAlmostEqual(float(np.sqrt(np.mean(values ** 2))), result[key]["rms"])
        self.assertTrue(result["exactEuclideanNearestNeighbors"])
        self.assertFalse(result["accuracyAgainstTruthMeasured"])

    def test_order_changes_and_duplicate_multiplicity_do_not_become_identity_claims(self):
        first = np.array([[0., 0., 0.], [1., 2., 3.], [1., 2., 3.]])
        second = first[[1, 0]]
        value = geometry.compare_clouds(first, second)
        self.assertEqual(0, value["referenceToCandidateRawUnits"]["maximum"])
        self.assertEqual(0, value["candidateToReferenceRawUnits"]["maximum"])
        self.assertFalse(value["multiplicityOrOneToOneCorrespondenceProven"])
        self.assertEqual(3, value["referenceToCandidateRawUnits"]["pointsCompared"])
        self.assertEqual(2, value["candidateToReferenceRawUnits"]["pointsCompared"])

    def test_asymmetric_far_return_is_not_filtered_or_hidden(self):
        value = geometry.compare_clouds(np.array([[0., 0., 0.], [100., 0., 0.]]), np.zeros((1, 3)))
        self.assertEqual(100, value["referenceToCandidateRawUnits"]["maximum"])
        self.assertEqual(0, value["candidateToReferenceRawUnits"]["maximum"])
        self.assertTrue(value["allPointsWithoutSamplingOrOutlierRemoval"])

    def test_empty_wrong_shape_and_nonfinite_inputs_are_rejected(self):
        for array in (np.empty((0, 3)), np.ones((2, 2)), np.full((2, 3), np.nan),
                      np.full((2, 3), np.inf), np.ones(3)):
            with self.subTest(shape=array.shape), self.assertRaisesRegex(ValueError, "complete_xyz"):
                geometry.compare_clouds(array, np.zeros((1, 3)))

    def test_only_initial_rigid_offset_is_removed_not_scale_change(self):
        first = {"saved": np.eye(4)[None], "points": np.array([[0., 0., 0.], [1., 0., 0.]])}
        pose = np.eye(4)
        pose[0, 3] = 10
        second = {"saved": pose[None], "points": np.array([[10., 0., 0.], [12., 0., 0.]])}
        result = geometry.compare_maps(first, second)
        self.assertEqual(11, result["unadjusted"]["candidateToReferenceRawUnits"]["maximum"])
        self.assertEqual(1, result["firstSavedLidarPoseRigidAlignmentOnly"]["candidateToReferenceRawUnits"]["maximum"])
        self.assertFalse(result["scaleFitted"])
        self.assertFalse(result["bestFitCloudRegistrationUsed"])
        self.assertFalse(result["geometryAcceptanceThresholdSelected"])

    def test_initial_rotation_is_removed_without_mutating_source_points(self):
        transform = np.eye(4)
        transform[:2, :2] = [[0, -1], [1, 0]]
        transform[:3, 3] = [4, 5, 6]
        points = np.array([[0., 0., 0.], [1., 2., 3.]])
        transformed = points @ transform[:3, :3].T + transform[:3, 3]
        result = geometry.compare_maps({"saved": np.eye(4)[None], "points": points},
                                       {"saved": transform[None], "points": transformed})
        self.assertEqual(0, result["firstSavedLidarPoseRigidAlignmentOnly"]["referenceToCandidateRawUnits"]["maximum"])
        np.testing.assert_array_equal(points, [[0., 0., 0.], [1., 2., 3.]])

    def test_bad_first_pose_is_not_used_as_alignment(self):
        pose = np.eye(4)
        pose[0, 0] = 2
        with self.assertRaisesRegex(ValueError, "invalid_estimated_pose"):
            geometry.compare_maps({"saved": pose[None], "points": np.zeros((1, 3))},
                                  {"saved": np.eye(4)[None], "points": np.zeros((1, 3))})


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy, SciPy and JSON5")
class TargetGeometryLoader(unittest.TestCase):
    def test_only_rms_reduction_allows_two_ulps_not_changed_count_maximum_or_tolerance(self):
        original = {"pointCount": 2, "maximumPointDisplacementRawUnits": 1e-6,
                    "rmsPointDisplacementRawUnits": 2e-7, "predeclaredToleranceRawUnits": 1e-4,
                    "samePointOrderCompared": True, "geometricAccuracyValidated": False}
        changed = original.copy()
        changed["rmsPointDisplacementRawUnits"] = float(np.nextafter(2e-7, np.inf))
        self.assertTrue(exporter.matching_round_trip(original, changed))
        for field in ("pointCount", "maximumPointDisplacementRawUnits", "predeclaredToleranceRawUnits"):
            changed = original.copy()
            changed[field] += 1
            self.assertFalse(exporter.matching_round_trip(original, changed))
        for value in (2e-7 + 1e-12, float("nan"), float("inf"), -1., True):
            changed = {**original, "rmsPointDisplacementRawUnits": value}
            self.assertFalse(exporter.matching_round_trip(original, changed))
        original["rmsPointDisplacementRawUnits"] = 0.
        changed = {**original, "rmsPointDisplacementRawUnits": float(np.nextafter(0., 1.))}
        self.assertFalse(exporter.matching_round_trip(original, changed))

    def fixture(self, directory):
        fixture = fixtures.ControlledLoader().fixture(Path(directory) / "run")
        target = {"machine": "aarch64", "python": "synthetic", "numpy": "synthetic"}
        libraries = {name: "1" * 64 for name in ("libglim.so", "libodometry_estimation_cpu.so",
                                                "liblingxi_glim_offline.so", "liblingxi_glim_mapping.so")}
        fixture.integrity = {"datasets": {"static": {"topicCounts": {"cloud": 4, "imu": 10}}}}
        fixture.bundle_hash = "2" * 64
        fixture.manifest = {
            "controlManifestSha256": fixture.report["controlManifestSha256"],
            "files": {"tools/glim_target_controlled_mapping.py": {"sha256": "3" * 64},
                      "tools/glim_target_replay.py": {"sha256": "4" * 64}},
        }
        fixture.manifest["files"].update({
            "config/" + name: {"sha256": digest}
            for name, digest in fixtures.control.config_hashes(fixture.path / "config").items()
        })
        fixture.libraries = libraries
        fixture.report.update({
            "result": "complete_controlled_arm64_map_saved_and_reloaded_not_geometry_acceptance",
            "target": target, "sourceCommits": exporter.PINS, "replayManifestSha256": fixture.bundle_hash,
            "runnerSha256": "3" * 64, "boundaries": {name: False for name in exporter.BOUNDARIES},
            "actualLoadedLibrariesSha256": libraries.copy(),
        })
        fixture.base.update({
            "result": "all_recorded_scans_accounted_for_on_arm64_not_quality_acceptance",
            "dataset": "static", "timingHypothesis": "header_is_scan_start",
            "target": target, "sourceCommits": exporter.PINS, "replayManifestSha256": fixture.bundle_hash,
            "runnerSha256": "4" * 64, "controlledMappingRunnerSha256": "3" * 64,
            "captureIntegrity": fixture.integrity, "captureIntegrityAfterReplay": fixture.integrity,
            "reader": "rosbag2_py_humble_sequential_reader_no_ros_init", "mapping": fixture.report["mapping"],
            "imuSamplesInserted": 3,
            "preprocessMillisecondsP50P95P99": [1., 1., 1.],
            "odometryMillisecondsP50P95P99": [2., 2., 2.], "combinedMillisecondsMaximum": 3.,
            "estimatedMotionNotGroundTruth": {
                "accumulatedPositionStepsRawUnits": 1., "endToStartDistanceRawUnits": 1.,
                "maximumConsecutivePositionStepRawUnits": 1.,
            },
            "checks": {"recordedDataProcessedOnArm64": True, "allScansAccountedFor": True,
                       "originalCapturesUnchanged": True},
            "boundaries": {name: name == "globalMappingExecuted" for name in exporter.BASE_BOUNDARIES},
        })
        fixture.reloaded.update({
            "result": "independent_controlled_arm64_map_reloaded_not_relocalization",
            "machine": "aarch64", "effectiveConfiguration": {
                name[7:]: entry["sha256"] for name, entry in fixture.manifest["files"].items()
                if name.startswith("config/")},
            "boundaries": fixture.report["boundaries"].copy(),
        })
        return fixture

    def load(self, value):
        helper = fixtures.ControlledLoader()
        helper.seal(value)
        (value.path / "controlled-report.json").write_text(json.dumps(value.report))
        collection = {"exitCode": 0, "reportSha256": exporter.sha256(value.path / "controlled-report.json")}
        return exporter.load_target(value.path, ("static", "header_is_scan_start", 1), collection,
                                    value.manifest, value.bundle_hash, value.integrity, value.libraries)

    def test_complete_synthetic_target_report_recomputes_map_arrays_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            value = self.load(self.fixture(directory))
            self.assertEqual([2], value["members"])
            self.assertEqual((2, 3), value["points"].shape)
            self.assertEqual(0, value["report"]["roundTrip"]["maximumPointDisplacementRawUnits"])

    def test_false_empty_checks_wrong_reader_and_recorded_count_are_rejected(self):
        for kind in ("checks", "boundaries", "reader", "recorded_count"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                value = self.fixture(directory)
                if kind in ("checks", "boundaries"):
                    value.base[kind] = {}
                elif kind == "reader":
                    value.base["reader"] = "synthetic_unverified_reader"
                else:
                    value.integrity["datasets"]["static"]["topicCounts"]["cloud"] += 1
                with self.assertRaisesRegex(ValueError, "scan_accounting"):
                    self.load(value)

    def test_unverified_native_library_or_same_reload_process_is_rejected(self):
        for kind in ("library", "same_pid", "pid_zero"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                value = self.fixture(directory)
                if kind == "library":
                    value.report["actualLoadedLibrariesSha256"] = {"unexpected.so": "0" * 64}
                else:
                    value.reloaded["pid"] = value.report["pid"] if kind == "same_pid" else 0
                with self.assertRaises(ValueError):
                    self.load(value)

    def test_summary_excludes_added_private_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            value = self.load(self.fixture(directory))
            for group in (value["report"], value["report"]["mapping"], value["base"], value["report"]["target"],
                          value["report"]["mapInventory"], value["report"]["roundTrip"],
                          value["roundTrip"],
                          value["report"]["filesSha256"], value["aggregate"]["counts"],
                          value["aggregate"]["timingsNotRealtimeAcceptance"],
                          value["aggregate"]["estimatedMotionNotGroundTruth"]):
                group["privateProfile"] = "SYNTHETIC_PRIVATE_MARKER"
            self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", json.dumps(exporter.summary(value)))

    def test_false_costs_motion_or_imu_counts_are_rejected(self):
        for kind in ("timings", "motion", "imu", "total_wall"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                value = self.fixture(directory)
                if kind == "timings":
                    value.base["combinedMillisecondsMaximum"] += 1
                elif kind == "motion":
                    value.base["estimatedMotionNotGroundTruth"]["endToStartDistanceRawUnits"] += 1
                elif kind == "imu":
                    value.base["imuSamplesInserted"] = 0
                else:
                    value.report["totalWallSecondsIncludingReload"] = float("nan")
                with self.assertRaises(ValueError):
                    self.load(value)

    def test_existing_output_is_preserved_without_geometry_or_input_access(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "export") as run:
            output = Path(directory) / "existing.json"
            output.write_text("synthetic preserve")
            args = ["export"]
            for name in ("bundle", "parent-bundle", "core-bundle", "results", "host-results", "data-root"):
                args += ["--" + name, directory]
            with patch.object(sys, "argv", args + ["--output", str(output)]):
                with self.assertRaisesRegex(ValueError, "preserved"):
                    exporter.main()
            run.assert_not_called()


class GeometryBoundaries(unittest.TestCase):
    def test_comparison_and_export_do_not_execute_ros_native_code_or_network(self):
        for name in ("compare_pointcloud_geometry.py", "export_glim_controlled_target.py"):
            text = (ROOT / "tools" / name).read_text()
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module]
                    for module in modules:
                        self.assertNotIn(module.split(".")[0],
                                         {"subprocess", "ctypes", "socket", "paramiko", "requests", "rclpy"})


if __name__ == "__main__":
    unittest.main()
