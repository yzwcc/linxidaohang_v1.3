"""Ingress freshness and synchronous callback ownership; no robot or ROS IO."""
from dataclasses import asdict, replace
import importlib.util
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam"),
                str(ROOT / "tools"), str(ROOT / "tools/tests")]
AVAILABLE = sys.platform == "linux" and all(importlib.util.find_spec(name) is not None
    for name in ("numpy", "scipy", "small_gicp"))
if AVAILABLE:
    import numpy as np
    from localization_control import LocalizedNative
    from lingxi_slam.localization_session import LocalizationBinding, LocalizationSession
    from lingxi_slam.messages import SensorProfile
    from lingxi_slam.point_output import PointPacket
    from lingxi_slam.seeded_localization import RegistrationEstimate
    from lingxi_slam.stream import Scan, SensorStream, StreamError
    from lingxi_slam.worker import SensorWorker
    import run_glim_supervised_replay as replay
    from test_slam_worker import cloud, imu


@unittest.skipUnless(AVAILABLE, "requires isolated Linux localization dependencies")
class OriginalReceipt(unittest.TestCase):
    def setUp(self):
        self.now, self.registration_delay = 10., 0.
        self.after_native = lambda packet: packet
        self.registration_calls, self.native_calls = [], []
        self.binding = LocalizationBinding(
            "2" * 32, "synthetic", "slam-" + "1" * 32, "3" * 64, "4" * 64, "5" * 64)
        case = self

        class Registrar:
            def register(self, scan, seed):
                case.registration_calls.append(scan.copy())
                case.now += case.registration_delay
                return RegistrationEstimate(True, (), seed, {})

        self.session = LocalizationSession(self.binding, Registrar, clock=lambda: self.now)

        class Native:
            def __init__(self, accept):
                self.accept, self.frame_id = accept, 0

            def imu(self, *args):
                case.native_calls.append("imu")

            def frame(self, stamp, points, offsets):
                case.native_calls.append("frame")
                self.frame_id += 1
                packet = case.after_native(PointPacket(
                    self.frame_id, stamp, len(points), points, np.eye(4)))
                if packet is not None:
                    self.accept(packet)
                return np.eye(4), np.array([0., 0., len(points), 0.])

            def close(self):
                case.native_calls.append("close")

        self.native = LocalizedNative(self.session, Native)
        self.addCleanup(self.native.close)

    def scan(self, received):
        points, offsets = np.ones((10, 3)), np.linspace(0, .02, 10)
        points.flags.writeable = offsets.flags.writeable = False
        return Scan(1_700_000_000_000_000_000, points, offsets, received, 320)

    def frame(self, frame, receipt=None):
        return self.native.frame_received(1000. + frame * .1, self.scan(
            self.now if receipt is None else receipt))

    def command(self, action):
        return self.session.request(action, asdict(self.binding), self.session.state()["generation"],
            **({"seed": np.eye(4), "reference_frame": self.session.state()["referenceFrame"]}
               if action == "seed" else {}))

    def tracking(self):
        self.frame(1)
        self.command("seed")
        for frame in (2, 3, 4):
            self.now += .2
            self.frame(frame, self.now - .1)
        self.assertEqual("tracking", self.session.state()["state"])

    def test_receipt_does_not_change_when_native_processing_takes_time(self):
        self.now = 10.4
        self.frame(1, 10.)
        self.assertEqual(10., self.session.latest[1])
        self.assertEqual((1, 10.), self.session.reference)
        self.now = 11.01
        self.assertIsNone(self.session.state()["referenceFrame"])

    def test_tracking_ttl_starts_at_ingress_not_point_callback(self):
        self.tracking()
        self.assertAlmostEqual(10.5, self.session.freshness_at)
        self.now = 11.51
        state = self.session.state()
        self.assertEqual("lost", state["state"])
        self.assertIsNone(state["mapFromSensor"])
        self.assertFalse(state["localizationReady"])
        self.assertFalse(state["navigationReady"])

    def test_completion_after_original_deadline_cannot_create_seed_reference(self):
        def delayed(packet):
            self.now = 11.01
            return packet
        self.after_native = delayed
        self.frame(1, 10.)
        self.assertIsNone(self.session.latest)
        self.assertIsNone(self.session.state()["referenceFrame"])
        self.assertEqual([], self.registration_calls)

    def test_registration_crossing_original_deadline_cannot_publish_pose(self):
        self.tracking()
        self.registration_delay = 1.01
        self.now = 10.7
        self.frame(5, 10.65)
        state = self.session.state()
        self.assertEqual("lost", state["state"])
        self.assertIsNone(state["mapFromSensor"])

    def test_plain_frame_without_ingress_context_never_calls_native(self):
        with self.assertRaisesRegex(RuntimeError, "original_scan_receipt"):
            self.native.frame(1000.1, self.scan(10.).points, self.scan(10.).offsets)
        self.assertEqual([], self.native_calls)

    def test_unsolicited_late_and_post_close_callbacks_cannot_refresh_reference(self):
        packet = PointPacket(1, 1000.1, 10, self.scan(10.).points, np.eye(4))
        with self.assertRaisesRegex(RuntimeError, "current_single_matching"):
            self.native.accept(packet)
        self.frame(1)
        self.now = 10.4
        with self.assertRaisesRegex(RuntimeError, "current_single_matching"):
            self.native.accept(packet)
        self.assertEqual((1, 10.), self.session.reference)
        self.native.close()
        with self.assertRaisesRegex(RuntimeError, "active_localization_native"):
            self.native.accept(packet)
        self.assertTrue(self.session.state()["cleanupComplete"])

    def test_wrong_thread_callback_during_frame_cannot_poison_legitimate_owner(self):
        errors = []

        def concurrent(packet):
            def other():
                try:
                    self.native.accept(packet)
                except RuntimeError as error:
                    errors.append(str(error))
            thread = threading.Thread(target=other)
            thread.start()
            thread.join(2.)
            self.assertFalse(thread.is_alive())
            return packet
        self.after_native = concurrent
        self.frame(1)
        self.assertEqual(["active_localization_native_required"], errors)
        self.assertFalse(self.native.failed)
        self.assertEqual((1, 10.), self.session.reference)

    def test_wrong_stamp_and_duplicate_callback_fail_closed_and_clear_context(self):
        for kind in ("wrong_stamp", "duplicate"):
            with self.subTest(kind=kind):
                if self.native.failed:
                    self.native.close()
                    self.setUp()

                def invalid(packet):
                    if kind == "duplicate":
                        self.native.accept(packet)
                        return packet
                    return replace(packet, algorithm_stamp=packet.algorithm_stamp + .1)
                self.after_native = invalid
                with self.assertRaisesRegex(RuntimeError, "current_single_matching"):
                    self.frame(1)
                self.assertTrue(self.native.failed)
                self.assertIsNone(self.native.current_scan)
                self.assertIsNone(self.session.state()["referenceFrame"])

    def test_failed_native_clears_context_and_cannot_be_retried(self):
        with patch.object(self.native.native, "frame", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.frame(1)
        self.assertIsNone(self.native.current_scan)
        self.assertTrue(self.native.failed)
        with self.assertRaisesRegex(RuntimeError, "active_localization_native"):
            self.frame(2)
        self.native.close()
        self.assertEqual("failed", self.session.state()["state"])

    def test_out_of_order_scan_receipt_cannot_enter_native(self):
        self.frame(1)
        with self.assertRaisesRegex(ValueError, "ordered_original"):
            self.frame(2, 9.9)
        self.assertEqual(["frame"], self.native_calls)
        self.assertIsNone(self.native.current_scan)

    def test_duplicate_scan_stamp_cannot_enter_native(self):
        self.frame(1)
        with self.assertRaisesRegex(ValueError, "ordered_original"):
            self.frame(1)
        self.assertEqual(["frame"], self.native_calls)

    def test_generation_change_during_odometry_discards_old_frame(self):
        for action in ("seed", "pause", "stop"):
            with self.subTest(action=action):
                if self.native.native.frame_id:
                    self.native.close()
                    self.setUp()
                self.frame(1)

                def command_during_native(packet):
                    self.command(action)
                    return packet
                self.after_native = command_during_native
                self.now = 10.1
                self.frame(2)
                state = self.session.state()
                self.assertEqual({"seed": "acquiring", "pause": "paused", "stop": "stopped"}[action],
                                 state["state"])
                self.assertNotEqual(2, state["referenceFrame"])
                self.assertEqual([], self.registration_calls)
                self.assertIsNone(state["mapFromSensor"])
                if action == "seed":
                    self.after_native = lambda packet: packet
                    self.now = 10.2
                    self.frame(3)
                    self.assertEqual(3, self.session.state()["referenceFrame"])
                    self.assertEqual(1, len(self.registration_calls))

    def test_observe_generation_guard_rejects_invalid_or_retired_identity(self):
        self.frame(1)
        packet = PointPacket(2, 1000.2, 10, self.scan(10.).points, np.eye(4))
        for generation in (True, -1, 0., "0"):
            with self.subTest(generation=generation), self.assertRaises(ValueError):
                self.session.observe(packet, session=self.binding.session, input_mode="synthetic",
                    received_at=10., expected_generation=generation)
        self.command("pause")
        self.session.observe(packet, session=self.binding.session, input_mode="synthetic",
                             received_at=10., expected_generation=0)
        self.assertIsNone(self.session.state()["referenceFrame"])


@unittest.skipUnless(AVAILABLE, "requires isolated Linux localization dependencies")
class StreamReceiptComposition(unittest.TestCase):
    def test_worker_queue_imu_wait_and_native_delay_preserve_ingress_receipt(self):
        now, contexts, owners = [10.], [], []
        created, create_release = threading.Event(), threading.Event()
        entered, frame_release, delivered = (threading.Event() for _ in range(3))
        binding = LocalizationBinding(
            "2" * 32, "synthetic", "slam-" + "1" * 32, "3" * 64, "4" * 64, "5" * 64)

        class Registrar:
            def register(self, *args):
                raise AssertionError("unseeded_worker_must_not_register")

        session = LocalizationSession(binding, Registrar, clock=lambda: now[0])

        class Native:
            def __init__(self, accept):
                self.accept = accept

            def imu(self, *args):
                pass

            def frame(self, stamp, points, offsets):
                owners.append(threading.get_ident())
                entered.set()
                if not frame_release.wait(2.):
                    raise RuntimeError("test_frame_gate_timeout")
                self.accept(PointPacket(1, stamp, len(points), points, np.eye(4)))
                contexts.append((session.reference, session.latest[1], points.copy(), offsets.copy()))
                delivered.set()
                return np.eye(4), np.array([0., 0., len(points), 0.])

            def close(self):
                owners.append(threading.get_ident())

        def factory():
            created.set()
            if not create_release.wait(2.):
                raise RuntimeError("test_factory_gate_timeout")
            return replay.OwnedNative(lambda: LocalizedNative(session, Native), lambda _: None,
                                      lambda: None)

        worker = SensorWorker(factory, SensorProfile(
            "synthetic_cloud", "synthetic_imu", "frame_relative_nanoseconds_hypothesis"),
            "header_is_scan_start", clock=lambda: now[0])

        def cleanup():
            create_release.set()
            frame_release.set()
            worker.stop()
        self.addCleanup(cleanup)
        worker.start()
        self.assertTrue(created.wait(2.))
        self.assertTrue(worker.offer_imu(imu()))
        now[0] = 10.05
        message = cloud()
        self.assertTrue(worker.offer_cloud(message))
        message.data[:] = b"\0" * len(message.data)
        now[0] = 10.2
        create_release.set()
        deadline = time.monotonic() + 2.
        while worker.snapshot()["counts"]["completedMessages"] < 2 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(2, worker.snapshot()["counts"]["completedMessages"])
        self.assertFalse(entered.is_set())
        now[0] = 10.3
        for index in range(1, 7):
            self.assertTrue(worker.offer_imu(imu(index * 10)))
        self.assertTrue(entered.wait(2.))
        now[0] = 10.7
        frame_release.set()
        self.assertTrue(delivered.wait(2.))
        reference, receipt, points, offsets = contexts[0]
        self.assertEqual((1, 10.05), reference)
        self.assertEqual(10.05, receipt)
        np.testing.assert_array_equal(points[:, 0], np.arange(1, 11))
        np.testing.assert_allclose(offsets, np.arange(10) * .002)
        self.assertAlmostEqual(.2, worker.snapshot()["maximumObservedQueueAgeSeconds"])
        state = worker.stop()
        self.assertEqual("stopped", state["state"])
        self.assertIsNone(state["failure"])
        self.assertEqual(1, state["stream"]["counts"]["processedScans"])
        self.assertEqual([worker.thread.ident] * 2, owners)
        self.assertNotEqual(threading.get_ident(), owners[0])
        self.assertTrue(session.state()["cleanupComplete"])

    def test_stream_waits_for_imu_and_wrapper_preserves_original_immutable_scan(self):
        contexts, results, calls = [], [], []

        class Native:
            def imu(self, *args):
                pass

            def frame_received(self, stamp, scan):
                contexts.append((stamp, scan))
                return np.eye(4), np.array([0., 0., 10., 0.])

            def close(self):
                pass

        timing = replay.ReplayTiming(10)
        owner = replay.OwnedNative(Native, calls.append, lambda: None, timing=timing)
        stream = SensorStream(owner, "header_is_scan_start", on_frame=lambda *args: results.append(args))
        base = 1_700_000_000_000_000_000
        stream.push_imu(base, [0, 0, 9.81], [0, 0, 0], 10.)
        points, offsets = np.ones((10, 3)), np.linspace(0, .02, 10)
        stream.push_cloud(base + 40_000_000, points, offsets, 10.01)
        points[:] = 99
        self.assertEqual([], contexts)
        for index in range(1, 7):
            stream.push_imu(base + index * 10_000_000, [0, 0, 9.81], [0, 0, 0], 10.02 + index * .01)
        stamp, scan = contexts[0]
        self.assertEqual(1000.04, stamp)
        self.assertEqual(10.01, scan.received_at)
        self.assertEqual(base + 40_000_000, scan.header_ns)
        self.assertFalse(scan.points.flags.writeable)
        self.assertFalse(scan.offsets.flags.writeable)
        np.testing.assert_array_equal(scan.points, np.ones((10, 3)))
        self.assertEqual(4, len(results[0]))
        self.assertEqual(1, calls.count("frame"))
        self.assertEqual(1, timing.snapshot()["channels"]["nativeFrameWall"]["count"])
        stream.finish()
        owner.close()

    def test_receipt_hook_error_does_not_fallback_and_repeat_scan(self):
        calls = []

        class Native:
            def imu(self, *args):
                pass

            def frame_received(self, *args):
                calls.append("receipt")
                raise RuntimeError("injected")

            def frame(self, *args):
                calls.append("fallback")

        owner = replay.OwnedNative(Native, lambda _: None, lambda: None)
        stream = SensorStream(owner, "header_is_scan_start")
        base = 1_700_000_000_000_000_000
        for index in range(7):
            stream.push_imu(base + index * 10_000_000, [0, 0, 9.81], [0, 0, 0], index * .01)
        with self.assertRaisesRegex(StreamError, "native_or_result_consumer_failed"):
            stream.push_cloud(base + 40_000_000, np.ones((10, 3)), np.linspace(0, .02, 10), .1)
        self.assertEqual(["receipt"], calls)
        self.assertEqual("failed", stream.state)


if __name__ == "__main__":
    unittest.main()
