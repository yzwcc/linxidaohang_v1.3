"""Synthetic initialization/timestamp audits; no hardware or private input."""
import importlib.util
from pathlib import Path
import platform
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[2]
AVAILABLE = (platform.system() == "Linux" and all(importlib.util.find_spec(name) is not None
             for name in ("numpy", "scipy", "json5", "rosbags")))
if AVAILABLE:
    sys.path.insert(0, str(ROOT / "tools"))
    import numpy as np
    import audit_glim_initialization as audit


@unittest.skipUnless(AVAILABLE, "requires isolated Linux NumPy/SciPy/rosbags environment")
class InitializationAudit(unittest.TestCase):
    BASE = 1_800_000_000_000_000_001

    def fixture(self):
        imu = self.BASE + np.arange(101, dtype=np.int64) * 5_000_000
        clouds = self.BASE + np.arange(5, dtype=np.int64) * 100_000_000
        poses = np.tile(np.eye(4), (2, 1, 1))
        arrays = {
            "frame_headers_ns": clouds,
            "pose_headers_ns": clouds[3:],
            "frame_algorithm_seconds": (clouds - imu[0]).astype(float) / 1e9 + 1000,
            "poses": poses,
        }
        sensors = {
            "imu_headers": imu, "cloud_headers": clouds,
            "offsets_ns": np.tile([0, 99_000_000], (5, 1)).astype(np.int64),
            "acceleration": np.tile([0., 0., 9.81], (len(imu), 1)),
            "angular": np.tile([.07, 0., 0.], (len(imu), 1)),
        }
        state = {"T_world_imu": np.array([0, 0, 0, 0, 0, 0, 1.]),
                 "v_world_imu": np.zeros(3), "imu_bias": np.array([0, 0, 0, .07, 0, 0.])}
        config = {"initialization_mode": "ROBUST", "initialization_window_size": .29}
        return arrays, sensors, config, state, np.eye(4)

    def test_integer_clock_subtraction_retains_one_nanosecond_and_does_not_claim_latency(self):
        headers = np.array([self.BASE, self.BASE + 5_000_000], dtype=np.int64)
        arrivals = np.array([50, 5_000_051], dtype=np.int64)
        result = audit.clock_summary(headers, arrivals)
        self.assertEqual(1e-6, result["endRelativeArrivalMinusHeaderMilliseconds"])
        self.assertTrue(result["absoluteTransportLatencyNotMeasured"])

    def test_duplicate_reversed_float_and_mismatched_clock_arrays_are_rejected(self):
        for values in (np.array([1., 2.]), np.array([2, 1], dtype=np.int64),
                       np.array([1, 1], dtype=np.int64), np.array([-1, 1], dtype=np.int64)):
            with self.assertRaises(ValueError):
                audit.clock_summary(values, values)
        with self.assertRaises(ValueError):
            audit.clock_summary(np.arange(3, dtype=np.int64), np.arange(2, dtype=np.int64))

    def test_both_scan_hypotheses_are_counted_without_choosing_one(self):
        _, sensors, *_ = self.fixture()
        result = audit.coverage(sensors["cloud_headers"], sensors["offsets_ns"], sensors["imu_headers"])
        self.assertEqual(5, result["header_is_scan_start"]["bracketed"])
        self.assertEqual(1, result["header_is_scan_end"]["beginningBeforeImu"])
        self.assertEqual(4, result["header_is_scan_end"]["bracketed"])
        self.assertTrue(result["neitherHypothesisSelectedOrApproved"])

    def test_invalid_point_offset_domain_is_not_rounded_into_valid_coverage(self):
        _, sensors, *_ = self.fixture()
        for bounds in (np.zeros((5, 2), dtype=np.int64), sensors["offsets_ns"].astype(float),
                       np.tile([-1, 99], (5, 1)).astype(np.int64)):
            with self.assertRaises(ValueError):
                audit.coverage(sensors["cloud_headers"], bounds, sensors["imu_headers"])

    def test_first_pose_prefix_and_bracketing_imu_are_reconstructed_not_simulated(self):
        result = audit.initialization(*self.fixture())
        self.assertEqual(3, result["noPoseScansBeforeFirstPose"])
        self.assertTrue(result["firstPoseAtFirstFrameReachingConfiguredWindow"])
        self.assertEqual(81, result["imuInsertedThroughFirstPoseScanIncludingBracketingSample"])
        self.assertAlmostEqual(0., result["initialMeanAngularMinusLoggedBiasNormRawUnits"], places=14)
        self.assertTrue(result["loggedEstimatesNotIndependentCalibrationOrTruth"])

    def test_late_initialization_is_reported_not_silently_approved(self):
        arrays, sensors, config, state, transform = self.fixture()
        config["initialization_window_size"] = .2
        result = audit.initialization(arrays, sensors, config, state, transform)
        self.assertFalse(result["firstPoseAtFirstFrameReachingConfiguredWindow"])

    def test_window_boundary_uses_native_double_arithmetic_not_rounded_integer_duration(self):
        values = self.fixture()
        values[2]["initialization_window_size"] = .3
        result = audit.initialization(*values)
        self.assertEqual(.3, result["firstPoseSpanFromFirstProcessedScanSeconds"])
        self.assertFalse(result["firstPoseAtFirstFrameReachingConfiguredWindow"])
        self.assertTrue(result["windowCrossingUsesActualAlgorithmDoubleSubtraction"])

    def test_algorithm_timestamp_shift_is_rejected_instead_of_fitted(self):
        values = self.fixture()
        values[0]["frame_algorithm_seconds"] += .001
        with self.assertRaisesRegex(ValueError, "unchanged_scan_start"):
            audit.initialization(*values)

    def test_missing_or_nonprefix_pose_output_is_rejected(self):
        values = self.fixture()
        values[0]["pose_headers_ns"] = values[0]["pose_headers_ns"] + 1
        with self.assertRaises(ValueError):
            audit.initialization(*values)

    def test_logged_state_must_match_first_pose_not_different_coordinate_frame(self):
        values = self.fixture()
        values[3]["T_world_imu"][0] = 1.
        with self.assertRaisesRegex(ValueError, "first_pose_must_match"):
            audit.initialization(*values)

    def test_logged_state_parser_requires_one_complete_finite_state(self):
        text = ("\n[odom] [info] T_world_imu=se3(0,0,0,0,0,0,1)"
                "\n[odom] [info] v_world_imu=vec(0,0,0)"
                "\n[odom] [info] imu_bias=vec(0,0,0,0,0,0)")
        self.assertEqual(6, len(audit.initial_state(text)["imu_bias"]))
        for bad in (text + text, text.replace("vec(0,0,0)", "vec(nan,0,0)"), text[:-1]):
            with self.assertRaises(ValueError):
                audit.initial_state(bad)

    def test_constant_input_is_not_labeled_stationary_or_calibrated(self):
        values = self.fixture()
        result = audit.vectors_summary(values[1]["acceleration"], values[1]["angular"])
        self.assertTrue(result["lowVariationDoesNotProvePhysicalStationarity"])
        self.assertAlmostEqual(.07, result["angularMeanNormRawUnits"])
        for malformed in (np.zeros((1, 3)), np.zeros((5, 2)), np.full((5, 3), np.nan)):
            with self.assertRaises(ValueError):
                audit.vectors_summary(malformed, malformed)

    def test_native_configuration_comments_use_json5_without_changing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            odometry = root / "config/config_odometry_cpu.json"
            text = '{// native comments\n"odometry_estimation":{"initialization_mode":"ROBUST"}}'
            odometry.write_text(text)
            (root / "config/config_sensors.json").write_text(
                '{"sensors":{"T_lidar_imu":[0,0,0,0,0,0,1]}}')
            config, transform = audit.read_configuration(root)
            self.assertEqual("ROBUST", config["initialization_mode"])
            np.testing.assert_array_equal(np.eye(4), transform)
            self.assertEqual(text, odometry.read_text())


if __name__ == "__main__":
    unittest.main()
