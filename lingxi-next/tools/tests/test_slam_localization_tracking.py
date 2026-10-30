"""Tracking uses actual GICP plus independent lifecycle fault injection."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
sys.path.insert(0, str(ROOT / "tools/tests"))
AVAILABLE = all(importlib.util.find_spec(name) is not None
                for name in ("numpy", "scipy", "small_gicp"))
if AVAILABLE:
    import numpy as np
    from lingxi_slam.localization_tracking import LocalizationTracker
    from lingxi_slam.point_output import PointPacket
    from lingxi_slam.seeded_localization import RegistrationEstimate, SeededMapRegistration
    from test_slam_seeded_localization import room, pose

SESSION = "38f3d28801694a91ba030a9029b05eba"


@unittest.skipUnless(AVAILABLE, "requires isolated localization trial dependencies")
class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.world = room()
        self.truth = pose()
        self.registrar = SeededMapRegistration(self.world)
        self.tracker = LocalizationTracker(self.registrar, session=SESSION, input_mode="synthetic",
                                           clock=lambda: self.now)

    def packet(self, frame):
        odom = self.truth.copy()
        odom[0, 3] += .04 * (frame - 1)
        # Keep the static world surfaces visible while the synthetic sensor moves.
        return PointPacket(frame, 1000. + frame * .1, len(self.world), self.world.copy(), odom)

    def seed(self):
        seed = self.truth.copy()
        seed[:3, 3] += [.1, -.1, .05]
        return self.tracker.begin(seed, self.packet(1), received_at=self.now)

    def observe(self, frame, generation):
        self.now += .1
        return self.tracker.observe(self.packet(frame), session=SESSION, generation=generation,
                                    received_at=self.now)

    def tracking(self):
        generation = self.seed()
        for frame in (1, 2, 3):
            result = self.observe(frame, generation)
        self.assertEqual("tracking", result.state)
        return generation, result

    def test_actual_native_continuous_tracking_uses_odom_prediction_and_confirmations(self):
        self.assertEqual("unseeded", self.tracker.snapshot().state)
        generation = self.seed()
        for frame in range(1, 7):
            result = self.observe(frame, generation)
            self.assertFalse(result.localization_ready)
            self.assertFalse(result.navigation_ready)
            if frame < 3:
                self.assertIsNone(result.map_from_sensor)
                self.assertEqual("acquiring", result.state)
            else:
                self.assertEqual("tracking", result.state)
                self.assertEqual(3, result.consecutive_matches)
                np.testing.assert_allclose(result.map_from_sensor, self.packet(frame).world_lidar_pose,
                                           atol=.005, rtol=0)

    def test_timeout_revokes_pose_and_requires_explicit_reseed(self):
        generation, _ = self.tracking()
        self.now += 1.01
        expired = self.tracker.snapshot()
        self.assertEqual("lost", expired.state)
        self.assertIsNone(expired.map_from_sensor)
        self.assertEqual("lost", self.observe(4, generation).state)
        self.assertGreater(self.seed(), generation)
        self.assertEqual("acquiring", self.tracker.snapshot().state)

    def test_old_generation_wrong_session_and_duplicates_do_not_refresh_track(self):
        generation, _ = self.tracking()
        self.seed()
        result = self.observe(4, generation)
        self.assertEqual(0, result.consecutive_matches)
        result = self.tracker.observe(self.packet(4), session="0" * 32,
                                      generation=generation + 1, received_at=self.now)
        self.assertEqual(0, result.consecutive_matches)
        self.observe(4, generation + 1)
        self.now += .8
        duplicate = self.tracker.observe(self.packet(4), session=SESSION, generation=generation + 1,
                                         received_at=self.now)
        self.assertEqual(1, duplicate.consecutive_matches)
        self.now += .21
        self.assertEqual("lost", self.tracker.snapshot().state)

    def test_gap_invalid_input_and_registration_failure_clear_previous_pose(self):
        for kind in ("gap", "input", "rejected", "exception"):
            with self.subTest(kind=kind):
                self.setUp()
                generation, _ = self.tracking()
                packet = self.packet(4)
                if kind == "gap":
                    packet = replace(packet, algorithm_stamp=1002.)
                elif kind == "input":
                    packet = replace(packet, points=np.full((100, 3), np.nan))
                elif kind == "rejected":
                    self.tracker.registrar = type("Reject", (), {
                        "register": lambda *args: RegistrationEstimate(False, ("no_overlap",), None, {})})()
                else:
                    def fail(*args):
                        raise RuntimeError("synthetic native error")
                    self.tracker.registrar = type("Fail", (), {"register": fail})()
                self.now += .1
                result = self.tracker.observe(packet, session=SESSION, generation=generation,
                                              received_at=self.now)
                self.assertEqual("lost", result.state)
                self.assertIsNone(result.map_from_sensor)
                self.assertEqual(0, result.consecutive_matches)

    def test_result_finishing_after_deadline_cannot_publish_pose(self):
        generation, _ = self.tracking()

        def slow(scan, seed):
            self.now += 1.01
            return RegistrationEstimate(True, (), self.truth, {})
        self.tracker.registrar = type("Slow", (), {"register": staticmethod(slow)})()
        result = self.observe(4, generation)
        self.assertEqual("lost", result.state)
        self.assertEqual("registration_completed_after_freshness_deadline", result.reason)
        self.assertIsNone(result.map_from_sensor)

    def test_stop_invalid_seed_and_clock_reversal_revoke_previous_generation(self):
        generation, _ = self.tracking()
        stopped = self.tracker.stop()
        self.assertEqual("stopped", stopped.state)
        self.assertGreater(stopped.generation, generation)
        self.assertEqual("stopped", self.observe(4, generation).state)
        self.seed()
        with self.assertRaises(ValueError):
            self.tracker.begin(np.zeros((4, 4)), self.packet(1), received_at=self.now)
        self.assertEqual("lost", self.tracker.snapshot().state)
        self.seed()
        self.now -= 1
        with self.assertRaises(ValueError):
            self.tracker.snapshot()
        self.now += 1
        self.assertEqual("lost", self.tracker.snapshot().state)

    def test_future_or_old_receipt_cannot_refresh_state(self):
        for offset in (-1.01, .01, float("nan"), True):
            with self.subTest(offset=offset):
                self.setUp()
                generation = self.seed()
                received = offset if offset is True else self.now + offset
                result = self.tracker.observe(self.packet(1), session=SESSION, generation=generation,
                                              received_at=received)
                self.assertEqual("lost", result.state)
                self.assertIsNone(result.map_from_sensor)

    def test_frames_older_than_seed_reference_cannot_start_new_generation(self):
        generation = self.tracker.begin(self.truth, self.packet(10), received_at=self.now)
        result = self.observe(9, generation)
        self.assertEqual(0, result.consecutive_matches)
        self.assertIsNone(result.map_from_sensor)
        result = self.observe(10, generation)
        self.assertEqual(1, result.consecutive_matches)

    def test_generation_revoked_during_registration_cannot_publish_old_result(self):
        generation = self.seed()

        def reseed(scan, seed):
            self.tracker.begin(self.truth, self.packet(10), received_at=self.now)
            return RegistrationEstimate(True, (), self.truth, {})
        self.tracker.registrar = type("Reseed", (), {"register": staticmethod(reseed)})()
        result = self.observe(1, generation)
        self.assertEqual(generation + 1, result.generation)
        self.assertEqual(0, result.consecutive_matches)
        self.assertIsNone(result.map_from_sensor)

    def test_first_observation_must_remain_close_to_seed_reference(self):
        generation = self.seed()
        result = self.observe(10, generation)
        self.assertEqual("lost", result.state)
        self.assertEqual("corrected_point_time_gap_or_reversal", result.reason)
        self.assertIsNone(result.map_from_sensor)

    def test_old_registration_exception_cannot_revoke_replacement_seed(self):
        generation = self.seed()

        def reseed_then_fail(scan, seed):
            self.tracker.begin(self.truth, self.packet(10), received_at=self.now)
            raise RuntimeError("old registration failed after reseed")
        self.tracker.registrar = type("ReseedFail", (), {
            "register": staticmethod(reseed_then_fail)})()
        result = self.observe(1, generation)
        self.assertEqual(generation + 1, result.generation)
        self.assertEqual("acquiring", result.state)
        self.assertEqual(0, result.consecutive_matches)

    def test_tracking_rejects_nonowner_access(self):
        generation, _ = self.tracking()
        errors = []

        def call():
            try:
                self.observe(4, generation)
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=call)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(["single_localization_tracking_owner_required"], errors)

    def test_published_pose_is_an_owned_copy(self):
        _, first = self.tracking()
        first.map_from_sensor.flags.writeable = True
        first.map_from_sensor[:] = 0
        second = self.tracker.snapshot()
        self.assertGreater(np.linalg.det(second.map_from_sensor[:3, :3]), .99)


if __name__ == "__main__":
    unittest.main()
