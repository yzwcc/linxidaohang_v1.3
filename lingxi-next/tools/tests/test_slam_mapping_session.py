"""Synthetic lifecycle checks, not native mapping or robot acceptance."""
import hashlib
import importlib.util
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.mapping_session import MappingSession, SaveDecision


SESSION = "bbaaccdd00112233445566778899aabb"
HAS_NUMPY = importlib.util.find_spec("numpy") is not None


class MappingSaveLifecycle(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "new-map"
        self.calls, self.decision = [], SaveDecision()
        self.healthy, self.failure, self.mutate_on_reload = True, None, False
        self.reload_pid = os.getpid() + 1
        self.on_reload = lambda: None
        self.frame_result = None
        self.provenance = {
            "schemaVersion": 1, "profileSha256": "a" * 64,
            "timingHypothesis": "header_is_scan_start",
            "configurationSha256": {"lidar.json": "b" * 64},
        }
        self.current_provenance = self.provenance
        self.reload_provenance = self.provenance
        self.verify_save = None

    def make(self, can_save=None, with_provenance=False):
        test = self
        native_created = [False]

        class Native:
            def __init__(self):
                native_created[0] = True

            def imu(self, *args):
                test.calls.append("imu")
                if test.failure == "imu":
                    raise RuntimeError("synthetic_imu_failure")
                return args

            def frame(self, *args):
                test.calls.append("frame")
                return test.frame_result(args) if test.frame_result is not None else args

            def close(self):
                test.calls.append("native-close")
                if test.failure == "native-close":
                    raise RuntimeError("synthetic_close_failure")
                return 2

        class Mapping:
            def __init__(self):
                test.assertTrue(native_created[0], "initialize odometry before loading its mapping extension")

            def call(self, name, *args):
                test.calls.append(name)
                if test.failure == name:
                    raise RuntimeError("synthetic_mapping_failure")
                if name == "finish":
                    folder = Path(os.fsdecode(args[0]))
                    folder.mkdir()
                    (folder / "synthetic-map.bin").write_bytes(b"explicitly synthetic map")
                    Path(os.fsdecode(args[1])).write_bytes(b"\0" * 3 * 24)
                    for index, value in enumerate((2, 1, 3, 2, 50, 0)):
                        args[2][index] = value

        def inspect(folder):
            content = (folder / "synthetic-map.bin").read_bytes()
            return {"num_submaps": 1, "num_all_frames": 2,
                    "artifactInventorySha256": hashlib.sha256(content).hexdigest()}

        def reload(folder):
            self.calls.append("reload")
            if self.failure == "reload":
                raise RuntimeError("synthetic_reload_failure")
            if self.mutate_on_reload:
                (folder / "map/synthetic-map.bin").write_bytes(b"changed synthetic map")
            self.on_reload()
            return {
                "pid": self.reload_pid, "loadedSubmaps": 1,
                "comparison": {"pointCount": 3, "samePointOrderCompared": True,
                               "maximumPointDisplacementRawUnits": 0.,
                               "predeclaredToleranceRawUnits": 1e-4},
                **({"sensorProvenance": self.reload_provenance}
                   if with_provenance else {}),
            }

        options = ({"sensor_provenance": self.provenance,
                    "verify_sensor_provenance": lambda: self.current_provenance}
                   if with_provenance else {})
        return MappingSession(Native, Mapping, self.decision, self.output,
                              name="Synthetic test map", session=SESSION, input_mode="synthetic",
                              can_save=can_save or (lambda: self.healthy),
                              inspect_map=inspect, reload_map=reload,
                              verify_save=self.verify_save, **options)

    def test_save_verifier_runs_after_native_close_and_again_after_reload(self):
        def verify():
            self.calls.append("verify-save")
        self.verify_save = verify
        session = self.make()
        self.decision.request_save()
        session.close()
        self.assertEqual(2, self.calls.count("verify-save"))
        self.assertLess(self.calls.index("native-close"), self.calls.index("verify-save"))
        self.assertLess(self.calls.index("verify-save"), self.calls.index("finish"))
        self.assertGreater(len(self.calls) - 1, self.calls.index("reload"))

    def test_save_input_change_at_either_verification_prevents_publication(self):
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at):
                self.output = self.output.parent / ("changed-" + str(fail_at))
                self.decision, attempts = SaveDecision(), []
                def verify():
                    attempts.append(True)
                    if len(attempts) == fail_at:
                        raise ValueError("synthetic_saved_inputs_changed")
                self.verify_save = verify
                session = self.make()
                self.decision.request_save()
                with self.assertRaisesRegex(ValueError, "saved_inputs_changed"):
                    session.close()
                self.assertFalse((self.output / "map-ready.json").exists())
                self.assertIsNone(session.receipt)

    def test_provenance_is_published_only_after_matching_fresh_reload(self):
        session = self.make(with_provenance=True)
        self.decision.request_save()
        session.close()
        saved = json.loads((self.output / "map-ready.json").read_text())
        self.assertEqual(2, saved["schemaVersion"])
        self.assertEqual(self.provenance, saved["sensorProvenance"])

    def test_provenance_change_during_reload_withdraws_ready_receipt(self):
        session = self.make(with_provenance=True)
        self.on_reload = lambda: setattr(self, "current_provenance", {
            **self.provenance, "profileSha256": "c" * 64})
        self.decision.request_save()
        with self.assertRaisesRegex(ValueError, "unchanged_mapping_sensor_provenance_required"):
            session.close()
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_provenance_mismatch_at_initialization_creates_no_native_or_output(self):
        self.current_provenance = {**self.provenance, "profileSha256": "c" * 64}
        with self.assertRaisesRegex(ValueError, "unchanged_mapping_sensor_provenance_required"):
            self.make(with_provenance=True)
        self.assertFalse(self.output.exists())
        self.assertEqual([], self.calls)

    def test_provenance_change_before_save_skips_native_finish(self):
        session = self.make(with_provenance=True)
        self.current_provenance = {**self.provenance, "timingHypothesis": "header_is_scan_end"}
        self.decision.request_save()
        with self.assertRaisesRegex(ValueError, "unchanged_mapping_sensor_provenance_required"):
            session.close()
        self.assertNotIn("finish", self.calls)
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_fresh_reload_must_report_the_same_provenance(self):
        for index, reported in enumerate((None, {**self.provenance, "profileSha256": "c" * 64})):
            with self.subTest(reported=reported):
                self.output = Path(self.temporary.name) / str(index)
                self.decision = SaveDecision()
                self.reload_provenance = reported
                session = self.make(with_provenance=True)
                self.decision.request_save()
                with self.assertRaises(ValueError):
                    session.close()
                self.assertFalse((self.output / "map-ready.json").exists())

    def test_explicit_save_flushes_closes_reloads_then_publishes_once(self):
        session = self.make()
        self.assertEqual((1,), session.imu(1))
        self.assertEqual((2,), session.frame(2))
        self.assertTrue(self.decision.request_save())
        self.assertEqual(2, session.close())
        self.assertEqual(2, session.close())
        self.assertEqual(["open", "imu", "frame", "native-close", "finish", "shutdown", "reload"], self.calls)
        saved = json.loads((self.output / "map-ready.json").read_text())
        self.assertEqual(session.receipt, saved)
        self.assertEqual("synthetic", saved["inputMode"])
        self.assertFalse(saved["navigationReady"])
        self.assertFalse(saved["localizationReady"])
        self.assertFalse(self.decision.request_save())

    def test_plain_stop_never_saves_or_reloads(self):
        session = self.make()
        session.close()
        self.assertEqual(["open", "native-close", "shutdown"], self.calls)
        self.assertFalse((self.output / "map-ready.json").exists())
        self.assertFalse(self.decision.request_save())

    def test_full_mapper_receives_real_imu_before_lio_and_observes_returned_pose(self):
        session = self.make()
        session.mapping.feed_imu = lambda *args: self.calls.append("mapping-imu")
        session.mapping.observe_frame = lambda result: self.calls.append(("observed-frame", result))
        session.imu(1)
        session.frame(2)
        session.close()
        self.assertEqual(["open", "mapping-imu", "imu", "frame", ("observed-frame", (2,)),
                          "native-close", "shutdown"], self.calls)

    def test_mapping_input_failure_revokes_save_without_forwarding_imu_to_lio(self):
        session = self.make()
        def failed(*args):
            raise RuntimeError("synthetic_mapping_backlog")
        session.mapping.feed_imu = failed
        self.decision.request_save()
        with self.assertRaisesRegex(RuntimeError, "mapping_backlog"):
            session.imu(1)
        session.close()
        self.assertEqual(["open", "native-close", "shutdown"], self.calls)
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_full_mapping_cannot_publish_a_receipt_without_sensor_provenance(self):
        session = self.make()
        session.mapping.engine = "glim-full-async-v1"
        self.decision.request_save()
        with self.assertRaisesRegex(ValueError, "requires_sensor_provenance"):
            session.close()
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_discard_revokes_pending_save(self):
        session = self.make()
        self.decision.request_save()
        self.decision.discard()
        session.close()
        self.assertNotIn("finish", self.calls)
        self.assertFalse(self.decision.request_save())

    def test_failed_worker_health_does_not_save(self):
        session = self.make()
        self.decision.request_save()
        self.healthy = False
        session.close()
        self.assertNotIn("finish", self.calls)

    def test_native_input_failure_revokes_save_but_still_cleans_up(self):
        session = self.make()
        self.decision.request_save()
        self.failure = "imu"
        with self.assertRaisesRegex(RuntimeError, "synthetic_imu"):
            session.imu(1)
        session.close()
        self.assertEqual(["open", "imu", "native-close", "shutdown"], self.calls)

    def test_every_save_failure_leaves_no_ready_map_and_shutdown_runs_once(self):
        for failure in ("native-close", "finish", "shutdown", "reload"):
            with self.subTest(failure=failure):
                self.output = Path(self.temporary.name) / failure
                self.calls, self.failure, self.decision = [], None, SaveDecision()
                session = self.make()
                self.failure = failure
                self.decision.request_save()
                with self.assertRaises(RuntimeError):
                    session.close()
                self.assertEqual(1, self.calls.count("shutdown"))
                self.assertFalse((self.output / "map-ready.json").exists())
                with self.assertRaisesRegex(RuntimeError, "previously_failed"):
                    session.close()
                self.assertFalse(self.decision.request_save())

    def test_open_failure_closes_both_owned_components(self):
        self.failure = "open"
        with self.assertRaises(RuntimeError):
            self.make()
        self.assertEqual(["open", "shutdown", "native-close"], self.calls)

    def test_reload_in_same_process_is_not_fresh_process_verification(self):
        session = self.make()
        self.reload_pid = os.getpid()
        self.decision.request_save()
        with self.assertRaisesRegex(ValueError, "independent_unchanged"):
            session.close()
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_close_failure_logs_code_location_without_exception_text(self):
        session = self.make()
        self.failure = "native-close"
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaises(RuntimeError):
            session.close()
        self.assertRegex(output.getvalue(), r"mapping_close_failure:RuntimeError:test_slam_mapping_session.py:\d+")
        self.assertNotIn("synthetic_close_failure", output.getvalue())
        self.assertNotIn(str(self.output), output.getvalue())

    def test_map_changed_during_reload_is_not_published(self):
        session = self.make()
        self.mutate_on_reload = True
        self.decision.request_save()
        with self.assertRaisesRegex(ValueError, "independent_unchanged"):
            session.close()
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_save_revoked_during_reload_is_not_published(self):
        session = self.make()
        self.on_reload = self.decision.discard
        self.decision.request_save()
        session.close()
        self.assertIn("reload", self.calls)
        self.assertIsNone(session.receipt)
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_worker_health_lost_during_reload_is_not_published(self):
        session = self.make()
        self.on_reload = lambda: setattr(self, "healthy", False)
        self.decision.request_save()
        session.close()
        self.assertIsNone(session.receipt)
        self.assertFalse((self.output / "map-ready.json").exists())

    def test_native_methods_stay_on_creating_thread(self):
        session = self.make()
        failures = []
        def wrong_thread():
            for call in (lambda: session.frame(1), session.close):
                try:
                    call()
                except RuntimeError:
                    failures.append(True)
        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join()
        self.assertEqual([True, True], failures)
        session.close()
        self.assertEqual(["open", "native-close", "shutdown"], self.calls)

    def test_existing_map_output_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "original"
        sentinel.write_text("preserved")
        with self.assertRaises(FileExistsError):
            self.make()
        self.assertEqual("preserved", sentinel.read_text())
        self.assertEqual([], self.calls)

    @unittest.skipUnless(HAS_NUMPY, "requires existing isolated NumPy environment")
    def test_existing_worker_saves_on_its_native_owner_thread(self):
        self.run_worker_case(fail_input=False)

    @unittest.skipUnless(HAS_NUMPY, "requires existing isolated NumPy environment")
    def test_existing_worker_input_failure_prevents_pending_save(self):
        self.run_worker_case(fail_input=True)

    def run_worker_case(self, *, fail_input):
        import numpy as np
        from lingxi_slam.messages import SensorProfile
        from lingxi_slam.worker import SensorWorker, WorkerLimits
        from test_slam_worker import cloud, imu

        def healthy():
            status = worker.snapshot()
            return status["state"] == "stopping" and status["failure"] is None

        self.frame_result = lambda args: (np.eye(4), np.array([1., 2., len(args[1]), 0.]))
        worker = SensorWorker(lambda: self.make(can_save=healthy),
                              SensorProfile("synthetic_cloud", "synthetic_imu",
                                            "frame_relative_nanoseconds_hypothesis"),
                              "header_is_scan_start", limits=WorkerLimits(shutdown_seconds=5.))
        self.addCleanup(worker.stop)
        worker.start()
        deadline = time.monotonic() + 2
        while worker.snapshot()["state"] == "starting" and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual("running", worker.snapshot()["state"])
        self.decision.request_save()
        if fail_input:
            self.assertFalse(worker.offer_imu(imu(-1)))
        else:
            for milliseconds in range(0, 101, 5):
                self.assertTrue(worker.offer_imu(imu(milliseconds)))
            self.assertTrue(worker.offer_cloud(cloud(40)))
            self.assertTrue(worker.offer_cloud(cloud(70)))
            deadline = time.monotonic() + 2
            while worker.snapshot()["counts"]["completedMessages"] < 23 and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(23, worker.snapshot()["counts"]["completedMessages"])
        status = worker.stop()
        self.assertFalse(status["threadAlive"])
        self.assertEqual("failed" if fail_input else "stopped", status["state"])
        self.assertEqual(not fail_input, (self.output / "map-ready.json").exists())


if __name__ == "__main__":
    unittest.main()
