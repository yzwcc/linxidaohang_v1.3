"""Real local POSIX pipes with explicitly isolated synthetic payloads only."""
import json
import os
from pathlib import Path
import select
import sys
import time
import unittest

try:
    import numpy as np
except ImportError:
    np = None

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.point_pipe import LatestPointPipeWriter, MAXIMUM_BYTES

FIXTURE = json.loads((ROOT / "shared/fixtures/slam-point-v1.synthetic.json").read_text(encoding="utf-8"))
PAYLOAD = bytes.fromhex(FIXTURE["hex"])


@unittest.skipUnless(sys.platform == "linux", "Dedicated point pipe runtime is Linux-only")
class PipeWriterTests(unittest.TestCase):
    def setUp(self):
        self.read, self.write = os.pipe()
        self.writer = LatestPointPipeWriter(self.write)
        self.addCleanup(self.writer.close)
        self.addCleanup(lambda: os.close(self.read) if self.read is not None else None)

    def wait(self, predicate):
        end = time.monotonic() + 2
        while not predicate() and time.monotonic() < end:
            time.sleep(.005)
        self.assertTrue(predicate())

    def receive(self, size):
        result = bytearray()
        end = time.monotonic() + 2
        while len(result) < size and time.monotonic() < end:
            if select.select([self.read], [], [], .05)[0]:
                block = os.read(self.read, size - len(result))
                if not block:
                    break
                result.extend(block)
        self.assertEqual(size, len(result))
        return bytes(result)

    def test_complete_record_arrives_without_modification(self):
        self.writer.offer(PAYLOAD)
        self.assertEqual(PAYLOAD, self.receive(len(PAYLOAD)))
        self.wait(lambda: self.writer.sent == 1)

    def test_partial_large_record_is_not_interleaved_with_latest(self):
        payload = PAYLOAD + bytes(MAXIMUM_BYTES - len(PAYLOAD))
        self.writer.offer(payload)
        self.wait(lambda: self.writer.pending is None)
        for value in range(100):
            self.writer.offer(bytes([value]) * len(PAYLOAD))
        self.assertEqual(payload, self.receive(len(payload)))
        self.assertEqual(bytes([99]) * len(PAYLOAD), self.receive(len(PAYLOAD)))
        self.wait(lambda: self.writer.sent == 2)
        self.assertEqual(99, self.writer.replaced)

    def test_close_of_blocked_pipe_is_bounded_and_idempotent(self):
        self.writer.offer(bytes(MAXIMUM_BYTES))
        self.wait(lambda: self.writer.pending is None)
        start = time.monotonic()
        self.writer.close()
        self.writer.close()
        self.assertLess(time.monotonic() - start, 1)
        self.assertFalse(self.writer.thread.is_alive())
        with self.assertRaises(RuntimeError):
            self.writer.offer(PAYLOAD)

    def test_broken_reader_latches_failure_and_clears_pending(self):
        os.close(self.read)
        self.read = None
        self.writer.offer(PAYLOAD)
        self.wait(lambda: self.writer.failed)
        self.assertIsNone(self.writer.pending)
        with self.assertRaises(RuntimeError):
            self.writer.offer(PAYLOAD)

    def test_bounds_and_dedicated_fd_contract(self):
        for payload in (b"", bytes(215), bytes(MAXIMUM_BYTES + 1), bytearray(PAYLOAD)):
            with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                self.writer.offer(payload)
        for fd in (-1, 0, 1, 2, True):
            with self.subTest(fd=fd), self.assertRaises(ValueError):
                LatestPointPipeWriter(fd)

    @unittest.skipIf(np is None, "NumPy native-owner composition runs in isolated Linux")
    def test_native_owner_callback_encodes_real_pipe_bytes_with_explicit_session(self):
        from lingxi_slam.point_data import PointPipeOutput, decode
        from lingxi_slam.point_output import PointPacket
        self.writer.close()
        read, write = os.pipe()
        self.addCleanup(lambda: os.close(read))
        output = PointPipeOutput(FIXTURE["session"], "synthetic", write, clock=lambda: 10)
        self.addCleanup(output.close)
        packet = PointPacket(1, FIXTURE["algorithmStamp"], 3,
                             np.array(FIXTURE["points"]).reshape(-1, 3),
                             np.array(FIXTURE["worldLidarPose"], dtype=float).reshape(4, 4))
        output(packet)
        self.assertTrue(select.select([read], [], [], 1)[0])
        payload = os.read(read, len(PAYLOAD))
        self.assertEqual(PAYLOAD, payload)
        result = decode(payload, FIXTURE["session"], "synthetic", now=10)
        np.testing.assert_array_equal(packet.points, result.packet.points)


if __name__ == "__main__":
    unittest.main()
