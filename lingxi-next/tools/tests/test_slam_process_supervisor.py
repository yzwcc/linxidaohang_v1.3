"""Actual local synthetic child ownership; no robot, ROS or native SLAM execution."""
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
with patch.object(sys, "path", [str(ROOT / "robot-slam")] + sys.path):
    from lingxi_slam.process_protocol import HealthEmitter, decode, validate
    from lingxi_slam.process_supervisor import ProcessLimits, ProcessSupervisor


def snapshot(completed=10):
    return {"state": "running", "failure": None, "counts": {"completedMessages": completed},
            "stream": {"counts": {"processedScans": 1, "estimatedPoses": 1}}}


class ProcessProtocol(unittest.TestCase):
    def event(self):
        sink = io.BytesIO()
        HealthEmitter(sink, "a" * 32, clock=lambda: 5.).emit(snapshot())
        return sink.getvalue()

    def test_exact_health_only_schema_and_contiguous_emitter_sequence(self):
        sink = io.BytesIO()
        emitter = HealthEmitter(sink, "a" * 32)
        first, second = emitter.emit(snapshot()), emitter.emit(snapshot())
        self.assertEqual((0, 1), (first["sequence"], second["sequence"]))
        self.assertEqual(first, decode(sink.getvalue().splitlines(keepends=True)[0], 4096))
        self.assertTrue(set(first).isdisjoint(("poses", "points", "password", "credentials")))

    def test_missing_extra_boolean_regressing_and_inconsistent_counters_rejected(self):
        original = decode(self.event(), 4096)
        for key, value in (("sequence", True), ("completedMessages", -1), ("processedScans", 11),
                           ("estimatedPoses", 2), ("version", True), ("session", "old"),
                           ("emittedAtSeconds", float("nan")), ("workerState", "ready"),
                           ("failure", "raw exception")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate({**original, key: value})
        with self.assertRaises(ValueError):
            validate({**original, "points": []})

    def test_partial_oversized_duplicate_and_non_json_lines_rejected(self):
        for payload, limit in ((self.event()[:-1], 4096), (self.event(), 10),
                               (b'{"version":1,"version":1}\n', 4096), (b"native log\n", 4096)):
            with self.assertRaises(ValueError):
                decode(payload, limit)

    def test_worker_starting_without_stream_and_fixed_failure_code(self):
        sink = io.BytesIO()
        emitter = HealthEmitter(sink, "b" * 32)
        event = emitter.emit({"state": "starting", "failure": None,
                              "counts": {"completedMessages": 0}, "stream": None})
        self.assertEqual(0, event["processedScans"])
        event["workerState"], event["failure"] = "failed", "native_cleanup_failed"
        self.assertEqual("failed", validate(event)["workerState"])

    def test_limits_reject_invalid_values_and_unbounded_protocol_sizes(self):
        for kwargs in ({"progress_seconds": True}, {"startup_seconds": float("inf")},
                       {"kill_seconds": 0}, {"tick_seconds": 3},
                       {"maximum_event_bytes": 1}, {"maximum_events_per_tick": 1000}):
            with self.assertRaises(ValueError):
                ProcessLimits(**kwargs)

    def test_partial_sink_write_never_advances_event_sequence(self):
        sink = unittest.mock.Mock()
        sink.write.return_value = 0
        emitter = HealthEmitter(sink, "c" * 32)
        with self.assertRaises(OSError):
            emitter.emit(snapshot())
        self.assertEqual(0, emitter.sequence)


@unittest.skipUnless(sys.platform == "linux", "process-group lifecycle requires Linux")
class ChildOwnership(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.owners = []

    def tearDown(self):
        for owner in self.owners:
            owner.stop()
        self.directory.cleanup()

    def owner(self, mode, **overrides):
        limits = ProcessLimits(**{"startup_seconds": 2., "progress_seconds": .5, "shutdown_seconds": .3,
                                  "terminate_seconds": .15, "kill_seconds": .5, "tick_seconds": .01,
                                  **overrides})
        owner = ProcessSupervisor(
            [sys.executable, "-u", str(ROOT / "tools/tests/fixtures/slam_process_child.py"), mode],
            cwd=self.root, environment={"PYTHONPATH": str(ROOT / "robot-slam"), "PYTHONUNBUFFERED": "1"},
            stderr_path=self.root / (str(len(self.owners)) + "-" + mode + ".log"), limits=limits,
        )
        self.owners.append(owner)
        return owner

    def wait_for(self, owner, predicate, timeout=5.):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = owner.snapshot()
            if predicate(result):
                return result
            time.sleep(.01)
        self.fail("expected process state not observed: " + repr(owner.snapshot()))

    def closed(self, owner):
        result = self.wait_for(owner, lambda value: value["cleanupComplete"])
        result = owner.stop()
        self.assertFalse(result["processAlive"])
        self.assertFalse(result["monitorAlive"])
        self.assertTrue(result["cleanupComplete"])
        self.assertTrue(all(result[key] is False for key in ("mappingReady", "localizationReady", "navigationReady")))
        return result

    def test_normal_progress_stop_ack_and_late_status_are_retired(self):
        owner = self.owner("normal").start()
        self.wait_for(owner, lambda value: value["completedMessages"] >= 3)
        result = owner.stop()
        self.assertEqual("stopped", result["state"])
        self.assertEqual(0, result["exitCode"])
        self.assertFalse(result["forcedTermination"])
        self.assertGreaterEqual(result["retiredHealthEvents"], 2)
        self.closed(owner)
        self.assertEqual(0o600, stat.S_IMODE(owner.stderr_path.stat().st_mode))

    def test_heartbeats_do_not_reset_actual_progress_deadline(self):
        owner = self.owner("heartbeats_only").start()
        result = self.closed(owner)
        self.assertEqual("process_progress_deadline_exceeded", result["failure"])
        self.assertGreater(result["healthEvents"], 2)

    def test_startup_deadline_escalates_to_kill_when_term_ignored(self):
        owner = self.owner("no_start").start()
        result = self.closed(owner)
        self.assertEqual("process_startup_deadline_exceeded", result["failure"])
        self.assertEqual(-9, result["exitCode"])

    def test_shutdown_deadline_cannot_be_hidden_by_active_statuses(self):
        owner = self.owner("ignore_stop_and_term").start()
        self.wait_for(owner, lambda value: value["completedMessages"] >= 2)
        result = owner.stop()
        self.assertEqual("process_shutdown_deadline_exceeded", result["failure"])
        self.assertEqual(-9, result["exitCode"])
        self.closed(owner)

    def test_unexpected_nonzero_exit_is_fault_not_success(self):
        owner = self.owner("early_exit").start()
        result = self.closed(owner)
        self.assertEqual("failed", result["state"])
        self.assertEqual(23, result["exitCode"])

    def test_wrong_session_and_stale_events_fail_closed(self):
        for mode in ("wrong_session", "stale"):
            with self.subTest(mode=mode):
                result = self.closed(self.owner(mode).start())
                self.assertEqual("invalid_child_health_protocol", result["failure"])

    def test_duplicate_sequence_and_counter_regression_are_not_progress(self):
        owner = self.owner("normal")
        owner.state, owner.started_at, owner.progress_at = "starting", 5., 5.
        sink = io.BytesIO()
        emitter = HealthEmitter(sink, owner.session, clock=lambda: 5.)
        emitter.emit(snapshot())
        payload = sink.getvalue()
        owner._event(payload, 5.)
        with self.assertRaises(ValueError):
            owner._event(payload, 5.)
        value = json.loads(payload)
        value.update(sequence=1, completedMessages=9)
        with self.assertRaises(ValueError):
            owner._event((json.dumps(value) + "\n").encode(), 5.)
        owner.state = "new"

    def test_exit_with_descendant_holding_pipe_is_detected_and_group_is_cleaned(self):
        owner = self.owner("descendant_pipe").start()
        result = self.closed(owner)
        self.assertEqual("child_exited_without_confirmed_stop", result["failure"])
        self.assertEqual(0, result["exitCode"])
        self.assertTrue(result["forcedTermination"])
        self.assertFalse(owner._group_has_live_members())

    def test_explicit_environment_does_not_inherit_parent_settings(self):
        with patch.dict(os.environ, {"LINGXI_TEST_AMBIENT": "isolated-test-sentinel"}):
            owner = self.owner("check_environment").start()
            self.wait_for(owner, lambda value: value["completedMessages"] >= 2)
            self.assertEqual("stopped", owner.stop()["state"])
        self.closed(owner)

    def test_thread_start_failure_reaps_spawned_child_and_closes_private_pipes(self):
        owner = self.owner("normal")
        with patch("lingxi_slam.process_supervisor.threading.Thread.start", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                owner.start()
        result = owner.snapshot()
        self.assertEqual("process_start_failed", result["failure"])
        self.assertTrue(result["cleanupComplete"])
        self.assertFalse(result["processAlive"])
        self.assertTrue(owner.process.stdin.closed)
        self.assertTrue(owner.process.stdout.closed)

    def test_ten_new_sessions_release_descriptors_and_supervisor_threads(self):
        descriptors = len(os.listdir("/proc/self/fd"))
        monitors = [thread.ident for thread in threading.enumerate() if thread.name == "lingxi-slam-process"]
        sessions = set()
        for _ in range(10):
            owner = self.owner("normal").start()
            self.wait_for(owner, lambda value: value["completedMessages"] >= 1)
            result = owner.stop()
            self.assertEqual("stopped", result["state"])
            self.assertTrue(result["cleanupComplete"])
            self.assertFalse(result["monitorAlive"])
            sessions.add(result["session"])
        self.assertEqual(10, len(sessions))
        self.assertEqual(descriptors, len(os.listdir("/proc/self/fd")))
        self.assertEqual(monitors, [thread.ident for thread in threading.enumerate()
                                    if thread.name == "lingxi-slam-process"])

    def test_stopping_one_session_does_not_terminate_another_owned_session(self):
        first, second = self.owner("normal").start(), self.owner("normal").start()
        self.wait_for(first, lambda value: value["completedMessages"] >= 2)
        before = self.wait_for(second, lambda value: value["completedMessages"] >= 2)["completedMessages"]
        self.assertEqual("stopped", first.stop()["state"])
        self.wait_for(second, lambda value: value["completedMessages"] > before)
        self.assertEqual("running", second.snapshot()["state"])
        self.assertEqual("stopped", second.stop()["state"])

    def test_reported_failure_is_fixed_code_not_raw_log(self):
        result = self.closed(self.owner("failed_status").start())
        self.assertEqual("child_reported_failure", result["failure"])
        self.assertEqual("synthetic_engine_failed", result["childFailure"])

    def test_owner_cannot_restart_and_existing_private_log_is_preserved(self):
        owner = self.owner("normal")
        owner.stderr_path.write_bytes(b"existing private log")
        with self.assertRaises(ValueError):
            ProcessSupervisor(owner.command, cwd=self.root, environment=owner.environment,
                              stderr_path=owner.stderr_path)
        self.assertEqual("stopped", owner.stop()["state"])
        with self.assertRaises(RuntimeError):
            owner.start()
        self.assertEqual(b"existing private log", owner.stderr_path.read_bytes())

    @unittest.skipUnless(importlib.util.find_spec("numpy") is not None, "synthetic SensorWorker needs NumPy")
    def test_real_worker_with_synthetic_engine_processes_packets_and_closes(self):
        owner = self.owner("worker_normal").start()
        self.wait_for(owner, lambda value: value["estimatedPoses"] >= 2)
        result = owner.stop()
        self.assertEqual("stopped", result["state"])
        self.closed(owner)

    @unittest.skipUnless(importlib.util.find_spec("numpy") is not None, "synthetic SensorWorker needs NumPy")
    def test_synthetic_worker_start_frame_and_close_hangs_cannot_leave_child_running(self):
        for mode in ("worker_start_hang", "worker_frame_hang", "worker_close_hang"):
            with self.subTest(mode=mode):
                owner = self.owner(mode).start()
                if mode == "worker_close_hang":
                    self.wait_for(owner, lambda value: value["estimatedPoses"] >= 2)
                    owner.stop()
                result = self.closed(owner)
                self.assertEqual("failed", result["state"])
                self.assertTrue(result["forcedTermination"])


if __name__ == "__main__":
    unittest.main()
