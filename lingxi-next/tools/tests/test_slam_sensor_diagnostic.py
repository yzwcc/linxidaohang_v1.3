"""Synthetic signal checks, not sensor calibration or navigation acceptance."""
import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
try:
    import numpy as np
except ModuleNotFoundError:
    np = None

if np is not None and sys.platform == "linux":
    spec = importlib.util.spec_from_file_location("diagnose_slam_sensors", ROOT / "tools/diagnose_slam_sensors.py")
    diagnostic = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(diagnostic)


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy environment")
class SensorDiagnostic(unittest.TestCase):
    def test_stamp_uses_integer_nanoseconds_and_rejects_bad_fields(self):
        message = NS(header=NS(stamp=NS(sec=1_700_000_000, nanosec=123)))
        self.assertEqual(1_700_000_000_000_000_123, diagnostic.stamp_ns(message))
        message.header.stamp.nanosec = 10**9
        with self.assertRaisesRegex(ValueError, "invalid_sensor_stamp"):
            diagnostic.stamp_ns(message)

    def test_subtracts_epoch_before_float_conversion(self):
        origin = 1_700_000_000_000_000_000
        times = diagnostic.series_times([origin + 123, origin + 124], origin)
        np.testing.assert_allclose(times, [123e-9, 124e-9], rtol=1e-12, atol=0)
        with self.assertRaisesRegex(ValueError, "increasing"):
            diagnostic.series_times([origin, origin], origin)

    def test_quaternion_sign_does_not_invent_rotation(self):
        q = np.asarray([0.0, 0, 0, 1])
        np.testing.assert_array_equal(diagnostic.rotation_vectors(q, -q), [0, 0, 0])
        for bad in ([0, 0, 0, 0], [0, 0, 0, float("nan")]):
            with self.assertRaisesRegex(ValueError, "invalid_sensor_quaternion"):
                diagnostic.quaternion(NS(**dict(zip(("x", "y", "z", "w"), bad))))

    def test_body_frame_rotation_uses_quaternion_multiplication_order(self):
        start = np.asarray([2**-0.5, 0, 0, 2**-0.5])
        increment = np.asarray([0, 0, np.sin(0.1), np.cos(0.1)])
        end = diagnostic.quaternion_product(start, increment)
        np.testing.assert_allclose(diagnostic.rotation_vectors(start, end), [0, 0, 0.2], atol=1e-12)

    def test_rate_comparison_reports_both_hypotheses_without_applying_bias(self):
        times = np.linspace(0, 2, 401)
        angle = 0.2 * times**2
        quaternions = np.column_stack([np.zeros_like(times), np.zeros_like(times),
                                      np.sin(angle / 2), np.cos(angle / 2)])
        gyros = np.column_stack([np.full_like(times, 0.07), np.zeros_like(times), 0.4 * times])
        original = gyros.copy()
        report = diagnostic.compare_angular_rates(times, gyros, times, quaternions)
        self.assertAlmostEqual(1.0, report["centeredLeastSquaresScaleDiagnosticOnly"], places=12)
        radians = report["fixedScaleHypotheses"]["radiansPerSecond"]
        self.assertAlmostEqual(0.07, radians["vectorRmse"], places=12)
        self.assertLess(radians["demeanedVectorRmseDiagnosticOnly"], 1e-12)
        self.assertEqual({"radiansPerSecond", "degreesPerSecond"}, set(report["fixedScaleHypotheses"]))
        np.testing.assert_array_equal(original, gyros)

    def test_comparison_never_extrapolates_or_uses_large_odometry_gaps(self):
        times = np.asarray([0.0, 1, 2])
        q = np.tile([0, 0, 0, 1], (3, 1))
        with self.assertRaisesRegex(ValueError, "no_overlapping"):
            diagnostic.compare_angular_rates(times, np.zeros((3, 3)), times, q)

    def test_coverage_keeps_scan_start_and_end_as_separate_hypotheses(self):
        stamps = [1_000_000_000, 1_100_000_000, 1_200_000_000]
        report = diagnostic.scan_coverage(stamps, [[0, 0.095]] * 3, stamps, stamps[0])
        self.assertEqual(2, report["headerIsScanStartHypothesis"]["fullyBracketedScans"])
        self.assertEqual(1, report["headerIsScanStartHypothesis"]["scansEndingAfterImu"])
        self.assertEqual(2, report["headerIsScanEndHypothesis"]["fullyBracketedScans"])
        self.assertEqual(1, report["headerIsScanEndHypothesis"]["scansBeginningBeforeImu"])
        self.assertTrue(report["neitherHypothesisIsCalibrationApproval"])

    def test_identity_orientation_is_reported_not_approved(self):
        q = np.tile([0, 0, 0, 1], (10, 1))
        report = diagnostic.orientation_summary(q)
        self.assertEqual(10, report["identityRotationSamples"])
        self.assertEqual(0.0, report["maximumRotationFromFirstDegrees"])
        self.assertTrue(report["normalizedQuaternionDoesNotProveValidAttitude"])


class SensorDiagnosticBoundary(unittest.TestCase):
    def test_diagnostic_has_no_robot_network_process_or_publishing_api(self):
        tree = ast.parse((ROOT / "tools/diagnose_slam_sensors.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module]
                for name in names:
                    self.assertNotIn(name.split(".")[0], {"rclpy", "rclcpp", "socket", "requests", "subprocess"})
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, {"create_node", "create_publisher", "create_client", "publish"})


if __name__ == "__main__":
    unittest.main()
