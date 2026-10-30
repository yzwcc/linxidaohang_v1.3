"""Real registration controls plus isolated recorded-composition failure checks."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools/tests"))
AVAILABLE = sys.platform == "linux" and all(
    importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "small_gicp"))
if AVAILABLE:
    import numpy as np
    import run_recorded_localization_trial as trial
    from lingxi_slam.point_output import PointPacket
    from lingxi_slam.seeded_localization import RegistrationEstimate
    from test_slam_seeded_localization import room, pose

SESSION = "38f3d28801694a91ba030a9029b05eba"


@unittest.skipUnless(AVAILABLE, "requires isolated Linux localization trial dependencies")
class RecordedTrackingTests(unittest.TestCase):
    def setUp(self):
        self.world, self.pose, self.now = room(), pose(), 10.
        self.summary = trial.TrackingSummary(
            self.world, self.pose, {"frameId": 1, "algorithmStamp": 1000.1,
                                    "copiedPoints": len(self.world)},
            SESSION, clock=lambda: self.now)

    def packet(self, frame):
        odom = self.pose.copy()
        odom[0, 3] += .04 * (frame - 1)
        return PointPacket(frame, 1000. + frame * .1, len(self.world), self.world.copy(), odom)

    def accept(self, frame):
        self.now += .1
        self.summary.accept(self.packet(frame))

    def finish(self, count):
        output = SimpleNamespace(closed=True, counts=lambda: {
            "callbacks": count + 1, "initializationSkips": 1,
            "copies": count, "takes": count, "discardedOnClose": 0})
        self.summary.finish(output)
        return self.summary.tracking_report(count + 1)

    def complete(self, count=6):
        self.summary.begin()
        for frame in range(1, count + 1):
            self.accept(frame)
        return self.finish(count)

    def test_actual_gicp_controls_use_all_frames_without_reseeding_or_exporting_poses(self):
        report = self.complete()
        trial.verified_tracking(report, 6)
        for control in report["controls"][:2]:
            self.assertEqual({"acquiring": 2, "tracking": 4}, control["stateCounts"])
            self.assertEqual(3, control["firstTrackingFrame"])
            self.assertEqual(6, control["registration"]["accepted"])
            self.assertEqual("stopped", control["afterExplicitStop"]["state"])
        negative = report["controls"][2]
        self.assertEqual({"lost": 6}, negative["stateCounts"])
        self.assertEqual(1, negative["registration"]["calls"])
        self.assertEqual(1, negative["firstLostFrame"])
        self.assertEqual(0, negative["registration"]["accepted"])
        encoded = json.dumps(report, allow_nan=False)
        self.assertNotIn("world_lidar_pose", encoded)
        self.assertNotIn("map_from_sensor", encoded)
        self.assertFalse(any(value["lastObservation"]["navigationReady"] for value in report["controls"]))
        self.assertIsNone(self.summary.map_points)
        self.assertEqual(1, self.summary.first.native_frame_id)
        self.assertEqual(6, self.summary.latest.native_frame_id)

    def test_expiration_during_sequence_stays_lost_and_does_not_repeat_registration(self):
        self.summary.begin()
        for frame in (1, 2, 3):
            self.accept(frame)
        self.now += 1.01
        for frame in (4, 5, 6):
            self.accept(frame)
        report = self.finish(6)
        trial.verified_tracking(report, 6)
        for control in report["controls"][:2]:
            self.assertEqual({"acquiring": 2, "tracking": 1, "lost": 3}, control["stateCounts"])
            self.assertEqual(4, control["firstLostFrame"])
            self.assertEqual("corrected_point_stream_expired", control["firstLossReason"])
            self.assertEqual(3, control["registration"]["calls"])
            self.assertFalse(control["lastObservation"]["hasDiagnosticPose"])

    def test_seed_reference_must_match_first_frame_stamp_and_size_before_acceptance(self):
        for kind in ("frame", "stamp", "size"):
            with self.subTest(kind=kind):
                self.setUp()
                self.summary.begin()
                packet = self.packet(1)
                if kind == "frame":
                    packet = replace(packet, native_frame_id=2)
                elif kind == "stamp":
                    packet = replace(packet, algorithm_stamp=1001.)
                else:
                    packet = replace(packet, points=packet.points[::2])
                with self.assertRaisesRegex(ValueError, "seed_reference"):
                    self.summary.accept(packet)
                self.assertEqual(0, self.summary.count)
                self.assertTrue(all(value["meter"].calls == 0 for value in self.summary.controls))

    def test_duplicate_gap_and_time_reversal_cannot_hide_missing_input(self):
        for kind in ("duplicate", "gap", "time"):
            with self.subTest(kind=kind):
                self.setUp()
                self.summary.begin()
                self.accept(1)
                packet = self.packet(1 if kind == "duplicate" else 3 if kind == "gap" else 2)
                if kind == "time":
                    packet = replace(packet, algorithm_stamp=1000.)
                with self.assertRaises(ValueError):
                    self.summary.accept(packet)
                self.assertEqual(1, self.summary.count)

    def test_worker_owner_must_close_and_no_callbacks_after_close(self):
        self.summary.begin()
        self.accept(1)
        errors = []

        def wrong_thread():
            try:
                self.finish(1)
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(5)
        self.assertEqual(["active_same_thread_point_summary_required"], errors)
        self.finish(1)
        with self.assertRaises(RuntimeError):
            self.accept(2)

    def test_quality_rejection_stops_calls_without_hiding_remaining_packets(self):
        class Reject:
            def register(self, scan, seed):
                return RegistrationEstimate(False, ("insufficient_scan_map_overlap",), None, {})
        self.summary.registrar_factory = lambda _: Reject()
        report = self.complete()
        trial.verified_tracking(report, 6)
        for control in report["controls"]:
            self.assertEqual({"lost": 6}, control["stateCounts"])
            self.assertEqual(1, control["registration"]["calls"])

    def test_aggregate_rejects_readiness_unknown_fields_hidden_recovery_or_lost_input(self):
        report = self.complete()
        for kind in ("readiness", "count", "reseed", "pose", "metric", "cleanup", "recovery", "extra"):
            value = deepcopy(report)
            control = value["controls"][0]
            if kind == "readiness":
                control["lastObservation"]["navigationReady"] = True
            elif kind == "count":
                control["observedPackets"] -= 1
            elif kind == "reseed":
                value["seedAttemptsPerControl"] = 2
            elif kind == "pose":
                control["lastObservation"]["map_from_sensor"] = [[1]]
            elif kind == "metric":
                control["registration"]["metrics"]["milliseconds"]["p95"] = float("nan")
            elif kind == "cleanup":
                control["afterExplicitStop"]["state"] = "tracking"
            elif kind == "recovery":
                control["firstLostFrame"], control["firstLossReason"] = 4, "registration_exception"
                control["stateCounts"] = {"acquiring": 2, "tracking": 3, "lost": 1}
            else:
                control["private_path"] = "/private/recording"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                trial.verified_tracking(value, 6)

    def test_native_cleanup_requires_all_post_initialization_packets(self):
        self.complete()
        with self.assertRaises(ValueError):
            self.summary.tracking_report(8)

    def test_metrics_are_bounded_and_do_not_retain_estimates(self):
        class Registrar:
            def register(self, *_):
                return RegistrationEstimate(True, (), np.eye(4), {"milliseconds": float("inf")})
        meter = trial.MeteredRegistration(Registrar())
        with self.assertRaises(ValueError):
            meter.register(None, None)
        meter.calls = 20_000
        with self.assertRaisesRegex(ValueError, "bounded"):
            meter.register(None, None)

    def test_known_statistics_and_empty_metrics(self):
        self.assertEqual({"count": 4, "minimum": 1, "p50": 2, "p95": 4, "maximum": 4},
                         trial.statistics([4, 1, 3, 2]))
        self.assertEqual({"count": 0, "minimum": None, "p50": None, "p95": None, "maximum": None},
                         trial.statistics([]))

    def test_unrelated_recording_cannot_supply_initial_pose(self):
        args = SimpleNamespace(point_reference=Path("/private/point-reference"))
        with patch.object(trial.points, "evidence", return_value={}), patch.object(
                trial.replay, "load", return_value={"inputIntegrity": {"journalSha256": "a"}}):
            with self.assertRaisesRegex(ValueError, "same_frozen_recording"):
                trial.seed_reference(args, {"journalSha256": "b"})


if __name__ == "__main__":
    unittest.main()
