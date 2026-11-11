"""GIL-holding native work must not starve the parent's sensor/control thread."""
import ctypes
import importlib.util
import os
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    import numpy as np
    from lingxi_slam.registration_process import ProcessMapRegistration
    from lingxi_slam.seeded_localization import RegistrationDiagnostics, RegistrationEstimate


class HoldingRegistrar:
    def __init__(self, points, config):
        self.delay = float(points[0, 0])

    def register(self, scan, seed):
        if self.delay < 0:
            raise RuntimeError("private_test_input_must_not_escape")
        # PyDLL deliberately retains the GIL during the foreign call.
        if sys.platform == "win32":
            ctypes.PyDLL("kernel32").Sleep(int(self.delay * 1000))
        else:
            ctypes.PyDLL(None).usleep(int(self.delay * 1_000_000))
        return RegistrationEstimate(True, (), seed, {"nativeInliers": len(scan), "processId": os.getpid()})


@unittest.skipUnless(AVAILABLE, "requires isolated numerical dependencies")
class ProcessRegistration(unittest.TestCase):
    def create(self, delay=.2, timeout=.65):
        points = np.ones((64, 3)); points[0, 0] = delay
        diagnostics = RegistrationDiagnostics()
        registrar = ProcessMapRegistration(points, diagnostics=diagnostics,
            timeout=timeout, factory=HoldingRegistrar)
        self.addCleanup(registrar.close)
        return registrar, diagnostics

    def test_native_gil_hold_is_isolated_and_result_and_diagnostics_survive(self):
        registrar, diagnostics = self.create()
        beats, stop = [], threading.Event()
        def heartbeat():
            while not stop.wait(.01):
                beats.append(time.monotonic())
        thread = threading.Thread(target=heartbeat); thread.start()
        try:
            result = registrar.register(np.ones((64, 3)), np.eye(4))
        finally:
            stop.set(); thread.join()
        self.assertGreaterEqual(len(beats), 5)
        self.assertNotEqual(os.getpid(), result.metrics["processId"])
        self.assertTrue(result.accepted)
        self.assertFalse(result.map_from_sensor.flags.writeable)
        self.assertEqual(1, diagnostics.summary()["accepted"])
        registrar.close()
        self.assertFalse(registrar.process.is_alive())

    def test_timeout_retires_child_and_cannot_replay_late_result(self):
        registrar, diagnostics = self.create(delay=2., timeout=.05)
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            registrar.register(np.ones((64, 3)), np.eye(4))
        self.assertFalse(registrar.process.is_alive())
        self.assertEqual(0, diagnostics.summary()["attempts"])
        with self.assertRaisesRegex(RuntimeError, "active_single"):
            registrar.register(np.ones((64, 3)), np.eye(4))

    def test_failure_is_fixed_code_and_cleanup_is_idempotent(self):
        registrar, _ = self.create(delay=-1.)
        with self.assertRaisesRegex(RuntimeError, "^registration_process_failed$"):
            registrar.register(np.ones((64, 3)), np.eye(4))
        self.assertFalse(registrar.process.is_alive())
        registrar.close()


if __name__ == "__main__":
    unittest.main()
