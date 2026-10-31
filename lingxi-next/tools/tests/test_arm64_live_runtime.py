import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import check_arm64_live_runtime as runtime


class LiveRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.terminal = dict(state="stopped", exitCode=0, forcedTermination=False,
                             cleanupComplete=True, processAlive=False, monitorAlive=False)
        self.manager = Mock()
        self.manager.start.return_value = {"session": "a" * 32}
        self.manager.current = {"owner": Mock()}
        self.manager.current["owner"].snapshot.return_value = self.terminal
        self.state = dict(session="a" * 32, phase="running", processedScans=120, estimatedPoses=60)
        self.manager.state.return_value = self.state
        self.manager.finish.return_value = dict(session="a" * 32, confirmed=True, saved=True,
                                               mapId="slam-" + "a" * 32)

    def capture(self):
        with patch.object(runtime.time, "monotonic", side_effect=[0., 1., 11.]):
            return runtime.capture(self.manager, seconds=35.)

    def test_progress_saves_once_after_warmup(self):
        saved, observed, terminal = self.capture()
        self.assertTrue(saved["saved"])
        self.assertEqual(observed, self.state)
        self.assertEqual(terminal, self.terminal)
        self.manager.finish.assert_called_once_with("a" * 32, save=True, name="Isolated runtime live check")

    def test_timeout_does_not_save(self):
        with patch.object(runtime.time, "monotonic", side_effect=[0., 36.]):
            with self.assertRaises(TimeoutError):
                runtime.capture(self.manager, seconds=35.)
        self.manager.finish.assert_not_called()

    def test_failure_or_changed_owner_does_not_save(self):
        for changed in ({"phase": "failed"}, {"session": "b" * 32}):
            with self.subTest(changed=changed):
                self.manager.state.return_value = {**self.state, **changed}
                with self.assertRaisesRegex(RuntimeError, "same_healthy"):
                    self.capture()
                self.manager.finish.assert_not_called()

    def test_thresholds_and_warmup_are_required(self):
        for changed in ({"phase": "requested"}, {"processedScans": 119}, {"estimatedPoses": 59}):
            with self.subTest(changed=changed):
                self.manager.state.return_value = {**self.state, **changed}
                with patch.object(runtime.time, "monotonic", side_effect=[0., 1., 36.]), \
                        patch.object(runtime.time, "sleep"):
                    with self.assertRaises(TimeoutError):
                        runtime.capture(self.manager, seconds=35.)
                self.manager.finish.assert_not_called()
        self.manager.state.return_value = self.state
        with patch.object(runtime.time, "monotonic", side_effect=[0., 1., 9., 36.]), \
                patch.object(runtime.time, "sleep"):
            with self.assertRaises(TimeoutError):
                runtime.capture(self.manager, seconds=35.)
        self.manager.finish.assert_not_called()

    def test_unconfirmed_save_cannot_pass(self):
        for key, value in (("confirmed", False), ("saved", False), ("session", "b" * 32), ("mapId", "old")):
            with self.subTest(key=key):
                previous = self.manager.finish.return_value
                self.manager.finish.return_value = {**previous, key: value}
                with self.assertRaisesRegex(RuntimeError, "confirmed_fresh_live"):
                    self.capture()
                self.manager.finish.return_value = previous

    def test_unclean_owner_cannot_pass(self):
        for key, value in (("state", "failed"), ("exitCode", 1), ("exitCode", False),
                           ("forcedTermination", True), ("cleanupComplete", False),
                           ("processAlive", True), ("monitorAlive", True)):
            with self.subTest(key=key):
                with self.assertRaisesRegex(RuntimeError, "clean_live_mapping"):
                    runtime.clean_terminal({**self.terminal, key: value})

    def test_consumer_starts_and_failure_prevents_save(self):
        consumer = Mock(error="ValueError")
        with patch.object(runtime.time, "monotonic", side_effect=[0., 1.]):
            with self.assertRaisesRegex(RuntimeError, "healthy_live_point"):
                runtime.capture(self.manager, seconds=35., consumer=consumer)
        consumer.start.assert_called_once_with(self.manager.start.return_value)
        self.manager.finish.assert_not_called()

    def test_storage_identity_is_not_process_session(self):
        self.manager.current["output"] = Path("/maps") / ("b" * 32)
        self.manager.current["owner"].session = "a" * 32
        self.assertEqual(runtime.saved_directory(self.manager, {"session": "a" * 32}),
                         Path("/maps") / ("b" * 32))
        with self.assertRaisesRegex(RuntimeError, "same_mapping_owner"):
            runtime.saved_directory(self.manager, {"session": "c" * 32})


@unittest.skipUnless(importlib.util.find_spec("numpy"), "requires isolated NumPy")
class PointConsumerTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(TOOLS.parent / "robot-slam"))
        import numpy as np
        from lingxi_slam.point_data import PointEncoder
        from lingxi_slam.point_output import PointPacket
        self.identity = "a" * 32
        self.payload = PointEncoder(self.identity, "live").encode(
            PointPacket(1, 1., 1, np.zeros((1, 3)), np.eye(4)))
        self.state = {"session": self.identity, "pointStream": {"socket": "a.points.sock"}}
        self.socket = Mock()
        self.factory = Mock()
        self.factory.return_value.__enter__ = Mock(return_value=self.socket)
        self.factory.return_value.__exit__ = Mock(return_value=False)

    def consume(self, blocks):
        consumer = runtime.PointConsumer(Path("/unused"))
        self.socket.recv.side_effect = blocks
        with patch.object(runtime.socket, "socket", self.factory), \
                patch.object(runtime.socket, "AF_UNIX", 1, create=True):
            consumer.run(self.state)
        return consumer

    def test_fragmented_frame_and_terminal_partial_bytes(self):
        consumer = self.consume([self.payload[:10], self.payload[10:200], self.payload[200:], b"end", b""])
        self.assertIsNone(consumer.error)
        self.assertEqual((consumer.frames, consumer.last_frame, consumer.trailing_bytes), (1, 1, 3))

    def test_repeated_or_wrong_session_frame_fails(self):
        for payload in (self.payload + self.payload,
                        self.payload[:48] + bytes.fromhex("b" * 32) + self.payload[64:]):
            with self.subTest(payload=len(payload)):
                consumer = self.consume([payload, b""])
                self.assertEqual(consumer.error, "ValueError")

    def test_oversized_frame_rejected_before_body(self):
        import struct
        invalid = bytearray(self.payload[:64])
        struct.pack_into("<I", invalid, 12, 8001)
        consumer = self.consume([invalid, b""])
        self.assertEqual((consumer.frames, consumer.error), (0, "ValueError"))


class PublishedLiveEvidence(unittest.TestCase):
    def setUp(self):
        self.root = TOOLS.parent
        self.value = json.loads((self.root / "docs/slam-python-runtime-live-arm64-evidence.json").read_bytes())

    def test_current_runtime_sources_and_lock_match_checked_bytes(self):
        for name, expected in self.value["sourceSha256"].items():
            self.assertEqual(expected, hashlib.sha256((self.root / name).read_bytes()).hexdigest())
        self.assertEqual(self.value["lockSha256"], hashlib.sha256(
            (self.root / "robot-slam/runtime-arm64-cp310.lock.json").read_bytes()).hexdigest())

    def test_two_fresh_runs_confirm_lifecycle_without_motion_or_readiness_claim(self):
        self.assertEqual(["j", "k"], [run["trial"] for run in self.value["runs"]])
        self.assertTrue(all(value is False for value in self.value["boundaries"].values()))
        for run in self.value["runs"]:
            self.assertTrue(run["sourceHashAndCollectionVerified"])
            self.assertTrue(run["allProcessesCleaned"])
            self.assertFalse(run["forcedTermination"])
            self.assertEqual([0, 0, 0], run["mappingAndLocalizationExitCodes"])
            self.assertTrue(run["mapping"]["freshProcessReloadPassed"])
            self.assertEqual(2, run["mapping"]["schemaVersion"])
            self.assertGreater(run["mapping"]["visualPointFrames"], 5)
            state = run["localization"]
            self.assertEqual(0, state["seedRequests"])
            self.assertEqual([1, 2, 3, 4, 5], state["referenceFramesObserved"])
            self.assertGreater(state["stoppedQueries"], 5)
            for key in ("pauseConfirmed", "stopConfirmed", "staleGenerationRejected", "closedBasePoseInputs"):
                self.assertTrue(state[key])
            self.assertEqual(state["timedFrames"], state["tfLookupsAvailable"] + state["tfLookupsUnavailable"])

    def test_public_evidence_has_no_connection_secrets_or_raw_sensor_coordinates(self):
        text = json.dumps(self.value)
        for marker in ("/home/", "192.168.", "10.201.", '"password"', '"credentials"',
                       '"poses"', '"mapFromSensor"', '"pointCoordinates"'):
            self.assertNotIn(marker, text)

    def test_retained_private_reports_agree_when_available(self):
        for run in self.value["runs"]:
            directory = self.root / ".local" / ("isolated-live-arm64-20261031-" + run["trial"])
            if not directory.is_dir():
                self.skipTest("private target records are intentionally not committed")
            payload = (directory / "live-runtime-report.json").read_bytes()
            report = json.loads(payload)
            self.assertEqual(run["privateReportSha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(runtime.RESULT, report["result"])
            self.assertEqual(run["mapping"]["points"], report["mapping"]["counts"]["points"])
            self.assertEqual(run["localization"]["processedScans"], report["localization"]["processedScans"])
            self.assertEqual(run["localization"]["timedFrames"],
                             report["localization"]["basePoseInput"]["observations"]["timedFrames"])
            collection = (directory / "collection-report.json").read_bytes()
            self.assertEqual(run["collectionReportSha256"], hashlib.sha256(collection).hexdigest())


if __name__ == "__main__":
    unittest.main()
