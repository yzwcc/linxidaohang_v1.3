"""Synthetic TF, factory-file and warning semantics; no robot or real coordinates."""
import importlib.util
from pathlib import Path
import platform
import sys
import unittest


ROOT = Path(__file__).parents[2]
AVAILABLE = (platform.system() == "Linux" and importlib.util.find_spec("numpy") is not None
             and importlib.util.find_spec("scipy") is not None
             and importlib.util.find_spec("json5") is not None)
if AVAILABLE:
    sys.path.insert(0, str(ROOT / "tools"))
    import numpy as np
    from scipy.spatial.transform import Rotation
    import audit_slam_calibration as audit


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy/SciPy/rosbags dependencies")
class CalibrationAudit(unittest.TestCase):
    BASE = 1_700_000_000_000_000_000

    def transform(self, x=0, yaw=0):
        return audit.matrix([x, 0, 0], Rotation.from_euler("z", yaw, degrees=True).as_quat())

    def history(self):
        history = audit.TransformHistory()
        history.add("base", "joint", 0, self.transform(1, 90), static=True)
        history.add("joint", "sensor", self.BASE, self.transform(0, 0), static=False)
        history.add("joint", "sensor", self.BASE + 10_000_000, self.transform(2, 90), static=False)
        return history

    def test_composition_has_child_to_parent_direction_not_reversed_matrix_order(self):
        history = self.history()
        path = history.path("base", "sensor")
        value, gap = history.query(path, self.BASE)
        np.testing.assert_allclose(value @ [1, 0, 0, 1], [1, 1, 0, 1], atol=1e-12)
        self.assertEqual(0, gap)
        self.assertEqual(["base", "joint", "sensor"], path)

    def test_integer_epoch_interpolation_uses_slerp_and_translation_not_nearest_pose(self):
        history = self.history()
        value, gap = history.query(history.path("base", "sensor"), self.BASE + 5_000_000)
        expected = self.transform(1, 90) @ self.transform(1, 45)
        np.testing.assert_allclose(value, expected, atol=1e-12)
        self.assertEqual(10_000_000, gap)
        value, _ = history.query(history.path("base", "sensor"), self.BASE + 1)
        self.assertGreater(value[1, 3], 0)

    def test_no_extrapolation_no_latest_fallback_and_no_large_gap_interpolation(self):
        history = self.history()
        path = history.path("base", "sensor")
        for stamp in (self.BASE - 1, self.BASE + 10_000_001):
            value, reason = history.query(path, stamp)
            self.assertIsNone(value)
            self.assertEqual("outside_dynamic_tf_coverage", reason)
        value, reason = history.query(path, self.BASE + 1, maximum_gap_ns=9_999_999)
        self.assertIsNone(value)
        self.assertEqual("dynamic_tf_interpolation_gap_exceeded", reason)

    def test_later_insertion_invalidates_cached_time_index(self):
        history = self.history()
        path = history.path("base", "sensor")
        history.query(path, self.BASE)
        history.add("joint", "sensor", self.BASE + 20_000_000, self.transform(3, 120), static=False)
        value, gap = history.query(path, self.BASE + 15_000_000)
        self.assertIsNotNone(value)
        self.assertEqual(10_000_000, gap)

    def test_static_changes_duplicate_time_conflicts_and_multiple_parents_fail_closed(self):
        history = self.history()
        for parent, child, stamp, transform, static in (
            ("base", "joint", 0, self.transform(2), True),
            ("joint", "sensor", self.BASE, self.transform(1), False),
            ("other", "sensor", self.BASE, self.transform(), False),
            ("joint", "sensor", 0, self.transform(), True),
        ):
            with self.assertRaises(ValueError):
                history.add(parent, child, stamp, transform, static=static)

    def test_missing_path_and_cycles_do_not_produce_identity_fallback(self):
        history = audit.TransformHistory()
        history.add("a", "b", 0, self.transform(), static=True)
        with self.assertRaises(ValueError):
            history.path("unknown", "b")
        history.add("b", "a", 0, self.transform(), static=True)
        with self.assertRaises(ValueError):
            history.path("unknown", "b")

    def test_invalid_transform_and_query_parameters_rejected(self):
        history = audit.TransformHistory()
        invalid = np.eye(4)
        invalid[0, 0] = 2
        with self.assertRaises(ValueError):
            history.add("a", "b", 0, invalid, static=True)
        history = self.history()
        for stamp, gap in ((float(self.BASE), 50_000_000), (self.BASE, False), (self.BASE, -1)):
            with self.assertRaises(ValueError):
                history.query(history.path("base", "sensor"), stamp, gap)

    def warning(self, count=64, ratio=".85, trans=0.00, vel=0.00"):
        return ("[warning] IMU prediction is not good.\n"
                f"[warning] num_validations={count}\n"
                f"[warning] IMU better ratios rot={ratio}\n")

    def test_prediction_and_bias_warnings_are_different_not_proof_of_bad_extrinsics(self):
        summary = audit.warning_summary(self.warning() + self.warning(128))
        self.assertEqual(2, summary["predictionWarningCount"])
        self.assertEqual(0, summary["biasMagnitudeWarningCount"])
        self.assertEqual([64, 128], [row["cumulativeValidations"] for row in summary["loggedCumulativeWindows"]])
        self.assertTrue(summary["roundedRatiosAreNotExactThresholdOrCalibrationTests"])

    def test_partial_non_cumulative_and_out_of_range_warning_blocks_are_rejected(self):
        for text in ("IMU prediction is not good.", self.warning(63),
                     self.warning(64) + self.warning(64), self.warning(0),
                     self.warning(ratio="1.01, trans=0.00, vel=0.00")):
            with self.assertRaises(ValueError):
                audit.warning_summary(text)

    def test_no_warning_is_not_converted_to_calibration_approval(self):
        summary = audit.warning_summary("synthetic initialization log")
        self.assertEqual(0, summary["predictionWarningCount"])
        self.assertNotIn("calibrationValidated", summary)

    def test_factory_parser_keeps_missing_lidar_imu_explicit_and_rejects_bad_shapes(self):
        class Source:
            def __init__(self, text):
                self.text = text
            def is_symlink(self):
                return False
            def stat(self):
                return type("Stat", (), {"st_size": len(self.text.encode())})()
            def read_text(self, **kwargs):
                return self.text
        text = ("chest_lidar:\n"
                "  R_baselink_sensor: [1,0,0,0,1,0,0,0,1]\n"
                "  t_baselink_sensor: [0,0,0]\n")
        transform, summary = audit.factory_file(Source(text))
        np.testing.assert_array_equal(transform, np.eye(4))
        self.assertFalse(summary["lidarImuNamedSectionPresent"])
        self.assertTrue(summary["factoryBaseLidarCannotSupplyLidarImuExtrinsic"])
        for bad in (text.replace("[0,0,0]", "[0,0]"),
                    text.replace("[1,0,0,0,1,0,0,0,1]", "[2,0,0,0,1,0,0,0,1]"),
                    text.replace("[0,0,0]", "[true,0,0]")):
            with self.assertRaises(ValueError):
                audit.factory_file(Source(bad))
        with self.assertRaises(Exception):
            audit.factory_file(Source(text + text))


if __name__ == "__main__":
    unittest.main()
