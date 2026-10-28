"""Isolated synthetic distance aggregation fixtures; no real cloud or robot access."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ModuleNotFoundError:
    np = None

ROOT = Path(__file__).parents[2]
if np is not None and sys.platform == "linux":
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec = importlib.util.spec_from_file_location("lidar_ranges", ROOT / "tools/inspect_lidar_ranges.py")
        ranges = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ranges)


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy environment")
class RangeAggregates(unittest.TestCase):
    def message(self, stamp):
        return NS(header=NS(stamp=NS(sec=stamp, nanosec=0), frame_id="lidar_chest_front"),
                  width=4, height=1)

    def test_histogram_includes_zero_boundaries_and_far_points_without_clipping(self):
        values = ranges.Distances()
        values.add(np.array([0, .5, 1, 2, 5, 10, 20, 30, 50, 100, 200]))
        report = values.summary()
        self.assertEqual(11, report["finitePointCount"])
        self.assertEqual(1, report["zeroDistancePointCount"])
        self.assertEqual(200, report["maximum"])
        self.assertEqual([2, 1, 1, 1, 1, 1, 1, 1, 2],
                         [item["count"] for item in report["histogramLowerInclusiveUpperExclusive"]])
        self.assertIsNone(report["histogramLowerInclusiveUpperExclusive"][-1]["upper"])
        self.assertIn("not_validated", report["distanceUnits"])
        self.assertTrue(report["perScanPercentilesAreNotPooledPointPercentiles"])

    def test_unequal_scans_use_point_weighted_counts_not_scan_weighted_histograms(self):
        values = ranges.Distances()
        values.add([1])
        values.add([20] * 9)
        report = values.summary()
        self.assertEqual(10, report["finitePointCount"])
        self.assertEqual(9, report["histogramLowerInclusiveUpperExclusive"][5]["count"])

    def test_nonfinite_negative_empty_and_multidimensional_distances_are_rejected(self):
        for values in ([], [np.inf], [np.nan], [-1], [[1, 2]]):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, "finite_nonnegative"):
                ranges.Distances().add(values)
        with self.assertRaisesRegex(ValueError, "recorded_distances"):
            ranges.Distances().summary()

    def test_radial_horizontal_and_nonfinite_counts_remain_distinct(self):
        points = np.array([[0., 0., 4.], [3., 4., 0.], [0., 0., 0.]])
        with patch.object(ranges, "cloud_arrays", return_value=(points, np.array([0., .01, .02]), 1)):
            report = ranges.summarize([self.message(1), self.message(2)], 2)
        self.assertEqual(8, report["sourcePointCount"])
        self.assertEqual(2, report["nonfiniteCoordinatePointCount"])
        self.assertEqual(6, report["radial3d"]["finitePointCount"])
        self.assertEqual(2, report["radial3d"]["zeroDistancePointCount"])
        self.assertEqual(4, report["horizontalXY"]["zeroDistancePointCount"])
        self.assertEqual(1, report["recordedHeaderSpanSeconds"])
        self.assertTrue(report["noImuOrScanCoverageGateApplied"])
        self.assertTrue(report["noRangeOrZeroReturnFilterApplied"])
        self.assertNotIn('"points"', json.dumps(report))

    def test_missing_frames_reordered_stamps_wrong_frame_and_missing_points_fail(self):
        points = np.zeros((4, 3))
        with patch.object(ranges, "cloud_arrays", return_value=(points, np.arange(4), 0)):
            with self.assertRaisesRegex(ValueError, "all_declared"):
                ranges.summarize([self.message(1), self.message(2)], 3)
            with self.assertRaisesRegex(ValueError, "ordered_expected"):
                ranges.summarize([self.message(2), self.message(1)], 2)
            message = self.message(1)
            message.header.frame_id = "unexpected"
            with self.assertRaisesRegex(ValueError, "ordered_expected"):
                ranges.summarize([message, self.message(2)], 2)
        with patch.object(ranges, "cloud_arrays", return_value=(points[:3], np.arange(3), 0)):
            with self.assertRaisesRegex(ValueError, "all_declared"):
                ranges.summarize([self.message(1), self.message(2)], 2)

    def test_existing_output_and_output_inside_recordings_are_rejected_before_reading(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ranges, "verify") as verify:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "preserved"):
                ranges.run(root / "missing.json", root, root)
            with self.assertRaisesRegex(ValueError, "inside_recordings"):
                ranges.run(root / "missing.json", root, root / "new.json")
            verify.assert_not_called()

    def test_every_capture_is_verified_before_and_after_reading_all_clouds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            manifest = {"datasets": {name: {"directoryName": name, "topicCounts": {"cloud": 2}}
                                     for name in ("static", "dynamic")}}
            (root / "manifest.json").write_text(json.dumps(manifest))
            messages = [self.message(1), self.message(2)]
            points = np.ones((4, 3))
            with (patch.object(ranges, "verify") as verify,
                  patch.object(ranges, "recorded_clouds", return_value=messages) as clouds,
                  patch.object(ranges, "cloud_arrays", return_value=(points, np.arange(4), 0))):
                report = ranges.run(root / "manifest.json", root / "data", root / "new.json")
            self.assertEqual(2, verify.call_count)
            self.assertEqual(2, clouds.call_count)
            self.assertTrue(report["allOriginalCapturesUnchangedBeforeAndAfter"])
            self.assertEqual(report, json.loads((root / "new.json").read_text()))
            self.assertTrue(all(value is False for value in report["boundaries"].values()))


class RangeBoundary(unittest.TestCase):
    def test_only_reads_recorded_clouds_and_contains_no_control_or_mapping(self):
        text = (ROOT / "tools/inspect_lidar_ranges.py").read_text()
        imports = [node for node in ast.walk(ast.parse(text))
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        names = [node.module if isinstance(node, ast.ImportFrom) else item.name
                 for node in imports for item in (node.names if isinstance(node, ast.Import) else [None])]
        for name in ("rclpy", "paramiko", "socket", "subprocess", "kiss_icp.kiss_icp"):
            self.assertNotIn(name, names)
        self.assertIn("allOriginalCapturesUnchangedBeforeAndAfter", text)
        self.assertIn('output.open("x"', text)

    def test_public_range_evidence_preserves_scope_provenance_and_privacy(self):
        evidence = json.loads((ROOT / "docs/lidar-range-evidence.json").read_text())
        for key, filename in (("runnerSourceSha256", "inspect_lidar_ranges.py"),
                              ("decoderSourceSha256", "run_kiss_offline_trial.py")):
            self.assertEqual(hashlib.sha256((ROOT / "tools" / filename).read_bytes()).hexdigest(), evidence[key])
        self.assertEqual(hashlib.sha256((ROOT / "docs/slam-capture-evidence.json").read_bytes()).hexdigest(),
                         evidence["captureManifestSha256"])
        self.assertTrue(evidence["allOriginalCapturesUnchangedBeforeAndAfter"])
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        for name, value in evidence["datasets"].items():
            self.assertEqual(353 if name == "static" else 1232, value["recordedScans"])
            self.assertEqual(0, value["nonfiniteCoordinatePointCount"])
            for kind in ("radial3d", "horizontalXY"):
                result = value[kind]
                self.assertEqual(value["sourcePointCount"], result["finitePointCount"])
                self.assertEqual(result["finitePointCount"],
                                 sum(item["count"] for item in result["histogramLowerInclusiveUpperExclusive"]))
                self.assertGreater(result["maximum"], 30)
                self.assertIn("not_validated", result["distanceUnits"])
        for token in ('"points"', '"header_stamps_ns"', '"T_lidar_imu"', '"password"',
                      '"hostname"', '"username"', '"poses"'):
            self.assertNotIn(token, json.dumps(evidence))


if __name__ == "__main__":
    unittest.main()
