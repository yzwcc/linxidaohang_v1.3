"""Actual GICP lifecycle with independent request/worker race controls."""
from concurrent.futures import Future
from dataclasses import asdict, replace
import importlib.util
from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "tools/tests")]
AVAILABLE = all(importlib.util.find_spec(name) is not None
                for name in ("numpy", "scipy", "small_gicp"))
if AVAILABLE:
    import numpy as np
    from lingxi_slam.localization_session import (
        LocalizationBinding, LocalizationControlError, LocalizationSession, dispatch)
    from lingxi_slam.point_output import PointPacket
    from lingxi_slam.seeded_localization import SeededMapRegistration
    from test_slam_seeded_localization import room, pose

SESSION = "38f3d28801694a91ba030a9029b05eba"


class WorkerHarness:
    """A test owner, not an alternative production process supervisor."""

    def __init__(self, session):
        self.session, self.queue = session, queue.Queue()
        self.thread = threading.Thread(target=self.run, name="test-localization-owner", daemon=True)
        self.thread.start()
        self.call(session.open_worker).result(10)

    def run(self):
        while True:
            callback, future = self.queue.get()
            if callback is None:
                future.set_result(None)
                return
            try:
                future.set_result(callback())
            except BaseException as error:
                future.set_exception(error)

    def call(self, callback):
        future = Future()
        self.queue.put((callback, future))
        return future

    def close(self):
        try:
            self.call(self.session.close_worker).result(10)
        finally:
            self.call(None).result(5)
            self.thread.join(5)
        if self.thread.is_alive():
            raise AssertionError("test worker did not stop")


@unittest.skipUnless(AVAILABLE, "requires isolated localization trial dependencies")
class SessionTests(unittest.TestCase):
    def setUp(self):
        self.now, self.world, self.truth = 10., room(), pose()
        self.binding = LocalizationBinding(
            SESSION, "synthetic", "slam-" + "1" * 32, "2" * 64, "3" * 64, "4" * 64)
        self.lifecycle, self.calls = [], []
        self.entered, self.release = threading.Event(), threading.Event()
        self.block, self.fail_once = False, False
        case = self

        class Registration:
            def __init__(self):
                case.lifecycle.append(("open", threading.get_ident()))
                self.native = SeededMapRegistration(case.world)

            def register(self, scan, seed):
                case.calls.append(threading.get_ident())
                if case.block:
                    case.entered.set()
                    if not case.release.wait(5):
                        raise RuntimeError("test release deadline")
                result = self.native.register(scan, seed)
                if case.fail_once:
                    case.fail_once = False
                    raise RuntimeError("injected old registration failure")
                return result

            def close(self):
                case.lifecycle.append(("close", threading.get_ident()))
                self.native = None

        self.session = LocalizationSession(self.binding, Registration, clock=lambda: self.now)
        self.harness = None

    def tearDown(self):
        self.release.set()
        if self.harness is not None:
            self.harness.close()
        elif self.session.owner is not None and not self.session.worker_closed:
            self.session.close_worker()

    def packet(self, frame):
        odom = self.truth.copy()
        odom[0, 3] += .04 * (frame - 1)
        return PointPacket(frame, 1000. + frame * .1, len(self.world), self.world.copy(), odom)

    def observe(self, frame, **kwargs):
        self.now += .1
        values = dict(session=SESSION, input_mode="synthetic", received_at=self.now)
        values.update(kwargs)
        callback = lambda: self.session.observe(self.packet(frame), **values)
        return callback() if self.harness is None else self.harness.call(callback).result(10)

    def command(self, action, **kwargs):
        values = dict(action=action, binding=asdict(self.binding),
                      expectedGeneration=self.session.state()["generation"])
        if action == "seed":
            values.update(mapFromSensor=self.truth.tolist(),
                          referenceFrame=self.session.state()["referenceFrame"])
        values.update(kwargs)
        return dispatch(self.session, values)

    def tick(self):
        if self.harness is None:
            self.session.tick()
        else:
            self.harness.call(self.session.tick).result(10)

    def tracking(self, *, threaded=False):
        if threaded:
            self.harness = WorkerHarness(self.session)
        else:
            self.session.open_worker()
        self.observe(1)
        self.assertEqual([], self.calls)
        self.command("seed")
        for frame in (2, 3, 4):
            state = self.observe(frame)
        self.assertEqual("tracking", state["state"])
        np.testing.assert_allclose(state["mapFromSensor"], self.packet(4).world_lidar_pose,
                                   atol=.005, rtol=0)
        return state

    def base_projection(self, *, threaded=False):
        from lingxi_slam.base_pose import BaseLidarTransform, BasePoseProjector, ScanTime
        self.session = LocalizationSession(self.binding, self.session.factory, clock=lambda: self.now,
                                           base_metres_per_map_unit=1.)
        self.tracking(threaded=threaded)
        tf = np.eye(4)
        tf[0, 3] = .25
        projector = BasePoseProjector(lambda stamp: BaseLidarTransform(stamp, tf), metres_per_map_unit=1.)
        timing = ScanTime(1_700_000_000_400_000_000, 18_000_000,
                          1_700_000_000_000_000_000, "header_is_scan_start")
        return timing, projector

    def test_base_projection_is_owned_current_frame_full_3d_and_clears_on_next_frame(self):
        timing, projector = self.base_projection()
        self.session.project_base(timing, projector, algorithm_stamp=1000.4)
        state = self.session.state()
        self.assertEqual("available", state["basePose"]["status"])
        self.assertEqual(4, state["basePose"]["lastFrame"])
        self.assertEqual(str(timing.effective_ns), state["basePose"]["effectiveScanNs"])
        expected = np.array(state["mapFromSensor"])
        expected[:3, 3] -= expected[:3, 0] * .25
        np.testing.assert_allclose(state["basePose"]["mapFromBase"], expected, atol=1e-12)
        state["basePose"]["mapFromBase"][0][3] = 999
        self.assertNotEqual(999, self.session.state()["basePose"]["mapFromBase"][0][3])
        self.assertIsNone(self.observe(5)["basePose"]["mapFromBase"])

    def test_base_projection_missing_tf_does_not_cancel_lidar_tracking_or_enable_navigation(self):
        from lingxi_slam.base_pose import BasePoseProjector
        timing, _ = self.base_projection()
        self.session.project_base(timing, BasePoseProjector(lambda _: None, metres_per_map_unit=1.),
                                  algorithm_stamp=1000.4)
        state = self.session.state()
        self.assertEqual("tracking", state["state"])
        self.assertEqual("unavailable", state["basePose"]["status"])
        self.assertFalse(state["navigationReady"])
        self.assertFalse(state["basePose"]["physicalScaleValidated"])

    def test_base_projection_expiry_withdraws_both_pose_caches(self):
        timing, projector = self.base_projection()
        self.session.project_base(timing, projector, algorithm_stamp=1000.4)
        self.now += 2
        state = self.session.state()
        self.assertIsNone(state["mapFromSensor"])
        self.assertIsNone(state["basePose"]["mapFromBase"])
        self.assertEqual("lost", state["state"])

    def test_inflight_base_projection_cannot_publish_after_pause_or_tf_revocation(self):
        from lingxi_slam.base_pose import BaseLidarTransform, BasePoseProjector
        for action in ("pause", "tf_revoke"):
            if self.harness is not None:
                self.harness.close()
                self.harness = None
            self.entered.clear()
            self.release.clear()
            self.calls.clear()
            timing, _ = self.base_projection(threaded=True)
            def lookup(stamp):
                self.entered.set()
                if not self.release.wait(5):
                    raise RuntimeError("synthetic projection wait")
                return BaseLidarTransform(stamp, np.eye(4))
            projector = BasePoseProjector(lookup, metres_per_map_unit=1.)
            future = self.harness.call(lambda: self.session.project_base(timing, projector, algorithm_stamp=1000.4))
            self.assertTrue(self.entered.wait(5))
            if action == "pause":
                self.command("pause")
            else:
                self.session.revoke_base("invalid_kinematic_tf")
            self.assertIsNone(self.session.state()["basePose"]["mapFromBase"])
            self.release.set()
            future.result(10)
            state = self.session.state()
            self.assertIsNone(state["basePose"]["mapFromBase"])
            self.assertEqual("paused" if action == "pause" else "tracking", state["state"])

    def test_actual_native_seed_is_applied_only_on_worker_and_confirms_three_frames(self):
        self.session.open_worker()
        self.observe(1)
        requested = self.command("seed")
        self.assertEqual("seed_requested", requested["state"])
        self.assertTrue(requested["requestPending"])
        self.assertEqual([], self.calls)
        for frame in (2, 3, 4, 5):
            state = self.observe(frame)
            self.assertEqual("acquiring" if frame < 4 else "tracking", state["state"])
            self.assertEqual(asdict(self.binding), state["binding"])
            self.assertFalse(state["localizationReady"])
            self.assertFalse(state["navigationReady"])
        self.assertEqual([threading.get_ident()] * 4, self.calls)

    def test_binding_change_and_stale_compare_and_set_cannot_control_replacement(self):
        current = self.tracking()
        for key, replacement in (("session", "a" * 32), ("input_mode", "live"),
                                 ("map_id", "slam-" + "b" * 32), ("receipt_sha256", "c" * 64),
                                 ("points_sha256", "d" * 64), ("profile_sha256", "e" * 64)):
            changed = asdict(self.binding)
            changed[key] = replacement
            with self.subTest(key=key), self.assertRaisesRegex(
                    LocalizationControlError, "localization_binding_changed"):
                self.command("pause", binding=changed)
        self.assertEqual("tracking", self.session.state()["state"])
        self.command("pause")
        with self.assertRaisesRegex(LocalizationControlError, "localization_generation_changed"):
            self.command("stop", expectedGeneration=current["generation"])
        self.assertEqual("paused", self.session.state()["state"])

    def test_wrong_session_or_input_mode_cannot_supply_seed_reference_or_refresh_pose(self):
        self.session.open_worker()
        self.observe(1, session="0" * 32)
        self.observe(2, input_mode="live")
        self.assertIsNone(self.session.state()["referenceFrame"])
        self.command("seed", referenceFrame=2)
        self.tick()
        self.assertEqual("lost", self.session.state()["state"])
        self.assertEqual([], self.calls)

    def test_missing_changed_stale_and_delayed_seed_reference_fail_without_registration(self):
        for kind in ("missing", "changed", "stale", "delayed"):
            with self.subTest(kind=kind):
                session = LocalizationSession(self.binding, lambda: SeededMapRegistration(self.world),
                                              clock=lambda: self.now)
                session.open_worker()
                try:
                    if kind != "missing":
                        session.observe(self.packet(1), session=SESSION, input_mode="synthetic",
                                        received_at=self.now)
                    if kind == "stale":
                        self.now += 1.01
                    dispatch(session, dict(
                        action="seed", binding=asdict(self.binding), expectedGeneration=0,
                        mapFromSensor=self.truth.tolist(), referenceFrame=2 if kind == "changed" else 1))
                    if kind == "delayed":
                        self.now += 1.01
                    session.tick()
                    state = session.state()
                    self.assertEqual("lost", state["state"])
                    self.assertEqual("fresh_matching_seed_reference_required", state["reason"])
                    self.assertIsNone(state["mapFromSensor"])
                finally:
                    session.close_worker()

    def test_pause_during_actual_native_call_revokes_immediately_and_cannot_auto_resume(self):
        self.tracking(threaded=True)
        self.block = True
        pending = self.harness.call(lambda: self.session.observe(
            self.packet(5), session=SESSION, input_mode="synthetic", received_at=self.now))
        try:
            self.assertTrue(self.entered.wait(5))
            paused = self.command("pause")
            self.assertEqual("paused", paused["state"])
            self.assertIsNone(paused["mapFromSensor"])
            self.assertTrue(paused["requestPending"])
        finally:
            self.release.set()
        self.assertEqual("paused", pending.result(10)["state"])
        for frame in (6, 7, 8):
            self.assertEqual("paused", self.observe(frame)["state"])
        self.assertEqual(4, len(self.calls))
        self.command("seed")
        for frame in (9, 10, 11):
            state = self.observe(frame)
        self.assertEqual("tracking", state["state"])
        self.assertEqual([self.harness.thread.ident] * len(self.calls), self.calls)

    def test_reseed_during_native_call_hides_old_result_and_new_confirmations_start_at_zero(self):
        self.tracking(threaded=True)
        self.block = True
        pending = self.harness.call(lambda: self.session.observe(
            self.packet(5), session=SESSION, input_mode="synthetic", received_at=self.now))
        try:
            self.assertTrue(self.entered.wait(5))
            requested = self.command("seed")
            self.assertEqual(5, requested["referenceFrame"])
            self.assertEqual(0, requested["consecutiveMatches"])
            self.assertIsNone(requested["mapFromSensor"])
        finally:
            self.release.set()
        self.assertEqual("seed_requested", pending.result(10)["state"])
        for frame in (6, 7, 8):
            state = self.observe(frame)
            self.assertEqual(frame - 5, state["consecutiveMatches"])
            self.assertEqual("tracking" if frame == 8 else "acquiring", state["state"])

    def test_stop_is_terminal_and_native_cleanup_has_same_owner(self):
        self.tracking(threaded=True)
        self.block = True
        pending = self.harness.call(lambda: self.session.observe(
            self.packet(5), session=SESSION, input_mode="synthetic", received_at=self.now))
        try:
            self.assertTrue(self.entered.wait(5))
            state = self.command("stop")
            self.assertEqual("stopped", state["state"])
            self.assertIsNone(state["mapFromSensor"])
        finally:
            self.release.set()
        self.assertEqual("stopped", pending.result(10)["state"])
        self.observe(6)
        with self.assertRaisesRegex(LocalizationControlError, "localization_session_closed"):
            self.command("seed", referenceFrame=6)
        owner = self.harness.thread.ident
        self.harness.close()
        self.harness = None
        self.assertEqual([("open", owner), ("close", owner)], self.lifecycle)
        self.assertTrue(self.session.state()["workerClosed"])
        self.assertTrue(self.session.state()["cleanupComplete"])

    def test_runtime_stop_during_point_copy_cannot_restore_seed_reference(self):
        self.tracking(threaded=True)
        from lingxi_slam.localization_session import arrays

        def blocked_arrays(packet):
            self.entered.set()
            if not self.release.wait(5):
                raise RuntimeError("point copy release deadline")
            return arrays(packet)

        with patch("lingxi_slam.localization_session.arrays", side_effect=blocked_arrays):
            pending = self.harness.call(lambda: self.session.observe(
                self.packet(5), session=SESSION, input_mode="synthetic", received_at=self.now))
            try:
                self.assertTrue(self.entered.wait(5))
                self.session.stop_admission()
                stopped = self.session.state()
                self.assertEqual("stopped", stopped["state"])
                self.assertIsNone(stopped["referenceFrame"])
                self.assertIsNone(stopped["mapFromSensor"])
            finally:
                self.release.set()
            state = pending.result(10)
        self.assertEqual("stopped", state["state"])
        self.assertIsNone(state["referenceFrame"])
        self.assertIsNone(state["mapFromSensor"])
        self.assertEqual(3, len(self.calls))

    def test_latest_request_replaces_pending_command_without_running_old_seed(self):
        self.session.open_worker()
        self.observe(1)
        self.command("seed")
        self.command("pause")
        self.tick()
        self.assertEqual("paused", self.session.state()["state"])
        self.assertEqual([], self.calls)
        self.assertFalse(self.session.state()["requestPending"])
        self.observe(2)
        self.command("seed")
        self.command("stop")
        self.tick()
        self.assertEqual([], self.calls)

    def test_status_expiry_while_registration_is_blocked_cannot_be_undone_by_late_result(self):
        self.tracking(threaded=True)
        self.block = True
        pending = self.harness.call(lambda: self.session.observe(
            self.packet(5), session=SESSION, input_mode="synthetic", received_at=self.now))
        try:
            self.assertTrue(self.entered.wait(5))
            self.now += 1.01
            self.assertEqual("lost", dispatch(self.session, {"action": "state"})["state"])
            self.assertIsNone(self.session.state()["mapFromSensor"])
        finally:
            self.release.set()
        self.assertEqual("lost", pending.result(10)["state"])
        self.assertEqual("lost", self.observe(6)["state"])

    def test_old_native_exception_after_reseed_cannot_fail_new_request(self):
        self.tracking(threaded=True)
        self.block = self.fail_once = True
        pending = self.harness.call(lambda: self.session.observe(
            self.packet(5), session=SESSION, input_mode="synthetic", received_at=self.now))
        try:
            self.assertTrue(self.entered.wait(5))
            self.command("seed")
        finally:
            self.release.set()
        self.assertEqual("seed_requested", pending.result(10)["state"])
        for frame in (6, 7, 8):
            state = self.observe(frame)
        self.assertEqual("tracking", state["state"])

    def test_idle_tick_expires_without_new_points_or_request_polling(self):
        self.tracking()
        self.now += 1.01
        self.tick()
        self.assertEqual("lost", self.session.state()["state"])
        self.assertIsNone(self.session.state()["mapFromSensor"])
        self.assertEqual(3, len(self.calls))

    def test_duplicate_packets_cannot_keep_a_tracking_session_fresh(self):
        self.tracking()
        for _ in range(11):
            state = self.observe(4)
        self.assertEqual("lost", state["state"])
        self.assertIsNone(state["mapFromSensor"])

    def test_reference_and_published_pose_are_owned_copies(self):
        self.session.open_worker()
        packet = self.packet(1)
        self.session.observe(packet, session=SESSION, input_mode="synthetic", received_at=self.now)
        packet.world_lidar_pose[:] = 0
        packet.points[:] = np.nan
        self.command("seed")
        for frame in (2, 3, 4):
            state = self.observe(frame)
        self.assertEqual("tracking", state["state"])
        state["mapFromSensor"][0][0] = 0
        state["binding"]["map_id"] = "mutated"
        self.assertGreater(self.session.state()["mapFromSensor"][0][0], .5)
        self.assertEqual(self.binding.map_id, self.session.state()["binding"]["map_id"])

    def test_invalid_requests_never_change_current_tracking_generation(self):
        current = self.tracking()
        cases = [
            None, [], {"action": "resume"}, {"action": "state", "extra": 1},
            dict(action="pause", binding=asdict(self.binding), expectedGeneration=True),
            dict(action="seed", binding=asdict(self.binding), expectedGeneration=current["generation"],
                 mapFromSensor=[[True] * 4] * 4, referenceFrame=4),
            dict(action="seed", binding=asdict(self.binding), expectedGeneration=current["generation"],
                 mapFromSensor=np.eye(4).tolist(), referenceFrame=True),
        ]
        for seed in (np.zeros((4, 4)).tolist(), [[float("nan")] * 4] * 4,
                     [["1"] * 4] * 4, [[10**500] * 4] * 4):
            cases.append(dict(action="seed", binding=asdict(self.binding),
                              expectedGeneration=current["generation"],
                              mapFromSensor=seed, referenceFrame=4))
        for value in cases:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(
                    LocalizationControlError) as caught:
                dispatch(self.session, value)
            self.assertEqual(400, caught.exception.status)
            self.assertEqual(current["generation"], self.session.state()["generation"])
            self.assertEqual("tracking", self.session.state()["state"])

    def test_invalid_or_stale_point_input_revokes_pose(self):
        self.tracking()
        state = self.session.observe(replace(self.packet(5), points=np.full((100, 3), np.nan)),
                                     session=SESSION, input_mode="synthetic", received_at=self.now)
        self.assertEqual("lost", state["state"])
        self.assertIsNone(state["mapFromSensor"])
        self.assertEqual(3, len(self.calls))

    def test_native_and_cleanup_methods_reject_request_handler_thread(self):
        self.tracking(threaded=True)
        for callback in (self.session.tick, self.session.close_worker, self.session.open_worker,
                         lambda: self.session.observe(self.packet(5), session=SESSION,
                                                      input_mode="synthetic", received_at=self.now)):
            with self.subTest(callback=callback), self.assertRaises(RuntimeError):
                callback()
        self.assertEqual(3, len(self.calls))

    def test_clock_reversal_revokes_without_request_thread_native_access(self):
        self.tracking(threaded=True)
        self.now -= .1
        state = self.session.state()
        self.assertEqual("failed", state["state"])
        self.assertIsNone(state["mapFromSensor"])
        self.assertEqual(3, len(self.calls))
        self.now += .2

    def test_failed_factory_cleanup_and_invalid_binding_do_not_enable_worker(self):
        with self.assertRaises(ValueError):
            replace(self.binding, profile_sha256="invalid")
        with self.assertRaises(ValueError):
            replace(self.binding, map_id="../map")

        class Invalid:
            def close(inner):
                self.lifecycle.append(("invalid_closed", threading.get_ident()))

        session = LocalizationSession(self.binding, Invalid, clock=lambda: self.now)
        with self.assertRaises(ValueError):
            session.open_worker()
        state = session.state()
        self.assertTrue(state["workerClosed"])
        self.assertEqual("failed", state["state"])
        self.assertEqual([("invalid_closed", threading.get_ident())], self.lifecycle)

    def test_cleanup_failure_is_reported_without_claiming_cleanup_complete(self):
        class BrokenClose:
            def register(inner, *args):
                raise AssertionError("unused")

            def close(inner):
                raise RuntimeError("injected cleanup failure")

        session = LocalizationSession(self.binding, BrokenClose, clock=lambda: self.now)
        session.open_worker()
        with self.assertRaisesRegex(RuntimeError, "injected cleanup failure"):
            session.close_worker()
        state = session.state()
        self.assertEqual("failed", state["state"])
        self.assertEqual("localization_worker_cleanup_failed", state["reason"])
        self.assertFalse(state["cleanupComplete"])
        self.assertIsNone(state["mapFromSensor"])

    def test_stop_during_native_factory_cannot_activate_or_seed_session(self):
        entered, release = threading.Event(), threading.Event()
        case = self

        def factory():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("factory release deadline")
            return SeededMapRegistration(case.world)

        session = LocalizationSession(self.binding, factory, clock=lambda: self.now)
        work = queue.Queue()

        def owner():
            try:
                session.open_worker()
                session.close_worker()
                work.put(None)
            except BaseException as error:
                work.put(error)

        thread = threading.Thread(target=owner, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            dispatch(session, dict(action="stop", binding=asdict(self.binding), expectedGeneration=0))
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(work.get_nowait())
        self.assertEqual("stopped", session.state()["state"])
        self.assertTrue(session.state()["cleanupComplete"])


if __name__ == "__main__":
    unittest.main()
