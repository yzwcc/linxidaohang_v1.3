"""Controller contracts with a synthetic mapper; native library execution is separate."""
import importlib.util
from contextlib import redirect_stderr
import io
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
AVAILABLE = importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    import numpy as np
    from lingxi_slam import mapping_worker as module
    from lingxi_slam.messages import SensorProfile
    from test_slam_worker import cloud, imu


@unittest.skipUnless(AVAILABLE, "requires existing isolated NumPy environment")
class MappingController(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output, self.calls = Path(temporary.name) / "map", []
        self.fail_create = False
        parent = self

        class SyntheticSession:
            def __init__(self, *args, **kwargs):
                parent.calls.append("create")
                if parent.fail_create:
                    raise RuntimeError("synthetic_factory_failed")
                self.name = kwargs["name"]
                self.decision, self.can_save, self.receipt = args[2], kwargs["can_save"], None

            def imu(self, *args):
                return None

            def frame(self, stamp, points, offsets):
                return np.eye(4), np.array([1., 2., len(points), 0.])

            def close(self):
                parent.calls.append("close")
                if self.decision.requested() and self.can_save():
                    self.receipt = {"counts": {"savedFrames": 2}}
                self.decision.finish()

        self.patch = patch.object(module, "MappingSession", SyntheticSession)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.controller = module.MappingWorker(
            lambda: None, lambda: None,
            SensorProfile("synthetic_cloud", "synthetic_imu", "frame_relative_nanoseconds_hypothesis"),
            "header_is_scan_start", self.output, name="Synthetic map",
            session="bbaaccdd00112233445566778899aabb", input_mode="synthetic",
            inspect_map=lambda path: None, reload_map=lambda path: None, save_timeout_seconds=5.)
        self.addCleanup(self.controller.stop)

    def start(self, *, feed=True):
        self.controller.start()
        deadline = time.monotonic() + 2
        while self.controller.worker.snapshot()["state"] == "starting" and time.monotonic() < deadline:
            time.sleep(.005)
        if not self.fail_create:
            self.assertEqual("running", self.controller.worker.snapshot()["state"])
        if feed:
            for stamp in range(0, 101, 5):
                self.assertTrue(self.controller.offer_imu(imu(stamp)))
            self.assertTrue(self.controller.offer_cloud(cloud(40)))
            self.assertTrue(self.controller.offer_cloud(cloud(70)))
            deadline = time.monotonic() + 2
            while self.controller.worker.snapshot()["counts"]["completedMessages"] < 23 and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(23, self.controller.worker.snapshot()["counts"]["completedMessages"])

    def test_explicit_save_finishes_without_readiness_or_restart(self):
        self.start()
        result = self.controller.stop(save=True)
        self.assertEqual("saved", result["phase"])
        self.assertTrue(result["saved"])
        self.assertEqual("synthetic", result["inputMode"])
        self.assertFalse(result["navigationReady"])
        self.assertFalse(result["localizationReady"])
        self.assertEqual(["create", "close"], self.calls)
        with self.assertRaisesRegex(RuntimeError, "cannot_restart"):
            self.controller.start()
        self.assertEqual(result, self.controller.stop(save=True))

    def test_plain_stop_never_promotes_to_saved(self):
        self.start()
        result = self.controller.stop()
        self.assertEqual("stopped", result["phase"])
        self.assertFalse(self.controller.stop(save=True)["saved"])
        self.assertEqual(["create", "close"], self.calls)

    def test_name_is_chosen_at_save_time(self):
        self.start()
        self.assertTrue(self.controller.stop(save=True, name="  Final synthetic map  ")["saved"])
        self.assertEqual("Final synthetic map", self.controller.native_session.name)
        self.controller.stop(save=True, name="Must not rename a completed map")
        self.assertEqual("Final synthetic map", self.controller.native_session.name)

    def test_rename_during_factory_initialization_is_not_lost(self):
        original = module.MappingSession
        entered, release = threading.Event(), threading.Event()

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("synthetic_factory_not_released")
            return original(*args, **kwargs)

        with patch.object(module, "MappingSession", delayed):
            self.controller.start()
            self.assertTrue(entered.wait(2))
            result = []
            stopper = threading.Thread(target=lambda: result.append(
                self.controller.stop(save=True, name="Concurrent synthetic name")))
            stopper.start()
            deadline = time.monotonic() + 1
            while self.controller.name != "Concurrent synthetic name" and time.monotonic() < deadline:
                time.sleep(.002)
            release.set()
            stopper.join(3)
            self.assertFalse(stopper.is_alive())
            self.assertEqual("Concurrent synthetic name", self.controller.native_session.name)
            self.assertEqual(1, len(result))

    def test_invalid_save_names_do_not_stop_the_worker(self):
        for name in ("", " " * 3, "x" * 65, 1):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.controller.stop(save=True, name=name)
        with self.assertRaises(ValueError):
            self.controller.stop(name="Discard cannot rename")
        self.assertEqual("new", self.controller.snapshot()["phase"])

    def test_cancelled_save_cannot_be_requested_again(self):
        self.start()
        self.controller.cancel_save()
        self.assertEqual("stopped", self.controller.stop(save=True)["phase"])

    def test_failed_sensor_input_cannot_publish_map(self):
        self.start(feed=False)
        self.assertFalse(self.controller.offer_imu(imu(-1)))
        result = self.controller.stop(save=True)
        self.assertEqual("failed", result["phase"])
        self.assertFalse(result["saved"])
        self.assertFalse(self.controller.snapshot()["worker"]["threadAlive"])

    def test_failed_factory_is_joined_and_never_saved(self):
        self.fail_create = True
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            self.start(feed=False)
        self.assertEqual("failed", self.controller.stop(save=True)["phase"])
        self.assertFalse(self.controller.snapshot()["worker"]["threadAlive"])
        self.assertEqual(["create"], self.calls)
        self.assertRegex(diagnostics.getvalue(), r"mapping_startup_failure:RuntimeError:test_slam_mapping_worker.py:[0-9]+")
        self.assertNotIn("synthetic_factory_failed", diagnostics.getvalue())

    def test_stop_before_start_never_creates_a_native_engine(self):
        self.assertEqual("stopped", self.controller.stop(save=True)["phase"])
        self.assertEqual([], self.calls)

    def test_concurrent_stop_closes_native_once(self):
        self.start()
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.controller.stop(save=True)))
                   for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(2, len(results))
        self.assertTrue(all(result["saved"] for result in results))
        self.assertEqual(["create", "close"], self.calls)


if __name__ == "__main__":
    unittest.main()
