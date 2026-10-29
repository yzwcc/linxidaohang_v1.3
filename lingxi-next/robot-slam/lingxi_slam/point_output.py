"""Same-owner copies of pinned GLIM corrected points; not a map or base pose."""
import ctypes
from dataclasses import dataclass
import math
import threading

import numpy as np


@dataclass(frozen=True)
class PointPacket:
    native_frame_id: int
    algorithm_stamp: float
    native_point_count: int
    points: np.ndarray
    world_lidar_pose: np.ndarray
    coordinate_frame: str = "lingxi_glim_odom"
    source_frame: str = "IMU"
    kind: str = "native_deskewed_corrected_odometry"


class NativePointOutput:
    def __init__(self, library, maximum_points=8000):
        if type(maximum_points) is not int or not 1 <= maximum_points <= 8000:
            raise ValueError("bounded_integer_point_capacity_required")
        self.owner, self.closed = threading.get_ident(), False
        self.maximum_points = maximum_points
        self.library = ctypes.CDLL(str(library))
        pointer = ctypes.POINTER(ctypes.c_double)
        self.library.lingxi_points_error.argtypes = []
        self.library.lingxi_points_error.restype = ctypes.c_char_p
        self.library.lingxi_points_open.argtypes = [ctypes.c_int]
        self.library.lingxi_points_take.argtypes = [ctypes.c_int, pointer, pointer, pointer]
        self.library.lingxi_points_counts.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
        self.library.lingxi_points_close.argtypes = []
        for name in ("open", "take", "counts", "close"):
            getattr(self.library, "lingxi_points_" + name).restype = ctypes.c_int
        self.check(self.library.lingxi_points_open(maximum_points))
        self.previous_id = 0
        self.previous_stamp = None

    def check(self, status):
        if status < 0:
            raise RuntimeError(self.library.lingxi_points_error().decode("utf-8", errors="replace"))
        return status

    def active(self):
        if self.closed or threading.get_ident() != self.owner:
            raise RuntimeError("active_same_thread_point_output_required")

    @staticmethod
    def pointer(array):
        return array.ctypes.data_as(ctypes.POINTER(ctypes.c_double))

    def take(self, stamp, expected_lidar_pose):
        self.active()
        expected = None if expected_lidar_pose is None else np.asarray(expected_lidar_pose)
        if (not math.isfinite(stamp) or expected is not None
                and (expected.shape != (4, 4) or not np.isfinite(expected).all())):
            raise ValueError("finite_stamp_and_optional_lidar_pose_required")
        xyz = np.empty((self.maximum_points, 3), dtype=np.float64)
        pose, metadata = np.empty((4, 4), dtype=np.float64), np.empty(4, dtype=np.float64)
        count = self.check(self.library.lingxi_points_take(
            self.maximum_points, self.pointer(xyz), self.pointer(pose), self.pointer(metadata)))
        if count == 0:
            return None
        if (not 1 <= count <= self.maximum_points or not np.isfinite(metadata).all()
                or metadata[0] != self.previous_id + 1 or metadata[1] != stamp
                or self.previous_stamp is not None and stamp <= self.previous_stamp
                or not math.isfinite(stamp) or metadata[2] != int(metadata[2])
                or not count <= metadata[2] <= 2_000_000 or metadata[3] != count
                or expected is None or not np.isfinite(pose).all()
                or not np.isfinite(xyz[:count]).all()
                or not np.allclose(pose, expected, atol=1e-12, rtol=0)
                or not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-7, rtol=0)
                or not np.allclose(pose[:3, :3].T @ pose[:3, :3], np.eye(3), atol=1e-5, rtol=0)
                or not np.isclose(np.linalg.det(pose[:3, :3]), 1, atol=1e-5, rtol=0)):
            raise ValueError("matching_complete_native_point_packet_required")
        self.previous_id, self.previous_stamp = int(metadata[0]), stamp
        xyz = xyz[:count].copy()
        xyz.flags.writeable = pose.flags.writeable = False
        return PointPacket(self.previous_id, stamp, int(metadata[2]), xyz, pose)

    def counts(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("same_thread_point_counters_required")
        values = (ctypes.c_uint64 * 5)()
        self.check(self.library.lingxi_points_counts(values))
        return dict(zip(("callbacks", "initializationSkips", "copies", "takes", "discardedOnClose"), values))

    def close(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("same_thread_point_close_required")
        if not self.closed:
            self.active()
            self.check(self.library.lingxi_points_close())
            self.closed = True


class PointOutputNative:
    """Decorate the existing engine without changing its input or return contract."""

    def __init__(self, factory, output, on_points):
        if not callable(factory) or not callable(on_points):
            raise ValueError("native_factory_and_point_consumer_required")
        output.active()
        self.owner = threading.get_ident()
        self.output, self.on_points, self.closed = output, on_points, False
        self.initialized, self.failed = False, False
        try:
            self.native = factory()
        except BaseException:
            output.close()
            raise

    def imu(self, *args):
        self.active()
        try:
            return self.native.imu(*args)
        except BaseException:
            self.failed = True
            raise

    def active(self):
        if self.closed or self.failed or threading.get_ident() != self.owner:
            raise RuntimeError("active_same_thread_point_native_required")
        self.output.active()

    def frame(self, stamp, *args):
        self.active()
        try:
            result = self.native.frame(stamp, *args)
            packet = self.output.take(stamp, result[0])
            has_pose = result[0] is not None
            if ((self.initialized and (not has_pose or packet is None))
                    or not self.initialized and packet is not None):
                raise RuntimeError("complete_post_initialization_point_output_required")
            self.initialized = self.initialized or has_pose
            if packet is not None:
                self.on_points(packet)
            return result
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("same_thread_point_native_close_required")
        if not self.closed:
            self.closed = True
            try:
                return self.native.close()
            finally:
                self.output.close()
