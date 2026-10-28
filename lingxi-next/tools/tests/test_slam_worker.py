"""Isolated synthetic packets and concurrency checks; no ROS or native SLAM."""
import ast
import importlib.util
from pathlib import Path
import struct
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
AVAILABLE = importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    import numpy as np
    with patch.object(sys, "path", [str(ROOT / "robot-slam")] + sys.path):
        from lingxi_slam import worker as module
        from lingxi_slam.messages import SensorProfile, cloud_snapshot, decode_cloud, imu_snapshot
        from lingxi_slam.stream import Limits
        from lingxi_slam.worker import SensorWorker, WorkerLimits


def header(milliseconds=0, frame="synthetic_imu"):
    return NS(frame_id=frame, stamp=NS(sec=1700000000, nanosec=milliseconds * 1_000_000))


def imu(milliseconds=0):
    return NS(
        header=header(milliseconds), linear_acceleration=NS(x=0., y=0., z=9.81),
        angular_velocity=NS(x=0., y=0., z=0.),
        linear_acceleration_covariance=[0.] * 9, angular_velocity_covariance=[0.] * 9,
    )


def cloud(milliseconds=40, *, big=False, organized=False):
    width, height = (5, 2) if organized else (10, 1)
    row_step = width * 32 + (16 if organized else 0)
    data = bytearray(height * row_step)
    for index in range(10):
        row, col = divmod(index, width)
        offset = row * row_step + col * 32
        struct.pack_into((">" if big else "<") + "fff", data, offset, index + 1, 2, 3)
        struct.pack_into((">" if big else "<") + "d", data, offset + 24, index * 2_000_000)
    return NS(
        header=header(milliseconds, "synthetic_cloud"), width=width, height=height,
        point_step=32, row_step=row_step, is_bigendian=big, data=data,
        fields=[NS(name=name, offset=offset, datatype=datatype, count=1)
                for name, offset, datatype in (("x", 0, 7), ("y", 4, 7), ("z", 8, 7), ("timestamp", 24, 8))],
    )


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy")
class SensorMessages(unittest.TestCase):
    def setUp(self):
        self.profile = SensorProfile(
            "synthetic_cloud", "synthetic_imu", "frame_relative_nanoseconds_hypothesis")

    def test_endian_and_organized_padding_preserve_all_points_and_times(self):
        for big in (False, True):
            for organized in (False, True):
                packet = cloud_snapshot(cloud(big=big, organized=organized), self.profile, 100, 1024)
                points, times = decode_cloud(packet)
                np.testing.assert_array_equal(points[:, 0], np.arange(1, 11))
                np.testing.assert_allclose(times, np.arange(10) * .002)

    def test_packet_owns_payload_metadata_and_imu_vectors(self):
        message = cloud()
        packet = cloud_snapshot(message, self.profile, 100, 1024)
        message.data[:] = b"\0" * len(message.data)
        message.fields[-1].offset = 0
        points, times = decode_cloud(packet)
        self.assertEqual(10, len(points))
        self.assertAlmostEqual(.018, times[-1])
        message = imu()
        packet = imu_snapshot(message, self.profile)
        message.linear_acceleration.z = 1234
        self.assertEqual((0., 0., 9.81), packet.acceleration)

    def test_invalid_frames_stamps_and_missing_imu_components_are_rejected(self):
        for change in ("frame", "stamp", "missing", "nonfinite"):
            message = imu()
            if change == "frame":
                message.header.frame_id = "different_sensor"
            elif change == "stamp":
                message.header.stamp.nanosec = 1_000_000_000
            elif change == "missing":
                message.angular_velocity_covariance[0] = -1
            else:
                message.angular_velocity.x = float("nan")
            with self.assertRaises(ValueError):
                imu_snapshot(message, self.profile)

    def test_schema_payload_and_capacity_are_checked_before_decoding(self):
        for change in ("bytes", "count", "time_type", "overlap", "duplicate", "dimensions"):
            message = cloud()
            if change == "bytes":
                message.data.pop()
            elif change == "count":
                message.fields[0].count = 2
            elif change == "time_type":
                message.fields[-1].datatype = 7
            elif change == "overlap":
                message.fields[-1].offset = 4
            elif change == "duplicate":
                message.fields.append(message.fields[0])
            else:
                message.width = True
            with self.assertRaises(ValueError):
                cloud_snapshot(message, self.profile, 100, 1024)
        with self.assertRaises(ValueError):
            cloud_snapshot(cloud(), self.profile, 9, 1024)
        with self.assertRaises(ValueError):
            cloud_snapshot(cloud(), self.profile, 100, 319)

    def test_nonfinite_points_or_bad_time_are_not_filtered_or_fabricated(self):
        for value, offset, format_ in (
            (float("nan"), 0, "<f"), (float("nan"), 24, "<d"), (-1, 24, "<d"),
            (1e18, 24, "<d"),
        ):
            message = cloud()
            struct.pack_into(format_, message.data, offset, value)
            packet = cloud_snapshot(message, self.profile, 100, 1024)
            with self.assertRaises(ValueError):
                decode_cloud(packet)

    def test_profile_requires_explicit_frames_and_time_hypothesis(self):
        for frames in (("", "imu"), (False, "imu"), ("frame with spaces", "imu")):
            with self.assertRaises(ValueError):
                SensorProfile(*frames, "frame_relative_nanoseconds_hypothesis")
        for encoding in ("auto", "seconds", "absolute_nanoseconds", None):
            with self.assertRaises(ValueError):
                SensorProfile("cloud", "imu", encoding)


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy")
class CallbackWorker(unittest.TestCase):
    def setUp(self):
        self.profile = SensorProfile(
            "synthetic_cloud", "synthetic_imu", "frame_relative_nanoseconds_hypothesis")
        self.calls = []
        self.frames = []
        self.created = threading.Event()

    def make(self, *, gate=None, frame_gate=None, fail_frame=False, fail_close=False, **options):
        parent = self

        class SyntheticEngine:
            def imu(self, *values):
                parent.calls.append(("imu", threading.get_ident(), values[0]))

            def frame(self, stamp, points, offsets):
                parent.calls.append(("frame", threading.get_ident(), stamp))
                parent.frames.append((points.copy(), offsets.copy()))
                if frame_gate is not None:
                    frame_gate[0].set()
                    frame_gate[1].wait(2)
                if fail_frame:
                    raise RuntimeError("synthetic native failure")
                return np.eye(4), np.array([1., 2., len(points), 0.])

            def close(self):
                parent.calls.append(("close", threading.get_ident()))
                if fail_close:
                    raise RuntimeError("synthetic close failure")

        def factory():
            parent.calls.append(("create", threading.get_ident()))
            parent.created.set()
            if gate is not None:
                gate.wait(2)
            return SyntheticEngine()

        worker = SensorWorker(factory, self.profile, "header_is_scan_start", **options)

        def cleanup():
            if gate is not None:
                gate.set()
            if frame_gate is not None:
                frame_gate[1].set()
            worker.stop()
        self.addCleanup(cleanup)
        worker.start()
        self.assertTrue(self.created.wait(1))
        return worker

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            threading.Event().wait(.002)
        self.fail("synthetic worker condition did not complete")

    def fill(self, worker):
        for index in range(9):
            self.assertTrue(worker.offer_imu(imu(index * 10)))
        self.assertTrue(worker.offer_cloud(cloud()))

    def test_native_factory_methods_consumer_and_close_share_only_worker_thread(self):
        delivered = []
        worker = self.make(on_frame=lambda *values: delivered.append(threading.get_ident()))
        self.fill(worker)
        self.wait_for(lambda: worker.snapshot()["counts"]["completedMessages"] == 10)
        state = worker.stop()
        self.assertEqual("stopped", state["state"])
        self.assertFalse(state["threadAlive"])
        ids = {call[1] for call in self.calls} | set(delivered)
        self.assertEqual(1, len(ids))
        self.assertNotIn(threading.get_ident(), ids)
        self.assertEqual(7, len([call for call in self.calls if call[0] == "imu"]))
        self.assertEqual(1, state["stream"]["counts"]["estimatedPoses"])
        self.assertEqual(0, state["queuedPayloadBytes"])

    def test_callback_snapshot_is_immutable_until_worker_decodes_it(self):
        gate = threading.Event()
        worker = self.make(gate=gate)
        message = cloud()
        for index in range(9):
            worker.offer_imu(imu(index * 10))
        worker.offer_cloud(message)
        message.data[:] = b"\0" * len(message.data)
        message.header.stamp.nanosec = 999999
        gate.set()
        self.wait_for(lambda: len(self.frames) == 1)
        np.testing.assert_array_equal(self.frames[0][0][:, 0], np.arange(1, 11))

    def test_queue_count_overflow_latches_and_does_not_run_or_retry_old_work(self):
        gate = threading.Event()
        worker = self.make(gate=gate, limits=WorkerLimits(maximum_messages=1))
        self.assertTrue(worker.offer_imu(imu()))
        self.assertFalse(worker.offer_imu(imu(10)))
        self.assertEqual("callback_queue_capacity_exceeded", worker.snapshot()["failure"])
        self.assertFalse(worker.offer_imu(imu(20)))
        gate.set()
        state = worker.stop()
        self.assertEqual(1, state["counts"]["discardedMessages"])
        self.assertEqual(["create", "close"], [call[0] for call in self.calls])
        with self.assertRaisesRegex(RuntimeError, "cannot_restart"):
            worker.start()

    def test_cloud_count_and_payload_limits_include_queued_snapshots(self):
        for limits in (
            WorkerLimits(maximum_clouds=1),
            WorkerLimits(maximum_payload_bytes=600, maximum_cloud_payload_bytes=600),
        ):
            with self.subTest(limits=limits):
                gate = threading.Event()
                worker = self.make(gate=gate, limits=limits)
                self.assertTrue(worker.offer_cloud(cloud()))
                self.assertFalse(worker.offer_cloud(cloud(50)))
                state = worker.snapshot()
                self.assertEqual("failed", state["state"])
                self.assertEqual(0, state["queuedPayloadBytes"])
                gate.set()
                worker.stop()

    def test_queue_age_expires_before_native_receives_retained_packets(self):
        gate = threading.Event()
        worker = self.make(gate=gate, limits=WorkerLimits(maximum_queue_age_seconds=.03))
        worker.offer_imu(imu())
        time.sleep(.05)
        gate.set()
        self.wait_for(lambda: not worker.snapshot()["threadAlive"])
        state = worker.snapshot()
        self.assertEqual("callback_queue_age_exceeded", state["failure"])
        self.assertEqual(["create", "close"], [call[0] for call in self.calls])

    def test_no_callbacks_still_trigger_sensor_startup_watchdog(self):
        worker = self.make(stream_limits=Limits(initial_wait_seconds=.06),
                           limits=WorkerLimits(tick_seconds=.005))
        self.wait_for(lambda: not worker.snapshot()["threadAlive"])
        self.assertEqual("failed", worker.snapshot()["state"])
        self.assertEqual("sensor_arrival_deadline_exceeded", worker.snapshot()["stream"]["failure"])

    def test_factory_timeout_suppresses_all_queued_processing(self):
        gate = threading.Event()
        worker = self.make(gate=gate, stream_limits=Limits(initial_wait_seconds=.03))
        worker.offer_imu(imu())
        time.sleep(.05)
        self.assertEqual("native_startup_deadline_exceeded", worker.snapshot()["failure"])
        gate.set()
        worker.stop()
        self.assertEqual(["create", "close"], [call[0] for call in self.calls])

    def test_callbacks_can_enqueue_while_native_frame_is_busy(self):
        entered, release = threading.Event(), threading.Event()
        worker = self.make(frame_gate=(entered, release))
        self.fill(worker)
        self.assertTrue(entered.wait(1))
        self.assertTrue(worker.offer_imu(imu(90)))
        self.assertEqual(1, worker.snapshot()["queuedMessages"])
        release.set()
        self.wait_for(lambda: worker.snapshot()["counts"]["completedMessages"] == 11)

    def test_shutdown_timeout_reports_live_thread_and_suppresses_its_late_result(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        worker = self.make(frame_gate=(entered, release), on_frame=lambda *values: results.append(values),
                           limits=WorkerLimits(shutdown_seconds=.03))
        self.fill(worker)
        self.assertTrue(entered.wait(1))
        worker.offer_imu(imu(90))
        state = worker.stop()
        self.assertEqual("worker_shutdown_deadline_exceeded", state["failure"])
        self.assertTrue(state["threadAlive"])
        self.assertEqual(0, state["queuedMessages"])
        release.set()
        self.wait_for(lambda: not worker.snapshot()["threadAlive"])
        self.assertEqual([], results)
        self.assertEqual(1, len([call for call in self.calls if call[0] == "frame"]))

    def test_native_and_consumer_failure_close_once_without_repeating_frames(self):
        for where in ("native", "consumer"):
            with self.subTest(where=where):
                self.calls.clear()
                def fail(*_):
                    raise RuntimeError("synthetic consumer failure")
                worker = self.make(fail_frame=where == "native",
                                   on_frame=fail if where == "consumer" else None)
                self.fill(worker)
                self.wait_for(lambda: not worker.snapshot()["threadAlive"])
                self.assertEqual("failed", worker.snapshot()["state"])
                self.assertFalse(worker.offer_cloud(cloud(140)))
                self.assertEqual(1, len([call for call in self.calls if call[0] == "frame"]))
                self.assertEqual(1, len([call for call in self.calls if call[0] == "close"]))

    def test_idle_ticks_and_new_arrivals_do_not_create_clock_rollback(self):
        worker = self.make(limits=WorkerLimits(tick_seconds=.005))
        self.assertTrue(worker.offer_imu(imu()))
        time.sleep(.02)
        for index in range(1, 9):
            worker.offer_imu(imu(index * 10))
        worker.offer_cloud(cloud())
        self.wait_for(lambda: worker.snapshot()["counts"]["completedMessages"] == 10)
        self.assertEqual("running", worker.snapshot()["state"])

    def test_slow_decode_is_detected_even_if_no_pose_is_produced(self):
        worker = self.make(limits=WorkerLimits(maximum_processing_seconds=.03, tick_seconds=.005))
        self.wait_for(lambda: worker.snapshot()["state"] == "running")
        original = module.decode_cloud
        def slow(packet):
            time.sleep(.06)
            return original(packet)
        with patch.object(module, "decode_cloud", side_effect=slow):
            worker.offer_cloud(cloud())
            self.wait_for(lambda: not worker.snapshot()["threadAlive"])
        self.assertEqual("worker_progress_deadline_exceeded", worker.snapshot()["failure"])
        self.assertEqual([], self.frames)

    def test_invalid_packet_and_duplicate_sensor_stamp_fail_closed(self):
        worker = self.make()
        message = imu()
        message.header.frame_id = "unconfigured"
        self.assertFalse(worker.offer_imu(message))
        self.assertEqual("failed", worker.snapshot()["state"])
        worker.stop()
        worker = self.make()
        worker.offer_imu(imu())
        worker.offer_imu(imu())
        self.wait_for(lambda: not worker.snapshot()["threadAlive"])
        self.assertEqual("failed", worker.snapshot()["state"])

    def test_shutdown_failure_is_not_reported_as_a_clean_stop(self):
        worker = self.make(fail_close=True)
        self.assertEqual("native_cleanup_failed", worker.stop()["failure"])

    def test_snapshot_cannot_mutate_worker_state_and_stop_is_idempotent(self):
        worker = self.make()
        worker.offer_imu(imu())
        self.wait_for(lambda: worker.snapshot()["stream"] is not None)
        state = worker.snapshot()
        state["counts"]["acceptedImu"] = 1000
        state["stream"]["counts"]["receivedImu"] = 1000
        self.assertEqual(1, worker.snapshot()["counts"]["acceptedImu"])
        self.assertEqual(1, worker.snapshot()["stream"]["counts"]["receivedImu"])
        worker.stop()
        worker.stop()
        self.assertEqual(1, len([call for call in self.calls if call[0] == "close"]))

    def test_invalid_limits_and_implicit_configuration_are_rejected(self):
        for values in ({"maximum_messages": False}, {"maximum_queue_age_seconds": float("nan")},
                       {"shutdown_seconds": 0}, {"maximum_processing_seconds": .01}):
            with self.assertRaises(ValueError):
                WorkerLimits(**values)
        for value in (False, {}, 0):
            with self.assertRaises(ValueError):
                SensorWorker(lambda: None, self.profile, "header_is_scan_start", limits=value)
        worker = SensorWorker(lambda: None, self.profile, "header_is_scan_start")
        self.assertEqual("stopped", worker.stop()["state"])
        with self.assertRaises(RuntimeError):
            worker.start()

    def test_clock_failure_is_latched_without_exposing_exception_text(self):
        clock = [time.monotonic()]
        worker = self.make(clock=lambda: clock[0])
        clock[0] -= 1
        self.assertFalse(worker.offer_imu(imu()))
        self.assertEqual("monotonic_worker_clock_required", worker.snapshot()["failure"])


class WorkerBoundaries(unittest.TestCase):
    def test_transport_does_not_load_native_libraries_or_use_ros_or_network(self):
        for filename in ("worker.py", "messages.py"):
            tree = ast.parse((ROOT / "robot-slam/lingxi_slam" / filename).read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = ([item.name for item in node.names] if isinstance(node, ast.Import)
                               else [node.module])
                    for module in modules:
                        self.assertNotIn(module.split(".")[0],
                                         {"rclpy", "ctypes", "subprocess", "socket", "paramiko", "requests"})


if __name__ == "__main__":
    unittest.main()
