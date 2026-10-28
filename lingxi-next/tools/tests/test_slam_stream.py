"""Synthetic arrival sequences; never subscribe, drive, or execute native SLAM."""
import ast
import importlib.util
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
AVAILABLE = importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    import numpy as np
    with patch.object(sys, "path", [str(ROOT / "robot-slam")] + sys.path):
        from lingxi_slam.stream import Limits, SensorStream, StreamError


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy")
class StreamSynchronization(unittest.TestCase):
    BASE = 1_700_000_000_000_000_000

    def make(self, hypothesis="header_is_scan_start", **options):
        class Synthetic:
            def __init__(self):
                self.calls, self.frames = [], []

            def imu(self, stamp, acceleration, angular):
                self.calls.append(("imu", stamp))

            def frame(self, stamp, points, offsets):
                self.calls.append(("cloud", stamp))
                self.frames.append((stamp, points.copy(), offsets.copy()))
                return np.eye(4), np.array([1., 2., len(points), 0.])
        native = Synthetic()
        return SensorStream(native, hypothesis, **options), native

    def cloud(self, stream, milliseconds=40, received=.1, points=None, offsets=None):
        stream.push_cloud(self.BASE + milliseconds * 1_000_000,
                          np.ones((10, 3)) if points is None else points,
                          np.linspace(0, .02, 10) if offsets is None else offsets, received)

    def imu(self, stream, milliseconds, received):
        stream.push_imu(self.BASE + milliseconds * 1_000_000, [0, 0, 9.81], [0, 0, 0], received)

    def test_only_imu_through_scan_end_is_inserted_not_all_received_future_samples(self):
        stream, native = self.make()
        for index in range(9):
            self.imu(stream, index * 10, index * .005)
        self.cloud(stream)
        self.assertEqual(1, len(native.frames))
        self.assertEqual(7, stream.snapshot()["counts"]["insertedImu"])
        self.assertEqual(2, stream.snapshot()["pendingImu"])
        self.assertEqual(("imu", 1000.06), native.calls[-2])
        self.assertEqual(("cloud", 1000.04), native.calls[-1])

    def test_end_hypothesis_moves_scan_start_without_adjusting_imu_clock(self):
        stream, native = self.make("header_is_scan_end")
        for index in range(9):
            self.imu(stream, index * 10, index * .005)
        self.cloud(stream)
        self.assertAlmostEqual(1000.02, native.frames[0][0])
        self.assertEqual(5, stream.snapshot()["counts"]["insertedImu"])
        self.assertEqual(("imu", 1000.04), native.calls[-2])

    def test_cloud_waits_for_full_imu_bracket_and_owns_immutable_packet_copies(self):
        stream, native = self.make()
        self.imu(stream, 0, 0.)
        points, offsets = np.ones((10, 3)), np.linspace(0, .02, 10)
        self.cloud(stream, received=.01, points=points, offsets=offsets)
        points[:] = 99
        offsets[:] = .19
        for index in range(1, 6):
            self.imu(stream, index * 10, .02 + index * .01)
        self.assertEqual([], native.frames)
        self.imu(stream, 60, .09)
        np.testing.assert_array_equal(native.frames[0][1], np.ones((10, 3)))
        self.assertAlmostEqual(.02, native.frames[0][2].max())

    def test_leading_unbracketed_clouds_are_counted_without_native_calls(self):
        stream, native = self.make()
        self.cloud(stream, milliseconds=-10, received=0.)
        self.imu(stream, 0, .1)
        self.assertEqual([], native.calls)
        self.assertEqual(1, stream.snapshot()["counts"]["skippedBeforeImu"])

    def test_explicit_eof_counts_trailing_cloud_and_cannot_restart_or_reuse(self):
        stream, native = self.make()
        self.imu(stream, 0, 0.)
        self.cloud(stream, received=.1)
        summary = stream.finish()
        self.assertEqual("finished", summary["state"])
        self.assertEqual(1, summary["counts"]["skippedAfterImuAtEndOfInput"])
        self.assertEqual(0, summary["pendingCloudBytes"])
        self.assertEqual([], native.calls)
        for operation in (stream.finish, lambda: stream.tick(.2), lambda: self.imu(stream, 10, .2)):
            with self.assertRaisesRegex(StreamError, "not_running"):
                operation()

    def test_monotonic_tick_detects_stall_even_without_new_sensor_callbacks(self):
        stream, native = self.make()
        self.imu(stream, 0, 0.)
        self.cloud(stream, received=.1)
        with self.assertRaisesRegex(StreamError, "deadline"):
            stream.tick(1.)
        self.assertEqual("failed", stream.snapshot()["state"])
        self.assertEqual(0, stream.snapshot()["pendingCloudBytes"])
        with self.assertRaisesRegex(StreamError, "not_running"):
            self.imu(stream, 60, 1.1)
        self.assertEqual([], native.calls)

    def test_missing_topic_has_a_bounded_startup_window(self):
        stream, _ = self.make()
        self.cloud(stream, received=0.)
        for index in range(1, 20):
            self.cloud(stream, milliseconds=40 + index * 100, received=index * .1)
        with self.assertRaisesRegex(StreamError, "deadline"):
            stream.tick(2.01)

    def test_cloud_and_imu_count_limits_fail_without_silent_dropping(self):
        for kind in ("cloud", "imu"):
            with self.subTest(kind=kind):
                limits = Limits(maximum_pending_clouds=1, maximum_pending_imu=1)
                stream, native = self.make(limits=limits)
                if kind == "cloud":
                    self.cloud(stream, received=0.)
                    operation = lambda: self.cloud(stream, milliseconds=50, received=.1)
                else:
                    self.imu(stream, 0, 0.)
                    operation = lambda: self.imu(stream, 10, .1)
                with self.assertRaisesRegex(StreamError, "capacity"):
                    operation()
                self.assertEqual([], native.calls)
                self.assertEqual("failed", stream.snapshot()["state"])

    def test_cloud_byte_limit_is_checked_before_retaining_large_packets(self):
        stream, native = self.make(limits=Limits(maximum_pending_bytes=319))
        with self.assertRaisesRegex(StreamError, "capacity"):
            self.cloud(stream, received=0.)
        self.assertEqual(0, stream.snapshot()["pendingCloudBytes"])
        self.assertEqual([], native.calls)

    def test_gap_duplicate_clock_reset_and_noninteger_headers_latch_failure(self):
        for stamp in (self.BASE, self.BASE - 1, self.BASE + 51_000_000, True, 1000.):
            with self.subTest(stamp=stamp):
                stream, _ = self.make()
                self.imu(stream, 0, 0.)
                with self.assertRaisesRegex(StreamError, "sensor_time"):
                    stream.push_imu(stamp, [0, 0, 9.81], [0, 0, 0], .01)
                self.assertEqual("failed", stream.snapshot()["state"])

    def test_arrival_clock_rollback_or_nonfinite_time_is_rejected(self):
        for now in (-1., float("nan"), float("inf"), True):
            with self.subTest(now=now):
                stream, _ = self.make()
                stream.tick(0.)
                with self.assertRaisesRegex(StreamError, "arrival_clock"):
                    stream.tick(now)

    def test_invalid_complete_clouds_and_imu_values_fail_closed(self):
        bad_clouds = [(np.ones((9, 3)), np.arange(9) / 1000),
                      (np.ones((10, 2)), np.linspace(0, .02, 10)),
                      (np.full((10, 3), np.nan), np.linspace(0, .02, 10)),
                      (np.ones((10, 3)), np.linspace(-.01, .01, 10)),
                      (np.ones((10, 3)), np.zeros(10)),
                      (np.ones((10, 3)), np.linspace(0, .21, 10)),
                      (["invalid"], ["invalid"])]
        for points, offsets in bad_clouds:
            stream, native = self.make()
            with self.assertRaises(StreamError):
                self.cloud(stream, received=0., points=points, offsets=offsets)
            self.assertEqual("failed", stream.snapshot()["state"])
            self.assertEqual([], native.calls)
        for acceleration in ([1, 2], [float("nan"), 0, 0], ["invalid", 0, 0]):
            stream, _ = self.make()
            with self.assertRaises(StreamError):
                stream.push_imu(self.BASE, acceleration, [0, 0, 0], 0.)
            self.assertEqual("failed", stream.snapshot()["state"])

    def test_native_or_consumer_error_latches_and_never_repeats_frame(self):
        for where in ("native", "consumer"):
            with self.subTest(where=where):
                stream, native = self.make()
                for index in range(9):
                    self.imu(stream, index * 10, index * .005)
                def fail(*_):
                    raise RuntimeError("synthetic isolated test failure")
                if where == "native":
                    native.frame = fail
                else:
                    stream.on_frame = fail
                with self.assertRaisesRegex(StreamError, "consumer_failed"):
                    self.cloud(stream)
                with self.assertRaisesRegex(StreamError, "not_running"):
                    stream.tick(.2)
                self.assertEqual(0, stream.snapshot()["pendingClouds"])

    def test_initializer_is_not_published_as_a_fabricated_pose(self):
        frames = []
        stream, native = self.make(on_frame=lambda *args: frames.append(args))
        native.frame = lambda *args: (None, np.array([1., 2., 50., 0.]))
        for index in range(9):
            self.imu(stream, index * 10, index * .005)
        self.cloud(stream)
        self.assertIsNone(frames[0][2])
        self.assertEqual(1, stream.snapshot()["counts"]["initializationScans"])
        self.assertEqual(0, stream.snapshot()["counts"]["estimatedPoses"])

    def test_bad_native_pose_or_statistics_never_reach_result_consumer(self):
        for kind in ("scaled_pose", "nonfinite_pose", "nonfinite_statistics", "fractional_counter"):
            with self.subTest(kind=kind):
                received = []
                stream, native = self.make(on_frame=lambda *args: received.append(args))
                pose, stats = np.eye(4), np.array([1., 2., 50., 0.])
                if kind == "scaled_pose":
                    pose[0, 0] = 2
                elif kind == "nonfinite_pose":
                    pose[0, 3] = np.nan
                elif kind == "nonfinite_statistics":
                    stats[0] = np.nan
                else:
                    stats[2] = 50.5
                native.frame = lambda *args: (pose, stats)
                for index in range(9):
                    self.imu(stream, index * 10, index * .005)
                with self.assertRaisesRegex(StreamError, "consumer_failed"):
                    self.cloud(stream)
                self.assertEqual([], received)

    def test_delayed_bracket_and_duplicate_cloud_cannot_resume_old_work(self):
        stream, native = self.make()
        self.imu(stream, 0, 0.)
        self.cloud(stream, received=.01)
        with self.assertRaisesRegex(StreamError, "deadline"):
            self.imu(stream, 10, .8)
        self.assertEqual([], native.calls)
        stream, native = self.make()
        self.cloud(stream, received=0.)
        with self.assertRaisesRegex(StreamError, "sensor_time"):
            self.cloud(stream, received=.1)
        self.assertEqual([], native.calls)

    def test_owner_thread_is_enforced_without_mutating_the_running_stream(self):
        stream, _ = self.make()
        errors = []
        def other_thread():
            try:
                stream.tick(0.)
            except StreamError as error:
                errors.append(str(error))
        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join()
        self.assertEqual(["single_owner_thread_required"], errors)
        self.assertEqual("running", stream.snapshot()["state"])

    def test_invalid_limits_or_implicit_timing_are_not_accepted(self):
        for options in ({"maximum_pending_clouds": True}, {"maximum_wait_seconds": float("nan")},
                        {"maximum_pending_bytes": 0}, {"maximum_points_per_cloud": 9}):
            with self.assertRaises(ValueError):
                Limits(**options)
        with self.assertRaises(ValueError):
            self.make("automatic")
        for limits in (False, 0, "", {}, []):
            with self.assertRaisesRegex(ValueError, "validated_stream_limits"):
                self.make(limits=limits)


class StreamBoundaries(unittest.TestCase):
    def test_core_has_no_ros_network_native_loading_or_actuation_dependency(self):
        path = ROOT / "robot-slam/lingxi_slam/stream.py"
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module]
                for module in modules:
                    self.assertNotIn(module.split(".")[0],
                                     {"rclpy", "subprocess", "ctypes", "socket", "paramiko", "requests"})


if __name__ == "__main__":
    unittest.main()
