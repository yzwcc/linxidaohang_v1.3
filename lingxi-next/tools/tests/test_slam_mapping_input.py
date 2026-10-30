"""One synthetic engine supplies points and saved frames; not native acceptance."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "robot-runtime/python")]
from controlled_mapping import finish_input
AVAILABLE = importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    import numpy as np
    from lingxi_slam.mapping_input import MappingInput
    from lingxi_slam.mapping_worker import MappingWorker
    from lingxi_slam.messages import SensorProfile
    from lingxi_slam.point_output import PointOutputNative, PointPacket
    from test_slam_worker import cloud, imu


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy")
class MappingInputContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "map"
        self.calls, self.packets, self.owners = [], [], set()
        self.frames, self.point_failure, self.reloads = 0, False, 0
        self.frame_entered, self.frame_release = threading.Event(), threading.Event()
        self.frame_release.set()
        self.on_reload = lambda: None
        self.verify_save = lambda: None
        test = self

        def record(name):
            test.calls.append(name)
            test.owners.add(threading.get_ident())

        class Native:
            def __init__(self):
                record("native_create")

            def imu(self, *args):
                pass

            def frame(self, stamp, points, offsets):
                test.frame_entered.set()
                if not test.frame_release.wait(1):
                    raise RuntimeError("synthetic_frame_blocked")
                test.frames += 1
                return np.eye(4), np.array([0., 0., len(points), 0.])

            def close(self):
                record("native_close")
                return 0

        class Points:
            def __init__(self):
                self.closed, self.owner = False, threading.get_ident()
                record("point_open")

            def active(self):
                if self.closed or self.owner != threading.get_ident():
                    raise RuntimeError("synthetic_point_owner")

            def take(self, stamp, pose):
                self.active()
                if test.frames == 1:
                    return None
                return PointPacket(test.frames - 1, stamp, 3,
                                   np.array([[1., 2., 3.]]), np.eye(4))

            def close(self):
                self.active()
                self.closed = True
                record("point_close")

        def accept(packet):
            record("point_accept")
            if self.point_failure:
                raise RuntimeError("synthetic_point_consumer_failed")
            self.packets.append(packet.native_frame_id)

        class Mapper:
            def __init__(self):
                record("mapper_create")

            def call(self, name, *args):
                record("mapper_" + name)
                if name == "finish":
                    folder = Path(os.fsdecode(args[0]))
                    folder.mkdir()
                    (folder / "synthetic-map.bin").write_bytes(b"synthetic map only")
                    Path(os.fsdecode(args[1])).write_bytes(b"\0" * 3 * 24)
                    for index, value in enumerate((test.frames, 1, 3, test.frames, 50, 0)):
                        args[2][index] = value

        def inspect(folder):
            return {"num_submaps": 1, "num_all_frames": self.frames,
                    "artifactInventorySha256": hashlib.sha256(
                        (folder / "synthetic-map.bin").read_bytes()).hexdigest()}

        def reload(folder):
            record("reload")
            self.reloads += 1
            self.on_reload()
            return {"pid": os.getpid() + 1, "loadedSubmaps": 1,
                    "comparison": {"pointCount": 3, "samePointOrderCompared": True,
                                   "maximumPointDisplacementRawUnits": 0.,
                                   "predeclaredToleranceRawUnits": 1e-4}}

        self.controller = MappingWorker(
            lambda: PointOutputNative(Native, Points(), accept), Mapper,
            SensorProfile("synthetic_cloud", "synthetic_imu", "frame_relative_nanoseconds_hypothesis"),
            "header_is_scan_start", self.output, name="Synthetic map",
            session="bbaaccdd00112233445566778899aabb", input_mode="synthetic",
            inspect_map=inspect, reload_map=reload, save_timeout_seconds=5.,
            verify_save=lambda: self.verify_save())
        self.input = MappingInput(self.controller)
        self.addCleanup(self.controller.stop)

    def start(self):
        self.input.admission.start()
        deadline = time.monotonic() + 1
        while self.controller.worker.snapshot()["state"] == "starting" and time.monotonic() < deadline:
            time.sleep(.002)
        for stamp in range(0, 131, 5):
            self.assertTrue(self.input.admission.offer_imu(imu(stamp)))
        for stamp in (40, 70, 100):
            self.assertTrue(self.input.admission.offer_cloud(cloud(stamp)))

    def test_same_engine_points_and_map_close_in_order_without_readiness(self):
        self.start()
        terminal = self.input.finish("requested_stop", save=True, name="Final map")
        self.assertEqual("saved", terminal["phase"])
        self.assertEqual([1, 2], self.packets)
        self.assertEqual(3, self.frames)
        self.assertEqual(1, len(self.owners))
        self.assertNotIn(threading.get_ident(), self.owners)
        self.assertLess(self.calls.index("native_create"), self.calls.index("mapper_create"))
        self.assertLess(self.calls.index("native_close"), self.calls.index("mapper_finish"))
        self.assertLess(self.calls.index("point_close"), self.calls.index("mapper_finish"))
        self.assertLess(self.calls.index("mapper_shutdown"), self.calls.index("reload"))
        receipt = json.loads((self.output / "map-ready.json").read_text())
        self.assertEqual("Final map", receipt["name"])
        self.assertEqual("synthetic", receipt["inputMode"])
        self.assertFalse(receipt["navigationReady"])
        self.assertFalse(receipt["localizationReady"])
        worker = self.controller.worker.snapshot()
        self.assertEqual(30, worker["counts"]["completedMessages"])
        self.assertEqual(0, worker["counts"]["discardedMessages"])
        self.assertFalse(worker["threadAlive"])

    def test_inflight_callback_is_completed_before_save(self):
        self.frame_release.clear()
        self.start()
        self.assertTrue(self.frame_entered.wait(1))
        result = []
        thread = threading.Thread(target=lambda: result.append(
            self.input.finish("requested_stop", save=True)))
        thread.start()
        try:
            time.sleep(.02)
            self.assertNotIn("native_close", self.calls)
        finally:
            self.frame_release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual("saved", result[0]["phase"])
        self.assertEqual(3, self.frames)

    def test_unowned_exit_and_cancellation_never_save(self):
        self.start()
        terminal = self.input.finish("duration_reached", save=True)
        self.assertEqual("stopped", terminal["phase"])
        self.assertFalse((self.output / "map-ready.json").exists())
        self.assertNotIn("mapper_finish", self.calls)
        self.assertEqual(1, self.calls.count("native_close"))
        self.assertEqual(1, self.calls.count("point_close"))

    def test_explicit_discard_still_drains_without_saving(self):
        self.start()
        terminal = self.input.finish("requested_stop")
        self.assertEqual("stopped", terminal["phase"])
        self.assertEqual(3, self.frames)
        self.assertEqual([1, 2], self.packets)
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_point_consumer_failure_revokes_map_save_and_cleans_both(self):
        self.point_failure = True
        self.start()
        with self.assertRaisesRegex(RuntimeError, "failed_before_drain"):
            self.input.finish("requested_stop", save=True)
        self.assertFalse((self.output / "map-ready.json").exists())
        self.assertFalse(self.controller.worker.snapshot()["threadAlive"])
        self.assertEqual(1, self.calls.count("native_close"))
        self.assertEqual(1, self.calls.count("point_close"))
        self.assertNotIn("mapper_finish", self.calls)

    def test_managed_live_input_save_requires_owned_stop_and_unchanged_inputs(self):
        self.start()
        terminal = finish_input(
            self.input, "requested_stop", SimpleNamespace(received=True),
            {"name": "Managed live map"}, threading.Event(), lambda: {"frozen": True}, {"frozen": True})
        self.assertEqual("saved", terminal["phase"])
        self.assertEqual(3, self.frames)
        self.assertEqual(1, self.reloads)
        self.assertEqual("Managed live map", json.loads(
            (self.output / "map-ready.json").read_text())["name"])

    def test_managed_input_loss_never_saves_even_with_a_pending_save_request(self):
        self.start()
        with self.assertRaisesRegex(RuntimeError, "explicit_owned_mapping_stop_required"):
            finish_input(self.input, "input_failed", SimpleNamespace(received=True),
                         {"name": "Must not save"}, threading.Event(), lambda: {}, {})
        self.assertNotIn("mapper_finish", self.calls)
        self.assertFalse((self.output / "map-ready.json").exists())
        self.assertFalse(self.controller.worker.snapshot()["threadAlive"])

    def test_managed_duration_expiry_never_saves(self):
        self.start()
        with self.assertRaisesRegex(RuntimeError, "explicit_owned_mapping_stop_required"):
            finish_input(self.input, "duration_reached", SimpleNamespace(received=False),
                         {"name": "Must not save"}, threading.Event(), lambda: {}, {})
        self.assertNotIn("mapper_finish", self.calls)
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_managed_changed_platform_rejects_save(self):
        def changed():
            raise RuntimeError("synthetic_platform_changed_before_save")
        self.verify_save = changed
        self.start()
        with self.assertRaisesRegex(RuntimeError, "mapping_terminal_not_confirmed"):
            finish_input(self.input, "requested_stop", SimpleNamespace(received=True),
                         {"name": "Must not save"}, threading.Event(), lambda: {"changed": True}, {})
        self.controller.stop()
        self.assertNotIn("mapper_finish", self.calls)
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_slow_full_verification_runs_only_after_the_input_watchdog_stops(self):
        verifications = []
        def verify():
            state = self.controller.worker.snapshot()
            self.assertEqual("stopping", state["state"])
            self.assertEqual("finished", state["stream"]["state"])
            self.assertIn("native_close", self.calls)
            time.sleep(.8)
            verifications.append(True)
        self.verify_save = verify
        self.start()
        def final_verify():
            self.assertFalse(self.controller.worker.snapshot()["threadAlive"])
            return {}
        terminal = finish_input(self.input, "requested_stop", SimpleNamespace(received=True),
                                {"name": "Verified after drain"}, threading.Event(), final_verify, {})
        self.assertEqual("saved", terminal["phase"])
        self.assertEqual(2, len(verifications))

    def test_managed_parent_loss_revokes_a_pending_save(self):
        self.start()
        failed = threading.Event()
        failed.set()
        with self.assertRaisesRegex(RuntimeError, "explicit_owned_mapping_stop_required"):
            finish_input(self.input, "requested_stop", SimpleNamespace(received=True),
                         {"name": "Must not save"}, failed, lambda: {}, {})
        self.assertNotIn("mapper_finish", self.calls)
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_cancel_during_independent_reload_cannot_publish(self):
        self.on_reload = self.controller.cancel_save
        self.start()
        terminal = self.input.finish("requested_stop", save=True)
        self.assertEqual(1, self.reloads)
        self.assertEqual("stopped", terminal["phase"])
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_cancel_before_drain_never_requests_save(self):
        self.start()
        terminal = self.input.finish("requested_stop", save=True, cancelled=lambda: True)
        self.assertEqual("stopped", terminal["phase"])
        self.assertNotIn("mapper_finish", self.calls)

    def test_preexisting_stop_does_not_initialize_or_save(self):
        terminal = self.input.finish("requested_stop", save=True)
        self.assertEqual("stopped", terminal["phase"])
        self.assertEqual([], self.calls)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
