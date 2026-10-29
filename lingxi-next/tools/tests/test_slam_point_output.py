"""Isolated synthetic contracts, never robot sensors or motion."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ImportError:
    np = None

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
if np is not None:
    from lingxi_slam.point_output import NativePointOutput, PointOutputNative


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class Library:
    def __init__(self):
        self.calls, self.packet = [], None
        self.lingxi_points_error = Function(lambda: b"synthetic_failure")
        self.lingxi_points_open = Function(lambda maximum: self.record("open", 0))
        self.lingxi_points_take = Function(self.take)
        self.lingxi_points_counts = Function(self.counts)
        self.lingxi_points_close = Function(lambda: self.record("close", 0))

    def record(self, name, result):
        self.calls.append(name)
        return result

    def take(self, capacity, xyz, pose, metadata):
        self.calls.append("take")
        if self.packet is None:
            return 0
        points, transform, fields = self.packet
        for pointer, values in ((xyz, points), (pose, transform), (metadata, fields)):
            for index, value in enumerate(np.asarray(values).ravel()):
                pointer[index] = value
        return len(points)

    def counts(self, pointer):
        for index, value in enumerate((3, 1, 2, 2, 0)):
            pointer[index] = value
        return 0

    def valid_packet(self, stamp=1000.2, frame_id=1):
        self.packet = (np.array([[1., 2., 3.], [4., 5., 6.]]),
                       np.eye(4), np.array([frame_id, stamp, 3, 2.]))


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
        raise AssertionError("synthetic thread did not terminate")
    return errors


@unittest.skipIf(np is None, "NumPy contracts run in the isolated Linux environment")
class NativePointTests(unittest.TestCase):
    def setUp(self):
        self.library = Library()
        self.loader = patch("lingxi_slam.point_output.ctypes.CDLL", return_value=self.library)
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.output = NativePointOutput("synthetic.so", 2)
        self.addCleanup(self.output.close)

    def test_copy_is_complete_readonly_and_owns_storage(self):
        self.library.valid_packet()
        packet = self.output.take(1000.2, np.eye(4))
        self.assertEqual(1, packet.native_frame_id)
        self.assertEqual(3, packet.native_point_count)
        self.assertEqual("IMU", packet.source_frame)
        self.assertEqual("lingxi_glim_odom", packet.coordinate_frame)
        self.assertFalse(packet.points.flags.writeable)
        self.assertFalse(packet.world_lidar_pose.flags.writeable)
        self.library.packet[0][:] = 999
        np.testing.assert_array_equal(packet.points, [[1, 2, 3], [4, 5, 6]])
        self.assertEqual(2, self.output.counts()["copies"])

    def test_no_callback_returns_no_packet(self):
        self.assertIsNone(self.output.take(1000.1, None))

    def test_metadata_and_geometry_errors_are_not_accepted(self):
        mutations = (
            lambda p: p[2].__setitem__(0, 2),
            lambda p: p[2].__setitem__(1, 1000.3),
            lambda p: p[2].__setitem__(2, 2.5),
            lambda p: p[2].__setitem__(2, 2_000_001),
            lambda p: p[2].__setitem__(3, 1),
            lambda p: p[2].__setitem__(0, np.nan),
            lambda p: p[0].__setitem__((0, 0), np.nan),
            lambda p: p[1].__setitem__((0, 3), 1),
            lambda p: p[1].__setitem__((3, 0), .01),
            lambda p: p[1].__setitem__((0, 0), -1),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.library.valid_packet()
                mutate(self.library.packet)
                with self.assertRaisesRegex(ValueError, "matching_complete"):
                    self.output.take(1000.2, np.eye(4))

    def test_missing_expected_pose_and_malformed_pose_rejected(self):
        self.library.valid_packet()
        for pose in (None, np.eye(3), np.full((4, 4), np.nan)):
            with self.subTest(pose=pose), self.assertRaises(ValueError):
                self.output.take(1000.2, pose)

    def test_frame_id_and_stamp_must_both_advance(self):
        self.library.valid_packet()
        self.output.take(1000.2, np.eye(4))
        for stamp, frame_id in ((1000.2, 2), (1000.1, 2), (1000.3, 1), (1000.3, 3)):
            with self.subTest(stamp=stamp, frame_id=frame_id):
                self.library.valid_packet(stamp, frame_id)
                with self.assertRaises(ValueError):
                    self.output.take(stamp, np.eye(4))

    def test_native_error_propagates(self):
        self.library.lingxi_points_take = Function(lambda *args: -1)
        with self.assertRaisesRegex(RuntimeError, "synthetic_failure"):
            self.output.take(1000.2, np.eye(4))

    def test_wrong_thread_never_enters_native_api(self):
        for function in (lambda: self.output.take(1., None), self.output.counts, self.output.close):
            self.assertIsInstance(other_thread(function)[0], RuntimeError)
        self.assertEqual(["open"], self.library.calls)

    def test_closed_output_cannot_take_and_close_is_idempotent(self):
        self.output.close()
        self.output.close()
        with self.assertRaises(RuntimeError):
            self.output.take(1., None)
        self.assertEqual(["open", "close"], self.library.calls)

    def test_capacity_validation_precedes_loading(self):
        for value in (True, 0, -1, 8001, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                NativePointOutput("unused", value)


class Engine:
    def __init__(self):
        self.calls, self.pose = [], None

    def imu(self, *args):
        self.calls.append("imu")

    def frame(self, *args):
        self.calls.append("frame")
        return self.pose, np.array([1., 2., 3., 4.])

    def close(self):
        self.calls.append("close")
        return 5


@unittest.skipIf(np is None, "NumPy contracts run in the isolated Linux environment")
class WrapperTests(unittest.TestCase):
    def setUp(self):
        NativePointTests.setUp(self)
        self.engine, self.packets = Engine(), []
        self.wrapper = PointOutputNative(lambda: self.engine, self.output, self.packets.append)
        self.addCleanup(self.wrapper.close)

    def initialize(self):
        self.assertIsNone(self.wrapper.frame(1000.)[0])
        self.engine.pose = np.eye(4)
        self.assertIsNotNone(self.wrapper.frame(1000.1)[0])

    def test_initialization_then_exactly_one_packet_per_pose(self):
        self.initialize()
        self.library.valid_packet()
        pose, statistics = self.wrapper.frame(1000.2)
        self.assertIs(self.engine.pose, pose)
        np.testing.assert_array_equal(statistics, [1, 2, 3, 4])
        self.assertEqual(1, len(self.packets))

    def test_missing_packet_after_first_pose_fails_and_cannot_continue(self):
        self.initialize()
        with self.assertRaisesRegex(RuntimeError, "complete_post_initialization"):
            self.wrapper.frame(1000.2)
        before = list(self.engine.calls)
        with self.assertRaises(RuntimeError):
            self.wrapper.imu(1, 2, 3)
        self.assertEqual(before, self.engine.calls)

    def test_packet_at_first_pose_is_not_mislabeled_deskewed(self):
        self.engine.pose = np.eye(4)
        self.library.valid_packet()
        with self.assertRaisesRegex(RuntimeError, "complete_post_initialization"):
            self.wrapper.frame(1000.2)

    def test_pose_cannot_disappear_after_initialization(self):
        self.initialize()
        self.engine.pose = None
        with self.assertRaisesRegex(RuntimeError, "complete_post_initialization"):
            self.wrapper.frame(1000.2)

    def test_wrapper_owner_checked_before_all_native_calls(self):
        for function in (lambda: self.wrapper.imu(1), lambda: self.wrapper.frame(1), self.wrapper.close):
            self.assertIsInstance(other_thread(function)[0], RuntimeError)
        self.assertEqual([], self.engine.calls)

    def test_factory_never_called_from_non_owner(self):
        calls = []
        errors = other_thread(lambda: PointOutputNative(lambda: calls.append(1), self.output, lambda p: None))
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertEqual([], calls)

    def test_failed_factory_closes_output(self):
        def factory():
            raise RuntimeError("synthetic_factory_failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic_factory"):
            PointOutputNative(factory, self.output, lambda p: None)
        self.assertTrue(self.output.closed)

    def test_native_close_failure_still_closes_output(self):
        def close():
            raise RuntimeError("synthetic_close_failure")
        self.engine.close = close
        with self.assertRaisesRegex(RuntimeError, "synthetic_close"):
            self.wrapper.close()
        self.assertTrue(self.output.closed)
        self.wrapper.close()


if __name__ == "__main__":
    unittest.main()
