"""Synthetic exact-time composition and tf2 adapter contracts; no ROS process."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    sys.path.insert(0, str(ROOT / "robot-slam"))
    import numpy as np
    from scipy.spatial.transform import Rotation
    from lingxi_slam.base_pose import (
        BASE_FRAME, LIDAR_FRAME, BaseLidarTransform, BasePoseProjector,
        BasePoseUnavailable, ScanTime, Tf2BaseLookup,
    )


@unittest.skipUnless(AVAILABLE, "requires NumPy and SciPy")
class BaseProjection(unittest.TestCase):
    EPOCH = 1_700_000_000_000_000_001

    def timing(self, hypothesis="header_is_scan_start"):
        return ScanTime(self.EPOCH, 94_848_123, self.EPOCH - 3_000_000_000, hypothesis)

    def pose(self, xyz=(0., 0., 0.), yaw=0.):
        value = np.eye(4)
        value[:3, :3] = Rotation.from_euler("z", yaw, degrees=True).as_matrix()
        value[:3, 3] = xyz
        return value

    def project(self, pose, timing=None, tf=None, scale=1.):
        timing = timing or self.timing()
        tf = self.pose() if tf is None else tf
        projector = BasePoseProjector(lambda stamp: BaseLidarTransform(stamp, tf), metres_per_map_unit=scale)
        return projector.project(pose, timing, algorithm_stamp=timing.algorithm_stamp)

    def test_original_integer_scan_time_is_not_rounded_through_float_epoch(self):
        first = self.timing()
        second = ScanTime(self.EPOCH + 1, first.maximum_offset_ns, first.first_imu_ns, first.hypothesis)
        self.assertEqual(1, second.effective_ns - first.effective_ns)
        end = self.timing("header_is_scan_end")
        self.assertEqual(self.EPOCH - 94_848_123, end.effective_ns)
        self.assertEqual(1003. - 94_848_123 * 1e-9, end.algorithm_stamp)

    def test_base_pose_uses_inverse_child_to_parent_tf_in_correct_order(self):
        result = self.project(self.pose((10., 20., 0.), 90), tf=self.pose((1., 0., 0.), 30))
        expected = self.pose((10., 20., 0.), 90) @ np.linalg.inv(self.pose((1., 0., 0.), 30))
        np.testing.assert_allclose(result, expected, atol=1e-12)
        self.assertFalse(result.flags.writeable)

    def test_explicit_scale_changes_map_translation_not_tf_lever_arm(self):
        pose, tf = self.pose((1000., 0., 0.)), self.pose((.25, 0., 0.))
        result = self.project(pose, tf=tf, scale=.001)
        self.assertAlmostEqual(.75, result[0, 3])
        np.testing.assert_array_equal(pose, self.pose((1000., 0., 0.)))
        np.testing.assert_array_equal(tf, self.pose((.25, 0., 0.)))

    def test_dynamic_chest_query_uses_effective_scan_time_not_latest_or_header(self):
        timing = self.timing("header_is_scan_end")
        queried = []
        def lookup(stamp):
            queried.append(stamp)
            return BaseLidarTransform(stamp, self.pose((.2, .1, 0.), 15.))
        projector = BasePoseProjector(lookup, metres_per_map_unit=1.)
        result = projector.project(np.eye(4), timing, algorithm_stamp=timing.algorithm_stamp)
        self.assertEqual([timing.effective_ns], queried)
        np.testing.assert_allclose(result, np.linalg.inv(self.pose((.2, .1, 0.), 15.)), atol=1e-12)

    def test_missing_or_wrong_time_tf_never_returns_identity_or_old_pose(self):
        timing = self.timing()
        for lookup in (lambda _: None, lambda stamp: BaseLidarTransform(stamp - 1, np.eye(4)),
                       lambda _: np.eye(4)):
            projector = BasePoseProjector(lookup, metres_per_map_unit=1.)
            with self.assertRaises(BasePoseUnavailable):
                projector.project(np.eye(4), timing, algorithm_stamp=timing.algorithm_stamp)

    def test_mismatched_algorithm_time_is_rejected_before_tf_lookup(self):
        timing, called = self.timing(), []
        projector = BasePoseProjector(lambda stamp: called.append(stamp), metres_per_map_unit=1.)
        for stamp in (timing.algorithm_stamp + 1e-9, True, float("nan"), -1.):
            with self.assertRaises(ValueError):
                projector.project(np.eye(4), timing, algorithm_stamp=stamp)
        self.assertEqual([], called)

    def test_invalid_times_hypotheses_scales_frames_and_rigid_poses_rejected(self):
        for scale in (0, -1, True, "1", float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                BasePoseProjector(lambda _: None, metres_per_map_unit=scale)
        for args in ((True, 1, self.EPOCH, "header_is_scan_start"),
                     (self.EPOCH, 0, self.EPOCH, "header_is_scan_start"),
                     (self.EPOCH, 200_000_001, self.EPOCH, "header_is_scan_start"),
                     (self.EPOCH, 1, self.EPOCH, "latest")):
            with self.assertRaises(ValueError):
                ScanTime(*args)
        for changed in ({"parent": "map"}, {"child": "IMU"}, {"stamp_ns": 0}):
            with self.assertRaises(ValueError):
                BaseLidarTransform(**{"stamp_ns": self.EPOCH, "base_from_lidar": np.eye(4), **changed})
        bad = np.eye(4)
        bad[0, 0] = 2
        with self.assertRaises(ValueError):
            self.project(bad)

    @contextmanager
    def ros(self):
        class TransformException(Exception):
            pass
        modules = {"rclpy": NS(), "rclpy.clock": NS(ClockType=NS(ROS_TIME="ros-time")),
                   "rclpy.duration": NS(Duration=lambda **values: NS(**values)),
                   "rclpy.time": NS(Time=lambda **values: NS(**values)),
                   "tf2_ros": NS(TransformException=TransformException)}
        with patch.dict(sys.modules, modules):
            yield TransformException

    def tf_message(self, stamp=None):
        stamp = self.EPOCH if stamp is None else stamp
        return NS(header=NS(frame_id=BASE_FRAME, stamp=NS(sec=stamp // 10**9, nanosec=stamp % 10**9)),
                  child_frame_id=LIDAR_FRAME,
                  transform=NS(translation=NS(x=.25, y=0., z=.7), rotation=NS(x=0., y=0., z=0., w=1.)))

    def test_tf2_adapter_requests_exact_ros_time_without_wait_or_latest(self):
        calls = []
        def lookup(*args, **kwargs):
            calls.append((args, kwargs))
            return self.tf_message()
        with self.ros():
            result = Tf2BaseLookup(NS(lookup_transform=lookup), coverage=lambda _: True)(self.EPOCH)
        args, kwargs = calls[0]
        self.assertEqual((BASE_FRAME, LIDAR_FRAME), args[:2])
        self.assertEqual(self.EPOCH, args[2].nanoseconds)
        self.assertEqual("ros-time", args[2].clock_type)
        self.assertEqual(0, kwargs["timeout"].nanoseconds)
        self.assertEqual(self.EPOCH, result.stamp_ns)
        np.testing.assert_array_equal(result.base_from_lidar[:3, 3], [.25, 0., .7])

    def test_tf2_unavailable_transform_returns_none(self):
        with self.ros() as error:
            def lookup(*args, **kwargs):
                raise error()
            self.assertIsNone(Tf2BaseLookup(NS(lookup_transform=lookup), coverage=lambda _: True)(self.EPOCH))

    def test_tf2_requires_explicit_dynamic_edge_coverage_before_any_lookup(self):
        queried, covered = [], []
        for answer in (False, None, 1):
            def coverage(stamp):
                covered.append(stamp)
                return answer
            adapter = Tf2BaseLookup(NS(lookup_transform=lambda *args, **kwargs: queried.append(args)),
                                    coverage=coverage)
            self.assertIsNone(adapter(self.EPOCH))
        self.assertEqual([], queried)
        self.assertEqual([self.EPOCH] * 3, covered)

    def test_tf2_wrong_stamp_frame_or_nonunit_quaternion_cannot_be_used(self):
        wrong_time, wrong_frame, bad_q = self.tf_message(0), self.tf_message(), self.tf_message()
        wrong_frame.child_frame_id = "IMU"
        bad_q.transform.rotation.w = 2.
        with self.ros():
            for value in (wrong_time, wrong_frame, bad_q):
                with self.subTest(value=value), self.assertRaises(BasePoseUnavailable):
                    Tf2BaseLookup(NS(lookup_transform=lambda *args, **kwargs: value),
                                  coverage=lambda _: True)(self.EPOCH)


if __name__ == "__main__":
    unittest.main()
