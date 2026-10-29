"""Explicitly synthetic orchestration contracts, no real native engine or robot."""
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import run_glim_point_pipe_trial as trial


class ConsumerEvidenceTests(unittest.TestCase):
    def fixture(self):
        output = {"packets": 2, "copiedPoints": 4, "allPacketBytesSha256": "a" * 64}
        consumer = {
            "result": "recorded_native_points_received_api_live_guard_checked",
            "inputMode": "recorded", "transport": "private_same_host_unix_stream",
            "points": {
                "records": 2, "copiedPoints": 4, "retired": 0,
                "firstNativeFrameId": 1, "lastNativeFrameId": 2,
                "allPacketBytesSha256": "a" * 64, "inputMode": "recorded",
            },
            "api": {
                "pairedWebSocketChecked": True, "recordedInputRejectedAsLive": True,
                "beforeAndAfterEofUnavailable": True, "readinessUnchanged": True,
                "robotCommandsSent": False, "tcpListenerStarted": False,
            },
        }
        return output, consumer

    def test_all_packet_counts_digest_and_isolation_checks_must_match(self):
        output, consumer = self.fixture()
        trial.verify_consumer(consumer, output)
        for key, value in (("records", 1), ("copiedPoints", 3), ("retired", 1),
                           ("firstNativeFrameId", 2), ("lastNativeFrameId", 1),
                           ("allPacketBytesSha256", "b" * 64), ("inputMode", "live")):
            current = deepcopy(consumer)
            current["points"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                trial.verify_consumer(current, output)
        for key in consumer["api"]:
            current = deepcopy(consumer)
            current["api"][key] = not current["api"][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                trial.verify_consumer(current, output)

    def test_transport_mode_and_result_cannot_be_relabelled(self):
        output, consumer = self.fixture()
        for key, value in (("inputMode", "live"), ("transport", "tcp"), ("result", "synthetic_only")):
            current = deepcopy(consumer)
            current[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                trial.verify_consumer(current, output)

    def test_current_source_inventory_includes_unchanged_health_and_new_api_chain(self):
        hashes = trial.source_hashes()
        for name in ("tools/slam-point-api-consumer.ts", "robot-api/src/server.ts",
                     "robot-api/src/slam-points.ts", "robot-slam/lingxi_slam/point_data.py",
                     "robot-slam/lingxi_slam/point_pipe.py", "package-lock.json"):
            self.assertIn(name, hashes)
        self.assertTrue(all(len(digest) == 64 for digest in hashes.values()))
        self.assertIn("robot-slam/lingxi_slam/process_supervisor.py", trial.replay.hashes())


@unittest.skipUnless(sys.platform == "linux", "Private local subprocess control is Linux-only")
class ConsumerLifecycleTests(unittest.TestCase):
    def process(self, code):
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", code], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: trial.stop_consumer(child))
        return child

    def test_exact_ready_line_and_owned_process_exit(self):
        child = self.process("import time; print('ready', flush=True); time.sleep(.1)")
        trial.wait_ready(child, 1)
        self.assertEqual(0, child.wait(timeout=1))
        self.assertEqual({"exitCode": 0, "forcedTermination": False, "processAlive": False},
                         trial.stop_consumer(child))

    def test_malformed_ready_line_is_not_accepted(self):
        child = self.process("print('wrong', flush=True)")
        with self.assertRaises(ValueError):
            trial.wait_ready(child, 1)

    def test_early_eof_without_readiness_is_not_accepted(self):
        child = self.process("pass")
        with self.assertRaises(RuntimeError):
            trial.wait_ready(child, 1)

    def test_owned_hung_consumer_is_stopped(self):
        child = self.process("import time; time.sleep(30)")
        state = trial.stop_consumer(child)
        self.assertTrue(state["forcedTermination"])
        self.assertFalse(state["processAlive"])
        self.assertIsNotNone(state["exitCode"])


if __name__ == "__main__":
    unittest.main()
