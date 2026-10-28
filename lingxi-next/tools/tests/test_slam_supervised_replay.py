"""Composition contracts with clearly synthetic peers; native trial is separate."""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import run_glim_supervised_replay as replay
from lingxi_slam.process_protocol import decode


EXPECTED = {"records": 5, "counts": {"imu": 3, "cloud": 2}, "bytes": 123}


def child_report():
    return {
        "stopReceived": True, "journalExhausted": True,
        "nativeOwnerIsSingleNonMainThread": True,
        "nativeCalls": {"create": 1, "close": 1, "imu": 3, "frame": 2},
        "worker": {
            "state": "stopped", "failure": None, "threadAlive": False,
            "queuedMessages": 0, "queuedPayloadBytes": 0, "inFlightMessages": 0,
            "counts": {"completedMessages": 5, "acceptedImu": 3, "acceptedClouds": 2,
                       "rejectedMessages": 0, "discardedMessages": 0},
            "stream": {
                "state": "finished", "failure": None,
                "counts": {"insertedImu": 3, "processedScans": 2, "estimatedPoses": 2,
                           "receivedImu": 3, "receivedClouds": 2,
                           "skippedBeforeImu": 0, "skippedAfterImuAtEndOfInput": 0},
            },
        },
    }


def stopped():
    return {
        "state": "stopped", "exitCode": 0, "processAlive": False, "monitorAlive": False,
        "forcedTermination": False, "cleanupComplete": True, "completedMessages": 5,
    }


class CompositionContracts(unittest.TestCase):
    def test_diagnostic_rate_is_explicit_bounded_and_not_a_boolean(self):
        for value in (None, True, 1, 0., .1, 2., float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replay.replay_rate(value)
        self.assertEqual(.5, replay.replay_rate(.5))
        self.assertEqual(1., replay.replay_rate(1.))

    def test_environment_is_explicit_bounded_and_does_not_inherit_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SYNTHETIC_SECRET": "never-inherit", "ROS_DISTRO": "humble"}):
                env = replay.child_environment(directory)
            self.assertNotIn("SYNTHETIC_SECRET", env)
            self.assertNotIn("ROS_DISTRO", env)
            self.assertEqual("2", env["OMP_NUM_THREADS"])
            self.assertEqual("1", env["OPENBLAS_NUM_THREADS"])
            self.assertNotIn("LD_PRELOAD", env)
            self.assertEqual(2, len(env["LD_LIBRARY_PATH"].split(os.pathsep)))

    def test_stop_pipe_accepts_fragmentation_but_not_other_commands_or_eof(self):
        stop = replay.StopPipe()
        self.assertFalse(stop.feed(b"st"))
        self.assertTrue(stop.feed(b"op\n"))
        for payload in (b"", b"start\n", b"stop\nx", b"stop\r\n", b"motion"):
            with self.subTest(payload=payload), self.assertRaises((RuntimeError, ValueError)):
                replay.StopPipe().feed(payload)

    def test_native_wrapper_closes_once_and_preserves_call_results(self):
        native, record = Mock(), Mock()
        native.frame.return_value = ("synthetic-pose", "synthetic-statistics")
        owner = replay.OwnedNative(lambda: native, record, lambda: None)
        owner.imu(1, 2, 3)
        self.assertEqual(("synthetic-pose", "synthetic-statistics"), owner.frame(1, 2, 3))
        owner.close()
        owner.close()
        self.assertEqual(["create", "imu", "frame", "close"], [call.args[0] for call in record.call_args_list])
        native.close.assert_called_once()

    def test_mapped_library_verification_failure_still_closes_created_native(self):
        native, record = Mock(), Mock()
        with self.assertRaisesRegex(ValueError, "synthetic"):
            replay.OwnedNative(lambda: native, record, Mock(side_effect=ValueError("synthetic")))
        native.close.assert_called_once()
        self.assertEqual(["create", "close"], [call.args[0] for call in record.call_args_list])

    def test_complete_child_checks_all_callback_and_native_shutdown_accounting(self):
        replay.complete_child(child_report(), EXPECTED)
        modifications = (
            (("stopReceived",), False), (("journalExhausted",), False),
            (("nativeOwnerIsSingleNonMainThread",), False),
            (("nativeCalls", "create"), 2), (("nativeCalls", "close"), 0),
            (("nativeCalls", "frame"), 1), (("nativeCalls", "imu"), 2),
            (("worker", "state"), "running"), (("worker", "threadAlive"), True),
            (("worker", "failure"), "synthetic_failure"), (("worker", "inFlightMessages"), 1),
            (("worker", "queuedMessages"), 1), (("worker", "queuedPayloadBytes"), 1),
            (("worker", "counts", "completedMessages"), 4),
            (("worker", "counts", "rejectedMessages"), 1), (("worker", "counts", "discardedMessages"), 1),
            (("worker", "counts", "acceptedClouds"), 1), (("worker", "counts", "acceptedImu"), 2),
            (("worker", "stream", "counts", "receivedClouds"), 1),
            (("worker", "stream", "counts", "receivedImu"), 2),
            (("worker", "stream", "counts", "skippedBeforeImu"), 1),
            (("worker", "stream", "counts", "estimatedPoses"), 0),
            (("worker", "stream", "state"), "running"),
        )
        for path, value in modifications:
            report = child_report()
            cursor = report
            for name in path[:-1]:
                cursor = cursor[name]
            cursor[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                replay.complete_child(report, EXPECTED)

    def test_coordinator_stops_immediately_after_completed_count_not_producer_eof(self):
        owner = Mock()
        owner.snapshot.side_effect = ({"state": "starting", "completedMessages": 0},
                                      {"state": "running", "completedMessages": 3},
                                      {"state": "running", "completedMessages": 5})
        owner.stop.return_value = stopped()
        sleep = Mock()
        self.assertEqual(stopped(), replay.coordinate(owner, 5, 10., clock=lambda: 0., sleep=sleep))
        self.assertEqual(2, sleep.call_count)
        owner.start.assert_called_once()
        owner.stop.assert_called_once()

    def test_coordinator_fault_or_timeout_never_reports_clean_replay(self):
        for initial in ({"state": "failed", "completedMessages": 0},
                        {"state": "running", "completedMessages": 4}):
            owner = Mock()
            owner.snapshot.return_value = initial
            owner.stop.return_value = stopped()
            with self.assertRaises(RuntimeError):
                replay.coordinate(owner, 5, 1., clock=Mock(side_effect=(0., .1, 2.)), sleep=lambda _: None)
            owner.stop.assert_called_once()
        owner = Mock()
        owner.snapshot.return_value = {"state": "running", "completedMessages": 6}
        owner.stop.return_value = stopped()
        with self.assertRaises(ValueError):
            replay.coordinate(owner, 5, 1., clock=lambda: 0.)
        owner.stop.assert_called_once()

    def test_completed_count_alone_does_not_override_failed_process_cleanup(self):
        for field, value in (("state", "failed"), ("exitCode", 1), ("forcedTermination", True),
                              ("cleanupComplete", False), ("monitorAlive", True), ("processAlive", True)):
            owner = Mock()
            owner.snapshot.return_value = {"state": "running", "completedMessages": 5}
            owner.stop.return_value = {**stopped(), field: value}
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                replay.coordinate(owner, 5, 1., clock=lambda: 0.)

    def test_interrupt_still_requests_stop_and_joins_owner(self):
        owner = Mock()
        owner.snapshot.side_effect = KeyboardInterrupt()
        owner.stop.return_value = stopped()
        with self.assertRaises(KeyboardInterrupt):
            replay.coordinate(owner, 5, 1., clock=lambda: 0.)
        owner.stop.assert_called_once()

    def test_private_report_writer_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            replay.private_json(path, {"result": "synthetic"})
            with self.assertRaises(FileExistsError):
                replay.private_json(path, {"result": "overwritten"})
            self.assertEqual({"result": "synthetic"}, replay.load(path))

    def test_staging_preserves_input_bytes_and_never_changes_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, bundle, reference, output = (root / name for name in ("source", "bundle", "reference", "output"))
            names = ("tools/run_glim_supervised_replay.py", "robot-slam/lingxi_slam/worker.py")
            for name in names:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"# explicitly synthetic source\n")
            for name in ("config/sample.json", "baseline-config/sample.json", "replay-manifest.json",
                         "control-manifest.json", "private-profile.json", "private-sensor-diagnostic.json"):
                path = bundle / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic configuration")
            reference.mkdir()
            for name in ("report.json", "private-callbacks.cdrjournal"):
                (reference / name).write_bytes(b"synthetic input, not a real journal")
            args = argparse.Namespace(bundle=bundle, reference=reference, output=output, hypothesis="synthetic")
            with patch.object(replay, "ROOT", source), patch.object(replay, "SOURCE_NAMES", names):
                staged, runtime, copied = replay.stage_private_inputs(args)
            self.assertEqual(set(names), set(copied))
            for name in names:
                self.assertEqual((source / name).read_bytes(), (runtime / name).read_bytes())
            self.assertEqual((reference / "private-callbacks.cdrjournal").read_bytes(),
                             (staged.reference / "private-callbacks.cdrjournal").read_bytes())
            self.assertNotEqual(reference, staged.reference)
            self.assertEqual(reference, args.reference)
            self.assertEqual(output, staged.output)

    def test_native_stdout_cannot_enter_health_pipe(self):
        text = (ROOT / "tools/run_glim_supervised_replay.py").read_text(encoding="utf-8")
        self.assertIn('os.fdopen(os.dup(1), "wb", buffering=0)', text)
        self.assertIn("os.dup2(2, 1)", text)
        self.assertNotIn("import rclpy", text)
        self.assertNotIn("create_publisher", text)

    def fixture_evidence(self, directory, *, mutate=None):
        from verify_slam_capture import sha256

        root = Path(directory)
        result = {
            **child_report(), "result": replay.CHILD_RESULT, "session": "a" * 32,
            "replayRate": .5,
            "machine": "synthetic", "versions": {"numpy": "synthetic", "rosbags": "synthetic"},
            "actualLoadedLibrariesSha256": {"synthetic.so": "a" * 64},
            "privateMarker": "PRIVATE_MARKER",
            "inputIntegrity": {
                "sourceSha256": {"synthetic": "a" * 64}, "sourceCommits": {},
                "journal": EXPECTED, "journalSha256": "b" * 64, "configurationSha256": {},
            },
        }
        result["worker"]["poses"] = "PRIVATE_MARKER"
        process = {**stopped(), "processedScans": 2, "estimatedPoses": 2, "session": "a" * 32,
                   "mappingReady": False, "localizationReady": False, "navigationReady": False}
        parent = {
            "result": replay.RESULT, "process": process, "limits": {},
            "replayRate": .5,
            "inputIntegrity": result["inputIntegrity"], "boundaries": dict.fromkeys(replay.BOUNDARIES, False),
        }
        if mutate:
            mutate(parent, result)
        replay.private_json(root / "child-report.json", result)
        (root / "private-child.log").write_bytes(b"PRIVATE_MARKER")
        parent.update(childReportSha256=sha256(root / "child-report.json"),
                      privateLogSha256=sha256(root / "private-child.log"))
        replay.private_json(root / "report.json", parent)
        return root

    def test_public_evidence_allowlists_aggregates_not_raw_report_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture_evidence(directory)
            with patch.object(replay, "hashes", return_value={"synthetic": "a" * 64}):
                result = replay.evidence(root)
        self.assertNotIn("PRIVATE_MARKER", json.dumps(result))
        self.assertNotIn("session", result["process"])
        self.assertNotIn("worker", result)
        self.assertEqual(5, result["completedWorkerCounts"]["completedMessages"])
        self.assertEqual(2, result["streamCounts"]["processedScans"])
        self.assertEqual(.5, result["replayRate"])
        self.assertFalse(result["oneTimesReplayPerformanceValidated"])

    def test_evidence_rejects_failure_session_change_and_readiness_claim(self):
        def failed(parent, child):
            child["result"] = "failed_or_interrupted"

        def wrong_session(parent, child):
            child["session"] = "b" * 32

        def cleanup_incomplete(parent, child):
            parent["process"]["cleanupComplete"] = False

        def unapproved_readiness(parent, child):
            parent["process"]["navigationReady"] = True

        for mutate in (failed, wrong_session, cleanup_incomplete, unapproved_readiness):
            with tempfile.TemporaryDirectory() as directory:
                root = self.fixture_evidence(directory, mutate=mutate)
                with patch.object(replay, "hashes", return_value={"synthetic": "a" * 64}):
                    with self.subTest(mutate=mutate.__name__), self.assertRaises(ValueError):
                        replay.evidence(root)

    def test_evidence_rejects_log_or_source_changes_after_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture_evidence(directory)
            with patch.object(replay, "hashes", return_value={"changed": "b" * 64}):
                with self.assertRaises(ValueError):
                    replay.evidence(root)
            (root / "private-child.log").write_bytes(b"changed")
            with patch.object(replay, "hashes", return_value={"synthetic": "a" * 64}):
                with self.assertRaises(ValueError):
                    replay.evidence(root)


@unittest.skipUnless(sys.platform == "linux" and importlib.util.find_spec("numpy") is not None,
                     "requires isolated Linux NumPy for actual SensorWorker thread")
class RealWorkerComposition(unittest.TestCase):
    def test_unchanged_worker_owns_synthetic_engine_create_work_and_close_off_main_thread(self):
        from lingxi_slam.worker import SensorWorker
        from lingxi_slam.messages import SensorProfile
        from test_slam_worker import cloud, imu
        import numpy as np

        main, owners, calls = threading.get_ident(), set(), dict.fromkeys(("create", "imu", "frame", "close"), 0)

        class Synthetic:
            def imu(self, *args):
                pass

            def frame(self, *args):
                return np.eye(4), np.array([0., 0., 10., 0.])

            def close(self):
                pass

        def record(name):
            owners.add(threading.get_ident())
            calls[name] += 1

        worker = SensorWorker(lambda: replay.OwnedNative(Synthetic, record, lambda: None),
                              SensorProfile("synthetic_cloud", "synthetic_imu",
                                            "frame_relative_nanoseconds_hypothesis"),
                              "header_is_scan_start").start()
        try:
            worker.offer_imu(imu(0))
            worker.offer_cloud(cloud(10))
            worker.offer_imu(imu(40))
            deadline = time.monotonic() + .4
            while time.monotonic() < deadline:
                if worker.snapshot()["counts"]["completedMessages"] == 3:
                    break
                time.sleep(.002)
            self.assertEqual(3, worker.snapshot()["counts"]["completedMessages"])
        finally:
            state = worker.stop()
        self.assertEqual("stopped", state["state"])
        self.assertEqual({"create": 1, "imu": 2, "frame": 1, "close": 1}, calls)
        self.assertEqual(1, len(owners))
        self.assertNotIn(main, owners)
        sink = io.BytesIO()
        event = replay.HealthEmitter(sink, "a" * 32).emit(state)
        self.assertEqual(event, decode(sink.getvalue(), 4096))
        self.assertEqual(3, event["completedMessages"])


if __name__ == "__main__":
    unittest.main()
