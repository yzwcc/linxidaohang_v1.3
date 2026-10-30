"""Bounded original scan metadata and dynamic X2 TF coverage; no ROS startup."""
from bisect import bisect_left, insort
from collections import OrderedDict
import threading

import numpy as np
from scipy.spatial.transform import Rotation

from .base_pose import BasePoseUnavailable, ScanTime, timestamp
from .messages import SensorProfile, cloud_snapshot, sensor_stamp

MAXIMUM_TF_GAP_NS = 50_000_000
CHAIN = ("base_link", "pelvis", "waist_yaw_link", "waist_pitch_link", "torso_link", "lidar_chest_front")
DYNAMIC_CHILDREN = frozenset(CHAIN[2:5])


class ScanTimeIndex:
    def __init__(self, profile, hypothesis, *, maximum_frames=64, maximum_bytes=8 * 1024 * 1024):
        if (not isinstance(profile, SensorProfile)
                or type(maximum_frames) is not int or not 1 <= maximum_frames <= 1024
                or type(maximum_bytes) is not int or not 1 <= maximum_bytes <= 64 * 1024 * 1024
                or hypothesis not in ("header_is_scan_start", "header_is_scan_end")):
            raise ValueError("bounded_original_scan_time_index_required")
        self.profile, self.hypothesis = profile, hypothesis
        self.maximum_frames, self.maximum_bytes = maximum_frames, maximum_bytes
        self.lock, self.frames = threading.Lock(), OrderedDict()
        self.first_imu, self.closed, self.failed = None, False, False

    def observe(self, kind, message):
        with self.lock:
            if self.closed or self.failed:
                return
        if kind == "imu":
            stamp = sensor_stamp(message, self.profile.imu_frame)
            with self.lock:
                if not self.closed and not self.failed and self.first_imu is None:
                    self.first_imu = stamp
            return
        if kind != "cloud":
            raise ValueError("explicit_sensor_kind_required")
        packet = cloud_snapshot(message, self.profile, 2_000_000, self.maximum_bytes)
        order = ">" if packet.big_endian else "<"
        offsets = np.ndarray((packet.height, packet.width), dtype=order + "f8", buffer=packet.data,
                             offset=packet.offsets[3], strides=(packet.row_step, packet.point_step))
        maximum = float(offsets.max())
        if (not np.isfinite(offsets).all() or offsets.min() < 0 or maximum > 200_000_000
                or np.ptp(offsets) <= 0 or abs(maximum - round(maximum)) > 1e-4):
            raise ValueError("integer_relative_point_nanoseconds_required")
        with self.lock:
            if self.closed or self.failed:
                return
            if packet.stamp_ns in self.frames:
                raise ValueError("unique_pending_scan_header_required")
            self.frames[packet.stamp_ns] = round(maximum)
            while len(self.frames) > self.maximum_frames:
                self.frames.popitem(last=False)

    def consume(self, header_ns, algorithm_stamp):
        with self.lock:
            offset = self.frames.pop(header_ns, None)
            if self.closed or self.failed or self.first_imu is None or offset is None:
                raise BasePoseUnavailable("original_scan_time_unavailable")
            timing = ScanTime(header_ns, offset, self.first_imu, self.hypothesis)
        if timing.algorithm_stamp != algorithm_stamp:
            raise BasePoseUnavailable("original_and_native_scan_time_mismatch")
        return timing

    def invalidate(self):
        with self.lock:
            self.failed = True
            self.frames.clear()

    def close(self):
        with self.lock:
            self.closed = True
            self.frames.clear()


class TfCoverage:
    """Only the fixed, audited X2 chain; tf2 performs actual interpolation."""

    def __init__(self, *, maximum_samples=256):
        if type(maximum_samples) is not int or not 2 <= maximum_samples <= 4096:
            raise ValueError("bounded_tf_coverage_history_required")
        self.maximum_samples = maximum_samples
        self.lock = threading.Lock()
        self.parents = dict(zip(CHAIN[1:], CHAIN))
        self.static, self.dynamic, self.ordered = {}, {}, {}
        self.closed = self.failed = False

    def observe(self, message, *, static, apply=lambda: None):
        child, parent = message.child_frame_id, message.header.frame_id
        if child not in self.parents:
            return False
        stamp = message.header.stamp
        if (type(static) is not bool or parent != self.parents[child]
                or static == (child in DYNAMIC_CHILDREN)
                or type(stamp.sec) is not int or type(stamp.nanosec) is not int
                or stamp.sec < 0 or not 0 <= stamp.nanosec < 10**9):
            raise ValueError("fixed_x2_tf_parent_type_and_time_required")
        stamp_ns = stamp.sec * 10**9 + stamp.nanosec
        if not static:
            timestamp(stamp_ns)
        transform = message.transform
        q = np.array([getattr(transform.rotation, key) for key in ("x", "y", "z", "w")])
        t = np.array([getattr(transform.translation, key) for key in ("x", "y", "z")])
        if (q.dtype.kind not in "fi" or t.dtype.kind not in "fi"
                or not np.isfinite(q).all() or not np.isfinite(t).all()
                or not np.isclose(np.linalg.norm(q), 1., atol=1e-6, rtol=0)):
            raise ValueError("finite_unit_quaternion_kinematic_tf_required")
        value = np.eye(4)
        value[:3, :3], value[:3, 3] = Rotation.from_quat(q).as_matrix(), t
        with self.lock:
            if self.closed or self.failed:
                return False
            if static:
                previous = self.static.get(child)
                if previous is not None and not np.allclose(previous, value, atol=1e-12, rtol=0):
                    raise ValueError("unchanged_static_kinematic_tf_required")
                apply()
                self.static[child] = value
            else:
                samples = self.dynamic.setdefault(child, {})
                ordered = self.ordered.setdefault(child, [])
                if stamp_ns in samples:
                    if not np.allclose(samples[stamp_ns], value, atol=1e-12, rtol=0):
                        raise ValueError("consistent_tf_at_duplicate_timestamp_required")
                    return False
                if len(ordered) >= self.maximum_samples and stamp_ns < ordered[0]:
                    return False
                apply()
                samples[stamp_ns] = value
                insort(ordered, stamp_ns)
                if len(ordered) > self.maximum_samples:
                    del samples[ordered.pop(0)]
            return True

    def covers(self, stamp_ns):
        timestamp(stamp_ns)
        with self.lock:
            if self.closed or self.failed or set(self.static) != set(self.parents) - DYNAMIC_CHILDREN:
                return False
            for child in DYNAMIC_CHILDREN:
                stamps = self.ordered.get(child, [])
                index = bisect_left(stamps, stamp_ns)
                if index < len(stamps) and stamps[index] == stamp_ns:
                    continue
                if (index == 0 or index == len(stamps)
                        or stamps[index] - stamps[index - 1] > MAXIMUM_TF_GAP_NS):
                    return False
            return True

    def invalidate(self):
        with self.lock:
            self.failed = True
            self.static.clear()
            self.dynamic.clear()
            self.ordered.clear()

    def close(self):
        self.invalidate()
        with self.lock:
            self.closed = True
