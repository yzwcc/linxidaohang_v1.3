"""Isolated synthetic codec contracts; no native library, sensors or motion."""
from dataclasses import replace
import json
from pathlib import Path
import struct
import sys
import threading
import unittest

try:
    import numpy as np
except ImportError:
    np = None

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
if np is not None:
    from lingxi_slam.point_data import PointEncoder, decode, HEADER, MAXIMUM_BYTES
    from lingxi_slam.point_output import PointPacket

FIXTURE = json.loads((ROOT / "shared/fixtures/slam-point-v1.synthetic.json").read_text(encoding="utf-8"))
SESSION = FIXTURE["session"]


@unittest.skipIf(np is None, "NumPy contracts run in the isolated Linux environment")
class PointDataTests(unittest.TestCase):
    def setUp(self):
        self.payload = bytes.fromhex(FIXTURE["hex"])
        self.packet = PointPacket(
            1, FIXTURE["algorithmStamp"], 3, np.array(FIXTURE["points"]).reshape(-1, 3),
            np.array(FIXTURE["worldLidarPose"], dtype=np.float64).reshape(4, 4))

    def mutate(self, offset, fmt, value):
        payload = bytearray(self.payload)
        struct.pack_into("<" + fmt, payload, offset, value)
        return bytes(payload)

    def test_shared_golden_bytes_and_readonly_owned_decoding(self):
        self.assertEqual(64, HEADER.size)
        self.assertEqual(self.payload, PointEncoder(SESSION, "synthetic", clock=lambda: 10).encode(self.packet))
        result = decode(self.payload, SESSION, "synthetic", now=10)
        self.assertEqual(SESSION, result.session)
        self.assertEqual("synthetic", result.input_mode)
        self.assertEqual(10, result.emitted_at_seconds)
        np.testing.assert_array_equal(result.packet.points, self.packet.points)
        np.testing.assert_array_equal(result.packet.world_lidar_pose, self.packet.world_lidar_pose)
        self.assertFalse(result.packet.points.flags.writeable)
        self.assertFalse(result.packet.world_lidar_pose.flags.writeable)

    def test_header_lengths_counts_and_finite_values_rejected(self):
        cases = [
            (0, "B", 0xcc), (8, "H", 2), (10, "B", 1), (11, "B", 1),
            (12, "I", 0), (12, "I", 8001), (16, "I", 1), (16, "I", 2_000_001),
            (20, "I", 1), (24, "Q", 0), (24, "Q", 2**53),
            (32, "d", 0), (32, "d", float("nan")), (32, "d", 1e308),
            (40, "d", float("inf")), (40, "d", -1), (48, "B", 255),
            (64, "d", -1), (64 + 12 * 8, "d", .001), (192, "d", float("nan")),
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                decode(self.mutate(*args), SESSION, "synthetic", now=10)
        for payload in (b"", self.payload[:63], self.payload[:-1], self.payload + b"\0",
                        b"\0" * (MAXIMUM_BYTES + 1), bytearray(self.payload)):
            with self.subTest(length=len(payload)), self.assertRaises(ValueError):
                decode(payload, SESSION, "synthetic", now=10)

    def test_rigid_rotation_contract_uses_frobenius_norm(self):
        pose = np.eye(4)
        for row, column in ((0, 1), (1, 0), (0, 2), (2, 0), (1, 2), (2, 1)):
            pose[row, column] = 4e-6
        with self.assertRaises(ValueError):
            PointEncoder(SESSION, "synthetic", clock=lambda: 10).encode(
                replace(self.packet, world_lidar_pose=pose))
        angle = .37
        pose[:3, :3] = [[np.cos(angle), -np.sin(angle), 0],
                       [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
        payload = PointEncoder(SESSION, "synthetic", clock=lambda: 10).encode(
            replace(self.packet, world_lidar_pose=pose))
        decode(payload, SESSION, "synthetic", now=10)

    def test_bottom_row_tolerance_matches_receiver(self):
        for value, accepted in ((1e-7, True), (1.001e-7, False)):
            pose = self.packet.world_lidar_pose.copy()
            pose[3, 0] = value
            with self.subTest(value=value):
                encoder = PointEncoder(SESSION, "synthetic", clock=lambda: 10)
                if accepted:
                    decode(encoder.encode(replace(self.packet, world_lidar_pose=pose)),
                           SESSION, "synthetic", now=10)
                else:
                    with self.assertRaises(ValueError):
                        encoder.encode(replace(self.packet, world_lidar_pose=pose))

    def test_clock_freshness_and_explicit_context(self):
        for now in (9.94, 12.001, -1, float("inf"), float("nan"), True):
            with self.subTest(now=now), self.assertRaises(ValueError):
                decode(self.payload, SESSION, "synthetic", now=now)
        for now in (9.951, 10, 12):
            decode(self.payload, SESSION, "synthetic", now=now)
        for session, mode in ((SESSION.upper(), "synthetic"), (None, "synthetic"),
                              (SESSION, "demo"), ("0" * 32, "live")):
            with self.subTest(session=session, mode=mode), self.assertRaises(ValueError):
                decode(self.payload, session, mode, now=10)

    def test_encoder_ownership_and_order_do_not_advance_on_invalid_input(self):
        encoder = PointEncoder(SESSION, "synthetic", clock=lambda: 10)
        errors = []

        def run():
            try:
                encoder.encode(self.packet)
            except RuntimeError as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(errors))
        self.assertEqual(self.payload, encoder.encode(self.packet))
        for frame_id, stamp in ((1, 1001), (3, 1001), (2, 1000.25)):
            with self.subTest(frame=frame_id, stamp=stamp), self.assertRaises(ValueError):
                encoder.encode(replace(self.packet, native_frame_id=frame_id, algorithm_stamp=stamp))
        encoder.encode(replace(self.packet, native_frame_id=2, algorithm_stamp=1001))

    def test_encoder_requires_nondecreasing_finite_nonnegative_emission(self):
        for value in (-1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PointEncoder(SESSION, "synthetic", clock=lambda: value).encode(self.packet)
        clock = iter((10., 9., 11.))
        encoder = PointEncoder(SESSION, "synthetic", clock=lambda: next(clock))
        encoder.encode(self.packet)
        packet = replace(self.packet, native_frame_id=2, algorithm_stamp=1001)
        with self.assertRaises(ValueError):
            encoder.encode(packet)
        encoder.encode(packet)

    def test_capacity_types_and_semantics(self):
        for change in (
                {"points": np.zeros((0, 3))}, {"points": np.zeros((8001, 3))},
                {"points": np.zeros((2, 2))}, {"points": self.packet.points.astype(np.float32)},
                {"native_frame_id": True}, {"native_point_count": True},
                {"algorithm_stamp": True}, {"coordinate_frame": "map"}, {"source_frame": "base"},
                {"kind": "raw"}, {"world_lidar_pose": np.eye(3)}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                PointEncoder(SESSION, "synthetic", clock=lambda: 10).encode(replace(self.packet, **change))
        maximum = replace(self.packet, points=np.zeros((8000, 3)), native_point_count=8000)
        payload = PointEncoder(SESSION, "synthetic", clock=lambda: 10).encode(maximum)
        self.assertEqual(MAXIMUM_BYTES, len(payload))
        self.assertEqual(8000, len(decode(payload, SESSION, "synthetic", now=10).packet.points))

    def test_noncontiguous_arrays_are_encoded_in_row_major_order(self):
        xyz = np.arange(12., dtype=np.float64).reshape(4, 3)[::2]
        packet = replace(self.packet, points=xyz)
        payload = PointEncoder(SESSION, "synthetic", clock=lambda: 10).encode(packet)
        np.testing.assert_array_equal(xyz, decode(payload, SESSION, "synthetic", now=10).packet.points)


if __name__ == "__main__":
    unittest.main()
