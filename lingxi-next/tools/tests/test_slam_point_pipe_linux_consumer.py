"""Optional real Linux Node process, private socket and isolated synthetic data.

Set explicit LINGXI_NODE_LINUX_BIN and LINGXI_POINT_CONSUMER_BUNDLE to run.
No native SLAM, recorded robot data, ROS, TCP listener or robot connection.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import run_glim_point_pipe_trial as trial

NODE, CONSUMER = (os.environ.get(name) for name in (
    "LINGXI_NODE_LINUX_BIN", "LINGXI_POINT_CONSUMER_BUNDLE"))


@unittest.skipUnless(sys.platform == "linux" and NODE and CONSUMER,
                     "Explicit Linux Node binary and compiled consumer required")
class RealConsumerTests(unittest.TestCase):
    def run_consumer(self, send, *, success=True):
        fixture = json.loads((ROOT / "shared/fixtures/slam-point-v1.synthetic.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="lingxi-point-synthetic-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            endpoint = root / "points.sock"
            command = [NODE]
            probe = os.environ.get("LINGXI_POINT_LIFECYCLE_PROBE")
            if probe:
                command.extend(("--import", probe))
            process = subprocess.Popen(
                [*command, CONSUMER, str(root), str(endpoint), fixture["session"]],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
            try:
                trial.wait_ready(process, 15)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.connect(str(endpoint))
                    send(connection, fixture)
                try:
                    code = process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    trial.stop_consumer(process)
                    details = process.stderr.read().decode("utf-8", errors="replace")[-12000:]
                    self.fail("isolated consumer did not exit: " + details)
                if code != (0 if success else 1):
                    self.fail("isolated consumer failed: " + process.stderr.read().decode("utf-8", errors="replace"))
                if not success:
                    self.assertFalse((root / "consumer-report.json").exists())
                    return None
                report = json.loads((root / "consumer-report.json").read_text(encoding="utf-8"))
                return report
            finally:
                trial.stop_consumer(process)
                process.stderr.close()

    def test_real_process_exits_after_paired_api_and_unix_pipe_cleanup(self):
        def send(connection, fixture):
            payload = bytearray.fromhex(fixture["hex"])
            payload[10] = 2  # Recorded protocol in an explicitly synthetic isolated test.
            struct.pack_into("<d", payload, 40, time.monotonic())
            connection.sendall(payload)
        report = self.run_consumer(send)
        self.assertEqual(1, report["points"]["records"])
        self.assertTrue(report["api"]["recordedInputRejectedAsLive"])
        self.assertTrue(report["api"]["readinessUnchanged"])

    def test_empty_input_fails_without_success_report_or_forced_exit(self):
        self.run_consumer(lambda connection, fixture: None, success=False)

    def test_truncated_record_fails_and_closes_the_owned_api(self):
        def send(connection, fixture):
            payload = bytearray.fromhex(fixture["hex"])
            payload[10] = 2
            struct.pack_into("<d", payload, 40, time.monotonic())
            connection.sendall(payload[:-1])
        self.run_consumer(send, success=False)

    def test_expired_record_is_not_relabelled_as_complete(self):
        def send(connection, fixture):
            payload = bytearray.fromhex(fixture["hex"])
            payload[10] = 2
            struct.pack_into("<d", payload, 40, time.monotonic() - 5.)
            connection.sendall(payload)
        self.run_consumer(send, success=False)


if __name__ == "__main__":
    unittest.main()
