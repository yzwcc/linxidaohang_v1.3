"""Owned immediate rays, with synthetic native peers and no robot IO."""
import importlib.util
from pathlib import Path
import struct
import sys
import threading
import unittest
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "tools")]
if AVAILABLE:
    import numpy as np
    from scipy.spatial.transform import Rotation
    from lingxi_slam.acquisition_output import MAX_BYTES, NativeAcquisitionOutput, decode_acquisition
    from lingxi_slam.point_output import PointOutputNative


def pose(x=0, yaw=0):
    value = np.eye(4)
    value[0, 3] = x
    value[:3, :3] = Rotation.from_euler("z", yaw, degrees=True).as_matrix()
    return value


def packet_bytes(frame_id=1, stamp=10., corrected=None, extrinsic=None):
    matrix = lambda value: np.asarray(value, dtype="<f8").tobytes()
    trajectory = [[stamp, 0, 0, 0, 0, 0, 0, 1],
                  [stamp + 1, 2, 0, 0, *Rotation.from_euler("z", 90, degrees=True).as_quat()]]
    returns = [[0, 1, 0, -.2], [.5, 1, 0, .5], [1, 1, 0, 2]]
    return (b"LXOBS002" + struct.pack("<4Q", 1, 1, 3, 1) + struct.pack("<Q", 0)
            + matrix(pose()) + struct.pack("<Q", 1) + struct.pack("<QddQQQ", frame_id, stamp, stamp + 1, 3, 2, 2)
            + matrix(pose()) + matrix(pose() if extrinsic is None else extrinsic)
            + matrix(pose() if corrected is None else corrected) + matrix(trajectory) + matrix(returns))


def changed(data, offset, format, value):
    result = bytearray(data)
    struct.pack_into(format, result, offset, value)
    return bytes(result)


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class Library:
    def __init__(self):
        self.calls, self.data = [], None
        self.metadata = (1, 3, 2, 840)
        self.after_query = lambda: None
        self.lingxi_acquisition_error = Function(lambda: b"synthetic_acquisition_failure")
        self.lingxi_acquisition_open = Function(lambda: self.record("open", 0))
        self.lingxi_acquisition_close = Function(lambda: self.record("close", 0))
        self.lingxi_acquisition_take = Function(self.take)
        self.lingxi_acquisition_counts = Function(self.counts)

    def record(self, name, value):
        self.calls.append(name)
        return value

    def take(self, capacity, destination, metadata):
        self.calls.append(("take", capacity))
        if self.data is None:
            return 0
        for index, value in enumerate(self.metadata):
            metadata[index] = value
        if destination is None:
            self.after_query()
        else:
            if capacity != len(self.data):
                raise AssertionError("bounded complete synthetic destination required")
            for index, value in enumerate(self.data):
                destination[index] = value
            self.data = None
        return 1

    def counts(self, destination):
        for index, value in enumerate((2, 1, 1, 1, 0)):
            destination[index] = value
        return 0


def other_thread(function):
    errors = []

    def run():
        try:
            function()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=run)
    thread.start()
    thread.join(2)
    if thread.is_alive():
        raise AssertionError("synthetic acquisition thread did not terminate")
    return errors


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python runtime")
class DecoderTests(unittest.TestCase):
    def decode(self, data=None, metadata=(1, 3, 2, 840), stamp=10., expected=None):
        return decode_acquisition(packet_bytes() if data is None else data, metadata, stamp,
                                  pose() if expected is None else expected)

    def test_per_return_origins_move_and_rotation_is_interpolated(self):
        packet = self.decode()
        np.testing.assert_allclose(packet.origins, [[0, 0, 0], [1, 0, 0], [2, 0, 0]], atol=1e-12)
        np.testing.assert_allclose(packet.endpoints,
            [[1, 0, -.2], [1 + 2**-.5, 2**-.5, .5], [2, 1, 2]], atol=1e-12)
        self.assertEqual("lingxi_glim_odom", packet.coordinate_frame)
        self.assertEqual("native_per_return_original_lio_acquisition", packet.kind)
        self.assertEqual(64, len(packet.source_sha256))
        for value in (packet.origins, packet.endpoints, packet.world_lidar_pose):
            self.assertFalse(value.flags.writeable)
        self.assertFalse(hasattr(packet, "received_at"))

    def test_nonzero_extrinsic_and_correction_transform_both_ends(self):
        extrinsic, corrected = pose(.2, 30), pose(4, -40)
        packet = self.decode(packet_bytes(corrected=corrected, extrinsic=extrinsic),
                             expected=corrected @ np.linalg.inv(extrinsic))
        for index, fraction in enumerate((0, .5, 1)):
            transform = corrected @ pose(2 * fraction, 90 * fraction) @ np.linalg.inv(extrinsic)
            np.testing.assert_allclose(packet.origins[index], (transform @ [0, 0, 0, 1])[:3], atol=1e-12)
            np.testing.assert_allclose(packet.endpoints[index],
                (transform @ [1, 0, (-.2, .5, 2)[index], 1])[:3], atol=1e-12)

    def test_pose_correction_keeps_source_identity_but_changes_geometry(self):
        before = self.decode()
        after = self.decode(packet_bytes(corrected=pose(3)), expected=pose(3))
        self.assertEqual(before.source_sha256, after.source_sha256)
        np.testing.assert_allclose(after.origins - before.origins, [[3, 0, 0]] * 3, atol=1e-12)

    def test_bounded_metadata_rejected_before_archive_parse(self):
        values = ((0, 3, 2, 840), (2**53, 3, 2, 840), (1, 0, 2, 840), (1, 65537, 2, 840),
                  (1, 3, 1, 840), (1, 3, 4097, 840), (1, 3, 2, MAX_BYTES + 1),
                  (True, 3, 2, 840), (1., 3, 2, 840), (1, 3, 2))
        for metadata in values:
            with self.subTest(metadata=metadata), self.assertRaisesRegex(ValueError, "bounded_complete"):
                self.decode(metadata=metadata)

    def test_truncation_trailing_bytes_and_mutable_buffers_are_rejected(self):
        good = packet_bytes()
        for data in (good[:-1], good + b"extra", bytearray(good)):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                self.decode(data)

    def test_historical_or_different_frame_envelopes_are_rejected(self):
        changes = ((8, "<Q", 2), (16, "<Q", 2), (32, "<Q", 2), (40, "<Q", 1),
                   (72, "<d", 1), (176, "<Q", 2), (184, "<Q", 2), (192, "<d", 10.01),
                   (208, "<Q", 2), (216, "<Q", 3), (224, "<Q", 1))
        for offset, format, value in changes:
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "single_current"):
                self.decode(changed(packet_bytes(), offset, format, value))

    def test_time_bracketing_quaternions_and_rigid_transforms_are_required(self):
        changes = ((232, "<d", 2), (360, "<d", -1), (488, "<d", float("nan")),
                   (616, "<d", 10.1), (680, "<d", 10.), (736, "<d", 2),
                   (744, "<d", -.1), (808, "<d", 1.1), (752, "<d", float("inf")))
        for offset, format, value in changes:
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.decode(changed(packet_bytes(), offset, format, value))

    def test_wrong_stamp_or_lidar_pose_is_not_a_matching_scan(self):
        for stamp, expected in ((float("nan"), pose()), (True, pose()), (10.1, pose()),
                                (10., pose(1)), (10., np.eye(3)), (10., np.full((4, 4), np.nan))):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                self.decode(stamp=stamp, expected=expected)


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python runtime")
class NativeOutputTests(unittest.TestCase):
    def setUp(self):
        self.library = Library()
        loader = patch("lingxi_slam.acquisition_output.ctypes.CDLL", return_value=self.library)
        loader.start()
        self.addCleanup(loader.stop)
        self.output = NativeAcquisitionOutput("synthetic.so")
        self.addCleanup(self.output.close)

    def test_query_then_owned_copy_consumes_once(self):
        self.library.data = packet_bytes()
        packet = self.output.take(10., pose())
        self.assertEqual(1, packet.native_frame_id)
        self.assertEqual(["open", ("take", 0), ("take", 840)], self.library.calls)
        self.assertIsNone(self.output.take(10., None))
        self.assertEqual(1, self.output.counts()["copies"])

    def test_initialization_has_no_packet(self):
        self.assertIsNone(self.output.take(9., None))

    def test_large_or_inconsistent_metadata_never_allocates_copy_buffer(self):
        for fields in ((1, 65537, 2, MAX_BYTES), (1, 3, 4097, MAX_BYTES), (1, 3, 2, MAX_BYTES + 1),
                       (1, 3, 2, 841), (2, 3, 2, 840)):
            self.library.data, self.library.metadata = packet_bytes(), fields
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.output.take(10., pose())
        self.assertTrue(all(call == "open" or call == ("take", 0) for call in self.library.calls))

    def test_metadata_must_not_change_between_query_and_copy(self):
        self.library.data = packet_bytes()
        self.library.after_query = lambda: setattr(self.library, "metadata", (2, 3, 2, 840))
        with self.assertRaisesRegex(ValueError, "unchanged_single"):
            self.output.take(10., pose())

    def test_frame_id_and_stamp_advance_together(self):
        self.library.data = packet_bytes()
        self.output.take(10., pose())
        for frame_id, stamp in ((1, 11.), (3, 11.), (2, 10.), (2, 9.)):
            self.library.data, self.library.metadata = packet_bytes(frame_id, stamp), (frame_id, 3, 2, 840)
            with self.subTest(frame_id=frame_id, stamp=stamp), self.assertRaises(ValueError):
                self.output.take(stamp, pose())

    def test_wrong_thread_never_enters_library(self):
        for function in (lambda: self.output.take(10., pose()), self.output.counts, self.output.close):
            self.assertIsInstance(other_thread(function)[0], RuntimeError)
        self.assertEqual(["open"], self.library.calls)

    def test_close_is_idempotent_and_prevents_future_take(self):
        self.output.close()
        self.output.close()
        with self.assertRaises(RuntimeError):
            self.output.take(10., pose())
        self.assertEqual(["open", "close"], self.library.calls)

    def test_native_error_is_not_treated_as_empty_slot(self):
        self.library.lingxi_acquisition_take = Function(lambda *args: -1)
        with self.assertRaisesRegex(RuntimeError, "synthetic_acquisition_failure"):
            self.output.take(10., pose())

    def test_real_decorator_initializes_then_delivers_once(self):
        case = self
        class Engine:
            index = -1
            def frame(self, stamp):
                self.index += 1
                if self.index == 2:
                    case.library.data = packet_bytes(stamp=stamp)
                return (None if self.index == 0 else pose()), np.zeros(4)
            def close(self):
                return 0
        delivered = []
        native = PointOutputNative(Engine, self.output, delivered.append)
        self.addCleanup(native.close)
        native.frame(8.)
        native.frame(9.)
        native.frame(10.)
        self.assertEqual(1, len(delivered))
        with self.assertRaisesRegex(RuntimeError, "complete_post_initialization"):
            native.frame(11.)
        self.assertTrue(native.failed)

    def test_consumer_failure_cannot_redeliver_or_continue(self):
        class Engine:
            def frame(self, stamp):
                return pose(), np.zeros(4)
            def close(self):
                return 0
        calls = []
        def consume(packet):
            calls.append(packet)
            raise RuntimeError("synthetic_consumer_failure")
        native = PointOutputNative(Engine, self.output, consume)
        self.addCleanup(native.close)
        native.initialized = True
        self.library.data = packet_bytes()
        with self.assertRaisesRegex(RuntimeError, "synthetic_consumer_failure"):
            native.frame(10.)
        with self.assertRaises(RuntimeError):
            native.frame(10.)
        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
