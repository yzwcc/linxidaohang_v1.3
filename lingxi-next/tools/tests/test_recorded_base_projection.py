"""Same-input time joins and missing-TF accounting on isolated synthetic data."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in (
    "numpy", "scipy", "rosbags", "ruamel", "json5"))
if AVAILABLE:
    sys.path.insert(0, str(ROOT / "tools"))
    import numpy as np
    from project_recorded_base_poses import TransformHistory, project_arrays


@unittest.skipUnless(AVAILABLE, "requires isolated offline recording dependencies")
class RecordedBaseProjection(unittest.TestCase):
    EPOCH = 1_700_000_000_000_000_001

    def inputs(self):
        history = TransformHistory()
        tf = np.eye(4)
        tf[0, 3] = .25
        history.add("base_link", "lidar_chest_front", self.EPOCH, tf, static=False)
        history.add("base_link", "lidar_chest_front", self.EPOCH + 10_000_000, tf, static=False)
        headers = np.array([self.EPOCH, self.EPOCH + 5_000_000, self.EPOCH + 10_000_001], dtype=np.int64)
        algorithms = (headers - self.EPOCH) / 1e9 + 1000.
        context = history, dict.fromkeys(map(int, headers), 90_000_000), self.EPOCH
        poses = np.repeat(np.eye(4)[None], len(headers), axis=0)
        poses[:, 0, 3] = [1., 2., 3.]
        return poses, headers, algorithms, context

    def test_valid_rows_project_and_uncovered_rows_have_no_substitute_pose(self):
        poses, headers, algorithms, context = self.inputs()
        summary, values = project_arrays(poses, headers, algorithms, context, "header_is_scan_start", 1.)
        self.assertEqual((3, 2, 1), tuple(summary[key] for key in (
            "nativePoses", "projectedBasePoses", "unavailablePoses")))
        self.assertEqual({"outside_dynamic_tf_coverage": 1}, summary["unavailableReasons"])
        np.testing.assert_array_equal(values["native_pose_rows"], [0, 1])
        np.testing.assert_allclose(values["odom_from_base_metres_at_declared_scale"][:, 0, 3], [.75, 1.75])
        np.testing.assert_array_equal(values["effective_sensor_stamps_ns"], headers[:2])
        self.assertLess(summary["maximumReconstructionElementError"], 1e-12)

    def test_time_or_recording_mismatch_does_not_silently_skip_native_pose(self):
        for kind in ("algorithm", "header"):
            poses, headers, algorithms, context = self.inputs()
            if kind == "algorithm":
                algorithms[0] += .001
            else:
                headers[0] -= 1
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                project_arrays(poses, headers, algorithms, context, "header_is_scan_start", 1.)

    def test_end_hypothesis_does_not_use_header_time_transform(self):
        poses, headers, algorithms, context = self.inputs()
        algorithms -= .09
        with self.assertRaisesRegex(ValueError, "accounting"):
            project_arrays(poses, headers, algorithms, context, "header_is_scan_end", 1.)

    def test_interpolation_gap_rejection_remains_explicit(self):
        poses, headers, algorithms, context = self.inputs()
        history, scans, origin = context
        history.dynamic[("base_link", "lidar_chest_front")].pop(self.EPOCH + 10_000_000)
        history.add("base_link", "lidar_chest_front", self.EPOCH + 100_000_000, np.eye(4), static=False)
        summary, values = project_arrays(poses, headers, algorithms, (history, scans, origin),
                                        "header_is_scan_start", 1.)
        self.assertEqual(1, summary["projectedBasePoses"])
        self.assertEqual({"dynamic_tf_interpolation_gap_exceeded": 2}, summary["unavailableReasons"])


if __name__ == "__main__":
    unittest.main()
