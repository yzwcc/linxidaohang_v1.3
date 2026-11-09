"""Regression: slow saved-map matching must not stall raw sensor ingestion."""
from dataclasses import asdict
import importlib.util
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "robot-runtime/python"), str(ROOT / "tools")]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    import numpy as np
    from localization_control import LocalizedNative
    from lingxi_slam.localization_session import LocalizationBinding, LocalizationSession
    from lingxi_slam.point_output import PointPacket
    from lingxi_slam.seeded_localization import RegistrationEstimate
    from lingxi_slam.stream import Scan


@unittest.skipUnless(AVAILABLE, "requires isolated numerical dependencies")
class AsyncLocalization(unittest.TestCase):
    def setUp(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.block = False
        self.lifecycle, self.native_calls, self.frames = [], [], []
        case = self

        class Registrar:
            def __init__(self):
                case.lifecycle.append(("create", threading.get_ident()))

            def register(self, scan, seed):
                if case.block:
                    case.entered.set()
                    if not case.release.wait(4):
                        raise RuntimeError("test_release_deadline")
                return RegistrationEstimate(True, (), seed.copy(), {})

            def close(self):
                case.lifecycle.append(("close", threading.get_ident()))

        class Native:
            def __init__(self, accept):
                self.accept, self.number = accept, 0

            def imu(self, *_):
                case.native_calls.append(("imu", threading.get_ident()))

            def frame(self, stamp, *_):
                self.number += 1
                case.native_calls.append(("frame", threading.get_ident()))
                case.packet = PointPacket(self.number, stamp, 100,
                    np.random.default_rng(self.number).normal(size=(100, 3)), np.eye(4))
                self.accept(case.packet)
                return np.eye(4), {"frame": self.number}

            def close(self):
                case.native_calls.append(("close", threading.get_ident()))

        binding = LocalizationBinding("2" * 32, "synthetic", "slam-" + "1" * 32,
                                      "3" * 64, "4" * 64, "5" * 64)
        self.session = LocalizationSession(binding, Registrar, clock=lambda: 100.)
        self.native = LocalizedNative(self.session, Native, asynchronous=True,
                                     on_frame=lambda *args: self.frames.append(args))
        self.lane = self.native.registration_lane
        self.number = 0

    def tearDown(self):
        self.release.set()
        self.native.close()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("asynchronous operation did not complete")
            time.sleep(.005)

    def send(self):
        self.number += 1
        stamp = 1000. + self.number * .02
        self.native.imu(stamp, (0., 0., 9.8), (0., 0., 0.))
        self.native.frame_received(stamp, Scan(self.number * 20_000_000,
            np.ones((100, 3)), np.zeros(100), 100., 2400))

    def command(self, action):
        state = self.session.state()
        return self.session.request(action, asdict(self.session.binding), state["generation"],
            **({"seed": np.eye(4), "reference_frame": state["referenceFrame"]}
               if action == "seed" else {}))

    def seed(self):
        self.send()
        self.wait_for(lambda: self.session.state()["referenceFrame"] == 1)
        self.command("seed")
        self.wait_for(lambda: self.session.state()["state"] == "acquiring")

    def test_slow_registration_coalesces_only_corrected_frames_and_owns_copies(self):
        self.seed()
        self.block = True
        self.send()
        self.assertTrue(self.entered.wait(2))
        for _ in range(8):
            self.send()
        self.assertEqual(20, len(self.native_calls))
        self.assertEqual(1, self.lane.summary()["pending"])
        self.assertEqual(7, self.lane.summary()["replaced"])
        self.packet.points[:] = np.nan
        self.packet.world_lidar_pose[:] = np.nan
        self.release.set()
        self.wait_for(lambda: self.session.state()["referenceFrame"] == 10)
        self.assertNotEqual("lost", self.session.state()["state"])
        self.assertTrue(np.isfinite(self.session.latest[0].points).all())
        self.assertTrue(np.isfinite(self.session.latest[0].world_lidar_pose).all())
        self.assertNotEqual(threading.get_ident(), self.session.owner)
        self.assertEqual({threading.get_ident()}, {owner for _, owner in self.native_calls})

    def test_pause_revokes_blocked_result_and_reseed_needs_new_confirmation(self):
        self.seed()
        self.block = True
        self.send()
        self.assertTrue(self.entered.wait(2))
        paused = self.command("pause")
        self.assertIsNone(paused["mapFromSensor"])
        self.release.set()
        self.wait_for(lambda: not self.session.state()["requestPending"])
        self.assertEqual("paused", self.session.state()["state"])
        self.send()
        self.wait_for(lambda: self.session.state()["referenceFrame"] == 3)
        self.command("seed")
        self.wait_for(lambda: self.session.state()["state"] == "acquiring")
        for frame in (4, 5, 6):
            self.send()
            self.wait_for(lambda: self.session.state()["lastFrame"] == frame)
        self.assertEqual("tracking", self.session.state()["state"])
        self.assertGreater(self.session.state()["generation"], paused["generation"])

    def test_stop_discards_pending_work_and_closes_each_owner_once(self):
        self.seed()
        self.block = True
        self.send()
        self.assertTrue(self.entered.wait(2))
        self.send()
        self.release.set()
        self.native.close()
        self.native.close()
        summary = self.lane.summary()
        self.assertEqual(summary["offered"], summary["processed"] + summary["replaced"] + summary["discarded"])
        self.assertEqual(0, summary["pending"])
        self.assertFalse(summary["threadAlive"])
        self.assertTrue(summary["closed"])
        self.assertEqual([("create", self.session.owner), ("close", self.session.owner)], self.lifecycle)
        self.assertEqual(1, sum(name == "close" for name, _ in self.native_calls))
        self.assertTrue(self.session.state()["cleanupComplete"])
        self.assertIsNone(self.session.state()["mapFromSensor"])

    def test_callback_failure_retires_session_and_reaches_sensor_owner(self):
        def failure(*_):
            raise RuntimeError("injected_callback_failure")
        self.lane.on_frame = failure
        self.send()
        self.wait_for(lambda: self.lane.summary()["failed"])
        with self.assertRaisesRegex(RuntimeError, "localization_lane_unavailable"):
            self.native.imu(1.)
        self.assertFalse(self.session.accepting)
        with self.assertRaisesRegex(RuntimeError, "localization_lane_failed"):
            self.native.close()
        self.assertFalse(self.lane.summary()["threadAlive"])


if __name__ == "__main__":
    unittest.main()
