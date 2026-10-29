"""Private local point sockets; synthetic protocol data, no ROS or robot access."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
from mapping_points import MappingPointChannel


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0, "ordinary-user Linux required")
class MappingPoints(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lx-points-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        os.chmod(self.directory, 0o700)

    def test_accepts_one_same_user_connection_and_transfers_explicit_recorded_bytes(self):
        import numpy as np
        from lingxi_slam.point_data import decode
        from lingxi_slam.point_output import PointPacket
        channel = MappingPointChannel(self.directory / "points.sock", lambda: False)
        self.addCleanup(channel.close)
        errors = []

        def produce():
            try:
                output = channel.output("a" * 32, "recorded")
                try:
                    output(PointPacket(1, 1000., 2, np.array([[1., 2., 3.], [2., 3., 4.]]), np.eye(4)))
                    # close() intentionally discards pending visual records.
                    with output.writer.condition:
                        if not output.writer.condition.wait_for(lambda: output.writer.sent == 1, 2):
                            raise RuntimeError("fixture_point_delivery_deadline")
                finally:
                    output.close()
            except BaseException as error:
                errors.append(error)

        with socket.socket(socket.AF_UNIX) as consumer:
            self.assertEqual(0o600, channel.endpoint.stat().st_mode & 0o777)
            consumer.settimeout(3)
            consumer.connect(str(channel.endpoint))
            producer = threading.Thread(target=produce)
            producer.start()
            payload = bytearray()
            while True:
                data = consumer.recv(65536)
                if not data:
                    break
                payload.extend(data)
            producer.join(3)
        self.assertFalse(producer.is_alive())
        self.assertEqual([], errors)
        record = decode(bytes(payload), "a" * 32, "recorded")
        self.assertEqual(2, len(record.packet.points))
        self.assertFalse(channel.endpoint.exists())
        with self.assertRaises(RuntimeError):
            channel.output("a" * 32, "recorded")
        channel.close()

    def test_rejects_shared_existing_and_linked_endpoints_and_honors_cancellation(self):
        endpoint = self.directory / "points.sock"
        os.chmod(self.directory, 0o755)
        with self.assertRaises(ValueError):
            MappingPointChannel(endpoint, lambda: False)
        os.chmod(self.directory, 0o700)
        endpoint.write_bytes(b"synthetic")
        with self.assertRaises(ValueError):
            MappingPointChannel(endpoint, lambda: False)
        endpoint.unlink()
        endpoint.symlink_to(self.directory / "missing")
        with self.assertRaises(ValueError):
            MappingPointChannel(endpoint, lambda: False)
        endpoint.unlink()
        channel = MappingPointChannel(endpoint, lambda: True)
        self.addCleanup(channel.close)
        with self.assertRaises(RuntimeError):
            channel.output("a" * 32, "recorded")
        channel.close()
        self.assertFalse(endpoint.exists())


if __name__ == "__main__":
    unittest.main()
