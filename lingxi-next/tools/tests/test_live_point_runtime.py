"""Isolated process composition contracts; no robot, native SLAM or real ROS."""
import argparse
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam"), str(ROOT / "tools")]
import live_child
import live_points
import point_owner
from lingxi_slam.process_protocol import decode


class LiveConfiguration(unittest.TestCase):
    def test_owner_deadlines_must_be_finite_positive_and_consistent(self):
        for settings in ({"tick_seconds": True}, {"signal_seconds": 0},
                         {"api_stop_seconds": float("nan")}, {"api_start_seconds": float("inf")},
                         {"tick_seconds": 30.}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                point_owner.OwnerLimits(**settings)

    def test_streaming_digest_accepts_exact_bytes_and_rejects_mismatch_or_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "isolated-artifact"
            artifact.write_bytes(b"synthetic-runtime" * 20_000)
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            live_points.digest_file(artifact, digest)
            for expected in ("b" * 64, digest.upper(), "", "z" * 64):
                with self.subTest(expected=expected), self.assertRaises(ValueError):
                    live_points.digest_file(artifact, expected)
            with self.assertRaises(ValueError):
                live_points.digest_file(artifact.parent / "missing", digest)

    @unittest.skipUnless(sys.platform == "linux", "uses the local POSIX account database")
    def test_environment_is_explicit_and_does_not_inherit_local_or_secret_settings(self):
        root, core = Path("/synthetic/live"), Path("/synthetic/core")
        with patch.dict(os.environ, {"ROS_ARGS": "synthetic", "PRIVATE_MARKER": "not-inherited",
                                   "HOME": "/synthetic/never-inherit-home"}):
            environment = live_points.environment(root, core)
        self.assertEqual("humble", environment["ROS_DISTRO"])
        self.assertEqual("0", environment["ROS_DOMAIN_ID"])
        self.assertEqual("rmw_fastrtps_cpp", environment["RMW_IMPLEMENTATION"])
        self.assertEqual("2", environment["OMP_NUM_THREADS"])
        self.assertEqual("2", environment["OPENBLAS_NUM_THREADS"])
        import pwd
        self.assertEqual(pwd.getpwuid(os.geteuid()).pw_dir, environment["HOME"])
        self.assertNotIn("ROS_ARGS", environment)
        self.assertNotIn("PRIVATE_MARKER", environment)
        self.assertNotIn("LD_PRELOAD", environment)

    def test_no_onsite_confirmation_is_rejected_before_any_process_or_artifact_access(self):
        with patch.object(live_points, "PointRuntimeOwner") as owner:
            with self.assertRaisesRegex(ValueError, "fresh_stationary"):
                live_points.run(argparse.Namespace(operator_confirmed=False))
            owner.assert_not_called()

    def configured(self, directory):
        root = Path(directory)
        for name in ("live", "core", "points", "runtime"):
            (root / name).mkdir()
        node, api = root / "runtime/node", root / "runtime/api.mjs"
        node.write_bytes(b"isolated-synthetic-executable-not-executed")
        api.write_bytes(b"isolated-synthetic-api-not-executed")
        return argparse.Namespace(
            operator_confirmed=True, root=root / "live", core_root=root / "core",
            point_bundle=root / "points", node=node, api_bundle=api, output=root / "output",
            seconds=5., node_sha256=hashlib.sha256(node.read_bytes()).hexdigest(),
            api_sha256=hashlib.sha256(api.read_bytes()).hexdigest(),
            point_manifest_sha256="c" * 64, hypothesis="header_is_scan_start")

    def test_production_configuration_requires_tls_and_pairing_before_creating_owner(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(live_points.sys, "platform", "linux"), \
                patch.object(live_points.platform, "machine", return_value="aarch64"), \
                patch.object(live_points.sys, "version_info", (3, 10)), \
                patch.object(live_points.os, "geteuid", return_value=1000, create=True), \
                patch.object(live_points, "PointRuntimeOwner") as owner, \
                patch.dict(os.environ, {}, clear=True):
            args = self.configured(directory)
            with self.assertRaisesRegex(ValueError, "production_tls_and_pairing"):
                live_points.run(args)
            owner.assert_not_called()

    def test_production_composition_does_not_inherit_demo_secrets_or_arbitrary_runtime_flags(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(live_points.sys, "platform", "linux"), \
                patch.object(live_points.platform, "machine", return_value="aarch64"), \
                patch.object(live_points.sys, "version_info", (3, 10)), \
                patch.object(live_points.os, "geteuid", return_value=1000, create=True), \
                patch.object(live_points.signal, "signal", return_value=None), \
                patch.object(live_points, "PointRuntimeOwner") as owner, \
                patch.dict(os.environ, {
                    "LINGXI_TLS_KEY": "/synthetic/private-key", "LINGXI_TLS_CERT": "/synthetic/certificate",
                    "LINGXI_PAIRING_CODE_SHA256": "a" * 64, "LINGXI_DEMO_MODE": "1",
                    "PRIVATE_MARKER": "not-inherited", "NODE_OPTIONS": "not-inherited",
                }, clear=True):
            args = self.configured(directory)
            owner.return_value.run.return_value = {"result": "stopped"}
            self.assertEqual({"result": "stopped"}, live_points.run(args))
            configuration = owner.call_args.kwargs
            self.assertEqual("/synthetic/private-key", configuration["api_environment"]["LINGXI_TLS_KEY"])
            for name in ("LINGXI_DEMO_MODE", "PRIVATE_MARKER", "NODE_OPTIONS"):
                self.assertNotIn(name, configuration["api_environment"])
                self.assertNotIn(name, configuration["native_environment"])
            self.assertIn("child", owner.call_args.args[0])
            self.assertEqual(5., owner.return_value.run.call_args.kwargs["seconds"])


@unittest.skipUnless(sys.platform == "linux", "Linux pipe control and ordinary-user process owner")
class OwnerContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def owner(self, **settings):
        return point_owner.PointRuntimeOwner(
            [sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"],
            native_environment={}, api_environment={}, output=self.root / "run", **settings)

    def test_explicit_session_and_parent_lifetime_cannot_be_overridden(self):
        for key in ("LINGXI_SLAM_POINT_FD", "LINGXI_SLAM_SESSION", "LINGXI_SLAM_INPUT_MODE",
                    "LINGXI_POINT_PARENT_STDIN", "LD_PRELOAD", "NODE_OPTIONS"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                point_owner.PointRuntimeOwner(
                    [sys.executable], [sys.executable], native_environment={},
                    api_environment={key: "synthetic"}, output=self.root / "run")
        self.assertFalse((self.root / "run").exists())

    def test_preexisting_stop_starts_neither_child_and_writes_private_terminal_report(self):
        owner, stop = self.owner(), threading.Event()
        stop.set()
        with patch.object(owner.native, "start") as start:
            report = owner.run(stop=stop, seconds=.1)
        start.assert_not_called()
        self.assertEqual("stopped", report["result"])
        self.assertIsNone(report["apiProcess"])
        self.assertTrue(report["sensorProcess"]["cleanupComplete"])
        self.assertEqual(report, json.loads((owner.output / "owner-report.json").read_text()))
        self.assertEqual(0o700, stat.S_IMODE(owner.output.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE((owner.output / "owner-report.json").stat().st_mode))
        with self.assertRaises(ValueError):
            owner.run(seconds=.1)

    def test_sensor_cleanup_exception_still_attempts_api_and_records_failure(self):
        owner = self.owner()
        with patch.object(owner, "_connect", side_effect=RuntimeError("synthetic")), \
                patch.object(owner.native, "stop", side_effect=RuntimeError("PRIVATE_MARKER")), \
                patch.object(owner, "_close_api", return_value=None) as close_api:
            report = owner.run(seconds=.1)
        close_api.assert_called_once()
        self.assertEqual("failed", report["result"])
        self.assertEqual("sensor_cleanup_failed", report["failure"])
        self.assertNotIn("PRIVATE_MARKER", (owner.output / "owner-report.json").read_text())

    def test_api_cleanup_exception_is_not_a_success_and_still_closes_owned_socket(self):
        owner, connection = self.owner(), Mock()
        owner.connection = connection
        with patch.object(owner, "_connect", side_effect=RuntimeError("synthetic")), \
                patch.object(owner, "_close_api", side_effect=RuntimeError("PRIVATE_MARKER")):
            report = owner.run(seconds=.1)
        connection.close.assert_called_once()
        self.assertEqual("failed", report["result"])
        self.assertEqual("api_cleanup_failed", report["failure"])
        self.assertFalse(report["mappingReady"])
        self.assertFalse(report["localizationReady"])
        self.assertFalse(report["navigationReady"])

    def test_exact_bounded_api_status_and_no_raw_diagnostics(self):
        owner = self.owner()
        owner.api = Mock(stdout=Mock())
        correct = b'{"service":"lingxi-next","event":"listening","port":1234}\n'
        with patch.object(point_owner.os, "read", return_value=correct):
            self.assertEqual(1234, owner._read_api()["port"])
        for payload in (correct + b"x", correct.replace(b"1234", b"true"), b"x" * 4097):
            owner.stdout_buffer.clear()
            with self.subTest(payload=payload[:80]), \
                    patch.object(point_owner.os, "read", return_value=payload), self.assertRaises(ValueError):
                owner._read_api()


class SyntheticWorker:
    def __init__(self):
        self.state, self.failure, self.stops = "new", None, 0

    def snapshot(self):
        return {"state": self.state, "failure": self.failure, "threadAlive": False,
                "counts": {"completedMessages": 0}, "stream": None}

    def stop(self):
        self.stops += 1
        if self.state != "failed":
            self.state = "stopped"
        return self.snapshot()


@unittest.skipUnless(sys.platform == "linux", "POSIX private control pipe")
class LiveChildControl(unittest.TestCase):
    def setUp(self):
        self.read_fd, self.write_fd = os.pipe()
        self.addCleanup(os.close, self.read_fd)
        self.addCleanup(self.close_writer)
        self.worker, self.sink = SyntheticWorker(), io.BytesIO()

    def close_writer(self):
        if self.write_fd is not None:
            os.close(self.write_fd)
            self.write_fd = None

    def runner(self, worker, topics, seconds, *, stop_event):
        self.assertEqual(300., seconds)
        if worker.state != "failed":
            worker.state = "running"
        os.write(self.write_fd, b"stop\n")
        self.assertTrue(stop_event.wait(2.))
        return {"purpose": "explicitly_synthetic_passive_fixture"}

    def serve(self, **settings):
        return live_child.serve(self.worker, object(), self.sink, runner=self.runner,
                                session="a" * 32, control_fd=self.read_fd, **settings)

    def test_stop_joins_control_and_emits_terminal_health_without_sensor_content(self):
        report = self.serve()
        self.assertEqual("live_sensor_session_closed", report["result"])
        self.assertTrue(report["stopReceived"])
        self.assertFalse(report["controlThreadAlive"])
        self.assertEqual(1, self.worker.stops)
        self.assertEqual([], report["failures"])
        events = [decode(line, 4096) for line in self.sink.getvalue().splitlines(keepends=True)]
        self.assertEqual("stopped", events[-1]["workerState"])
        self.assertEqual(list(range(len(events))), [event["sequence"] for event in events])
        self.assertTrue(all("points" not in event and "poses" not in event for event in events))

    def test_parent_eof_stops_worker_without_claiming_confirmed_stop(self):
        self.close_writer()
        def runner(worker, topics, seconds, *, stop_event):
            worker.state = "running"
            self.assertTrue(stop_event.wait(2.))
        with patch.object(self, "runner", runner):
            report = self.serve()
        self.assertEqual("failed", report["result"])
        self.assertFalse(report["stopReceived"])
        self.assertEqual(1, self.worker.stops)
        self.assertFalse(report["controlThreadAlive"])
        self.assertIn("live_parent_control_failed", report["failures"])

    def test_malformed_private_stop_fails_and_still_retires_worker(self):
        os.write(self.write_fd, b"wrong\n")
        def runner(worker, topics, seconds, *, stop_event):
            self.assertTrue(stop_event.wait(2.))
        with patch.object(self, "runner", runner):
            report = self.serve()
        self.assertEqual("failed", report["result"])
        self.assertEqual(1, self.worker.stops)
        self.assertFalse(report["controlThreadAlive"])

    def test_writer_failure_is_terminal_not_a_native_restart(self):
        def runner(worker, topics, seconds, *, stop_event):
            self.assertTrue(stop_event.wait(2.))
        with patch.object(self, "runner", runner):
            report = self.serve(writer_failed=lambda: True)
        self.assertEqual("failed", report["result"])
        self.assertIn("live_sensor_or_writer_failed", report["failures"])
        self.assertEqual(1, self.worker.stops)

    def test_sensor_failure_is_preserved_and_terminal_health_is_failed(self):
        self.worker.state, self.worker.failure = "failed", "synthetic_worker_failed"
        report = self.serve()
        self.assertEqual("failed", report["result"])
        self.assertEqual("synthetic_worker_failed", report["worker"]["failure"])
        self.assertEqual("failed", json.loads(self.sink.getvalue().splitlines()[-1])["workerState"])

    def test_ros_runner_exception_still_stops_worker_and_joins_control(self):
        with patch.object(self, "runner", side_effect=RuntimeError("PRIVATE_MARKER")):
            report = self.serve()
        self.assertEqual("failed", report["result"])
        self.assertFalse(report["controlThreadAlive"])
        self.assertEqual(1, self.worker.stops)
        self.assertIn("passive_sensor_session_failed", report["failures"])
        self.assertNotIn("PRIVATE_MARKER", json.dumps(report))

    def test_worker_cleanup_exception_is_recorded_and_terminal_health_still_emitted(self):
        with patch.object(self.worker, "stop", side_effect=RuntimeError("synthetic")):
            report = self.serve()
        self.assertEqual("failed", report["result"])
        self.assertIn("live_worker_cleanup_failed", report["failures"])
        self.assertFalse(report["controlThreadAlive"])
        self.assertEqual("failed", json.loads(self.sink.getvalue().splitlines()[-1])["workerState"])

    def test_closed_health_sink_still_stops_worker_without_raw_exception_report(self):
        self.sink.close()
        report = self.serve()
        self.assertEqual("failed", report["result"])
        self.assertIn("live_terminal_health_failed", report["failures"])
        self.assertEqual(1, self.worker.stops)
        self.assertFalse(report["controlThreadAlive"])

    def test_stop_callback_precedes_runner_exit_and_is_called_once(self):
        calls = []
        def runner(worker, topics, seconds, *, stop_event):
            self.assertEqual(60., seconds)
            worker.state = "running"
            os.write(self.write_fd, b"stop\n")
            self.assertTrue(stop_event.wait(2.))
            self.assertEqual(["revoked"], calls)
            self.assertEqual(0, worker.stops)
            return {"purpose": "explicitly_synthetic_passive_fixture"}
        with patch.object(self, "runner", runner):
            report = self.serve(seconds=60., on_stopping=lambda: calls.append("revoked"))
        self.assertEqual(["revoked"], calls)
        self.assertEqual("live_sensor_session_closed", report["result"])

    def test_stop_callback_failure_still_stops_worker_and_is_reported(self):
        callback = Mock(side_effect=RuntimeError("PRIVATE_MARKER"))
        report = self.serve(on_stopping=callback)
        callback.assert_called_once()
        self.assertEqual(1, self.worker.stops)
        self.assertEqual("failed", report["result"])
        self.assertIn("live_stop_callback_failed", report["failures"])
        self.assertNotIn("PRIVATE_MARKER", json.dumps(report))

    def test_invalid_duration_rejects_before_worker_or_runner(self):
        for value in (True, 0, -1, float("nan"), float("inf"), 301):
            with self.subTest(value=value), patch.object(self, "runner") as runner:
                with self.assertRaises(ValueError):
                    self.serve(seconds=value)
                runner.assert_not_called()
        self.assertEqual(0, self.worker.stops)


@unittest.skipUnless(sys.platform == "linux", "target artifact verification imports POSIX tooling")
class FrozenInputContract(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            root=Path("/synthetic/live"), core_root=Path("/synthetic/core"),
            point_bundle=Path("/synthetic/points"), point_manifest_sha256="c" * 64,
            hypothesis="header_is_scan_start")
        self.baseline = {"replayManifestSha256": "a" * 64, "coreBundleManifestSha256": "b" * 64,
                         "runs": {"start": {"ddsProfileSha256": "d" * 64}}}
        self.loading = {"bundleManifestSha256": "c" * 64, "coreManifestSha256": "b" * 64,
                        "actualLoadedLibrariesSha256": {"synthetic.so": "e" * 64}}
        self.live = Mock(DDS_PROFILE="/synthetic/dds")
        self.probe = Mock(LIBRARY="synthetic.so")
        self.probe.verify_bundle.return_value = {"files": {"lib/synthetic.so": {"sha256": "e" * 64}}}
        self.modules = {
            "glim_live_session": self.live, "glim_point_target_probe": self.probe,
            "glim_target_replay": Mock(), "slam_target_probe": Mock(),
            "run_glim_offline_trial": Mock(), "verify_slam_capture": Mock(),
        }
        self.modules["verify_slam_capture"].sha256.side_effect = lambda path: {
            "replay-manifest.json": "a" * 64, "bundle-manifest.json": "b" * 64, "dds": "d" * 64,
        }[path.name]
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, self.modules).start()
        patch.dict(os.environ, {}, clear=True).start()
        patch.object(Path, "read_text", autospec=True, side_effect=self.read_text).start()

    def read_text(self, path, **settings):
        return json.dumps(self.baseline if path.name == "slam-humble-live-evidence.json"
                          else self.loading if path.name == "slam-point-arm64-target-evidence.json" else {})

    def test_parent_preflight_needs_no_child_session_or_ros_environment(self):
        verified = live_child.verified_inputs(self.args)
        self.assertEqual(3, len(verified))
        self.live.validate_environment.assert_not_called()
        self.live.verify_control.assert_called_once_with(self.args.root)
        self.modules["run_glim_offline_trial"].explicit_diagnostic_profile.assert_called_once_with(
            {}, {}, "header_is_scan_start")

    def test_child_requires_supervisor_session_before_environment_validation(self):
        for value in (None, "", "invalid"):
            with self.subTest(value=value), patch.dict(os.environ, {} if value is None else {
                    "LINGXI_SLAM_SESSION": value}, clear=True):
                with self.assertRaisesRegex(ValueError, "explicit_process_session_id"):
                    live_child.verified_child_session(self.args)
        self.live.validate_environment.assert_not_called()

    def test_child_keeps_strict_ros_environment_validation(self):
        with patch.dict(os.environ, {"LINGXI_SLAM_SESSION": "a" * 32}):
            self.assertEqual("a" * 32, live_child.verified_child_session(self.args))
            self.live.validate_environment.assert_called_once_with(self.args.root, self.args.core_root)
            self.live.validate_environment.side_effect = ValueError("synthetic_bad_ros_environment")
            with self.assertRaisesRegex(ValueError, "bad_ros_environment"):
                live_child.verified_child_session(self.args)

    def test_manifest_dds_and_loaded_point_library_mismatches_are_not_adopted(self):
        original, loading = deepcopy(self.baseline), deepcopy(self.loading)
        for field in ("replayManifestSha256", "coreBundleManifestSha256", "dds", "point", "library"):
            self.baseline, self.loading = deepcopy(original), deepcopy(loading)
            if field == "dds":
                self.baseline["runs"]["start"]["ddsProfileSha256"] = "f" * 64
            elif field == "point":
                self.loading["bundleManifestSha256"] = "f" * 64
            elif field == "library":
                self.loading["actualLoadedLibrariesSha256"]["synthetic.so"] = "f" * 64
            else:
                self.baseline[field] = "f" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                live_child.verified_inputs(self.args)


if __name__ == "__main__":
    unittest.main()
