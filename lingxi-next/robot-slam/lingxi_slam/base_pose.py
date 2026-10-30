"""Exact-time LiDAR-to-base projection; no TF publication or navigation approval."""
from dataclasses import dataclass
import math

import numpy as np
from scipy.spatial.transform import Rotation

from .seeded_localization import rigid_pose


LIDAR_FRAME = "lidar_chest_front"
BASE_FRAME = "base_link"
HYPOTHESES = ("header_is_scan_start", "header_is_scan_end")


def timestamp(value):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValueError("positive_integer_sensor_nanoseconds_required")
    return value


@dataclass(frozen=True)
class ScanTime:
    """Original integer time, not an epoch reconstructed from a native double.

    The two hypotheses retain the existing input formulas. Neither this object
    nor matching the formula validates the hardware's scan-time semantics.
    """

    header_ns: int
    maximum_offset_ns: int
    first_imu_ns: int
    hypothesis: str

    def __post_init__(self):
        timestamp(self.header_ns)
        timestamp(self.first_imu_ns)
        if (type(self.maximum_offset_ns) is not int
                or not 0 < self.maximum_offset_ns <= 200_000_000
                or self.hypothesis not in HYPOTHESES):
            raise ValueError("explicit_bounded_scan_time_hypothesis_required")
        timestamp(self.effective_ns)

    @property
    def effective_ns(self):
        return self.header_ns - (self.maximum_offset_ns if self.hypothesis == HYPOTHESES[1] else 0)

    @property
    def algorithm_stamp(self):
        value = (self.header_ns - self.first_imu_ns) / 1e9 + 1000.
        return value - self.maximum_offset_ns * 1e-9 if self.hypothesis == HYPOTHESES[1] else value


@dataclass(frozen=True)
class BaseLidarTransform:
    stamp_ns: int
    base_from_lidar: np.ndarray
    parent: str = BASE_FRAME
    child: str = LIDAR_FRAME

    def __post_init__(self):
        timestamp(self.stamp_ns)
        if self.parent != BASE_FRAME or self.child != LIDAR_FRAME:
            raise ValueError("explicit_base_from_chest_lidar_transform_required")
        value = rigid_pose(self.base_from_lidar)
        value.flags.writeable = False
        object.__setattr__(self, "base_from_lidar", value)


class BasePoseUnavailable(ValueError):
    """A missing exact-time transform never produces a latest/identity fallback."""


class BasePoseProjector:
    """Worker-side composition with an injected, exact-time transform provider.

    A caller must bind the provider, pose and timing to the same input session
    and profile, and enforce pose freshness/generation before publication.
    This stateless component neither caches poses nor certifies that binding.
    """

    def __init__(self, lookup, *, metres_per_map_unit):
        if (not callable(lookup) or type(metres_per_map_unit) not in (int, float)
                or not math.isfinite(metres_per_map_unit) or metres_per_map_unit <= 0):
            raise ValueError("exact_time_lookup_and_explicit_positive_unit_scale_required")
        self.lookup, self.scale = lookup, metres_per_map_unit

    def project(self, world_from_lidar, timing, *, algorithm_stamp):
        if (not isinstance(timing, ScanTime) or type(algorithm_stamp) not in (int, float)
                or not math.isfinite(algorithm_stamp) or algorithm_stamp <= 0
                or algorithm_stamp != timing.algorithm_stamp):
            raise ValueError("same_original_scan_and_native_algorithm_time_required")
        pose = rigid_pose(world_from_lidar)
        pose[:3, 3] *= self.scale
        if not np.isfinite(pose).all():
            raise ValueError("finite_scaled_lidar_pose_required")
        transform = self.lookup(timing.effective_ns)
        if transform is None:
            raise BasePoseUnavailable("timestamped_base_lidar_tf_unavailable")
        if (not isinstance(transform, BaseLidarTransform)
                or transform.stamp_ns != timing.effective_ns):
            raise BasePoseUnavailable("matching_timestamped_base_lidar_tf_required")
        # TF carries metres. Scale the map translation once, not the TF lever arm.
        base_from_lidar = transform.base_from_lidar
        lidar_from_base = np.eye(4)
        lidar_from_base[:3, :3] = base_from_lidar[:3, :3].T
        lidar_from_base[:3, 3] = -base_from_lidar[:3, :3].T @ base_from_lidar[:3, 3]
        result = rigid_pose(pose @ lidar_from_base)
        result.flags.writeable = False
        return result


class Tf2BaseLookup:
    """Read an existing tf2_ros.Buffer at an explicit ROS timestamp, without waiting.

    The caller owns its bounded Buffer, subscriptions, executor and shutdown.
    ``coverage`` must verify that every dynamic edge brackets the query within
    the session's declared maximum interpolation gap. tf2's returned timestamp
    alone does not establish that sample-density condition.
    Constructing this adapter never starts a ROS node, subscriber or publisher.
    """

    def __init__(self, buffer, *, coverage):
        if not callable(getattr(buffer, "lookup_transform", None)) or not callable(coverage):
            raise ValueError("existing_tf2_buffer_and_timed_coverage_verifier_required")
        self.buffer, self.coverage = buffer, coverage

    def __call__(self, stamp_ns):
        timestamp(stamp_ns)
        if self.coverage(stamp_ns) is not True:
            return None
        from rclpy.clock import ClockType
        from rclpy.duration import Duration
        from rclpy.time import Time
        from tf2_ros import TransformException

        try:
            value = self.buffer.lookup_transform(
                BASE_FRAME, LIDAR_FRAME,
                Time(nanoseconds=stamp_ns, clock_type=ClockType.ROS_TIME),
                timeout=Duration(nanoseconds=0))
        except TransformException:
            return None
        stamp = value.header.stamp
        if (type(stamp.sec) is not int or type(stamp.nanosec) is not int
                or stamp.sec < 0 or not 0 <= stamp.nanosec < 10**9
                or stamp.sec * 10**9 + stamp.nanosec != stamp_ns
                or value.header.frame_id != BASE_FRAME or value.child_frame_id != LIDAR_FRAME):
            raise BasePoseUnavailable("matching_tf2_frames_and_query_stamp_required")
        transform = value.transform
        q = np.array([getattr(transform.rotation, key) for key in ("x", "y", "z", "w")])
        t = np.array([getattr(transform.translation, key) for key in ("x", "y", "z")])
        if (q.dtype.kind not in "fi" or t.dtype.kind not in "fi"
                or not np.isfinite(q).all() or not np.isfinite(t).all()
                or not np.isclose(np.linalg.norm(q), 1., atol=1e-6, rtol=0)):
            raise BasePoseUnavailable("finite_unit_quaternion_tf2_transform_required")
        matrix = np.eye(4)
        matrix[:3, :3] = Rotation.from_quat(q).as_matrix()
        matrix[:3, 3] = t
        return BaseLidarTransform(stamp_ns, matrix)
