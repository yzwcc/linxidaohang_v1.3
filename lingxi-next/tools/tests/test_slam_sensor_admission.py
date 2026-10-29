"""Synthetic admission races only; no ROS, SLAM libraries or robot access."""
import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
AVAILABLE = importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    from lingxi_slam.messages import SensorProfile
    from lingxi_slam.sensor_admission import SensorAdmission
    from lingxi_slam.worker import SensorWorker
    from test_slam_worker import imu


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy")
class AdmissionContracts(unittest.TestCase):
    def setUp(self):
        self.worker = SensorWorker(
            lambda: NS(imu=lambda *args: None, close=lambda: None),
            SensorProfile("synthetic_cloud", "synthetic_imu", "frame_relative_nanoseconds_hypothesis"),
            "header_is_scan_start")
        self.addCleanup(self.worker.stop)
        self.admission = SensorAdmission(self.worker)

    def start(self):
        self.admission.start()
        deadline = time.monotonic() + 1
        while self.worker.snapshot()["state"] == "starting" and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertEqual("running", self.worker.snapshot()["state"])

    def test_close_rejects_late_callbacks_without_poisoning_worker(self):
        self.start()
        self.assertTrue(self.admission.offer_imu(imu()))
        self.admission.close()
        self.assertFalse(self.admission.offer_imu(imu(5)))
        terminal = self.admission.drain()
        self.assertEqual(1, terminal["counts"]["completedMessages"])
        self.assertEqual(0, terminal["counts"]["rejectedMessages"])
        self.assertEqual(1, self.admission.rejected_after_close)
        self.assertEqual("running", terminal["state"])
        with self.assertRaisesRegex(RuntimeError, "cannot_restart"):
            self.admission.start()

    def test_close_waits_for_callback_already_snapshotting(self):
        self.start()
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        original = self.worker.offer_imu

        def blocked(message):
            entered.set()
            release.wait(1)
            return original(message)

        with patch.object(self.worker, "offer_imu", blocked):
            offer = threading.Thread(target=lambda: self.admission.offer_imu(imu()))
            closer = threading.Thread(target=lambda: (self.admission.close(), closed.set()))
            offer.start()
            self.assertTrue(entered.wait(1))
            closer.start()
            try:
                self.assertFalse(closed.wait(.02))
            finally:
                release.set()
                offer.join(1)
                closer.join(1)
        self.assertFalse(offer.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(1, self.admission.drain()["counts"]["completedMessages"])

    def test_drain_requires_closed_started_input_and_preserves_watchdogs(self):
        with self.assertRaisesRegex(RuntimeError, "closed_started"):
            self.admission.drain()
        self.start()
        with self.assertRaisesRegex(RuntimeError, "closed_started"):
            self.admission.drain()
        self.admission.close()
        limits = self.worker.snapshot()["limits"]
        self.admission.drain()
        self.assertEqual(limits, self.worker.snapshot()["limits"])
        for seconds in (True, 0, float("nan"), .501):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.admission.drain(seconds=seconds)

    def test_cancelled_and_failed_input_cannot_drain_as_success(self):
        self.start()
        self.admission.close()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.admission.drain(cancelled=lambda: True)
        self.worker.stop()
        with self.assertRaisesRegex(RuntimeError, "failed_before_drain"):
            self.admission.drain()

    def test_inflight_drain_has_a_bounded_deadline(self):
        self.start()
        self.admission.close()
        state = {**self.worker.snapshot(), "inFlightMessages": 1}
        with patch.object(self.worker, "snapshot", return_value=state):
            with self.assertRaisesRegex(RuntimeError, "drain_deadline"):
                self.admission.drain(seconds=.01)

    def test_closed_before_start_never_constructs_native(self):
        self.admission.close()
        self.assertFalse(self.admission.offer_imu(imu()))
        with self.assertRaisesRegex(RuntimeError, "cannot_restart"):
            self.admission.start()
        self.assertEqual("new", self.worker.snapshot()["state"])


if __name__ == "__main__":
    unittest.main()
