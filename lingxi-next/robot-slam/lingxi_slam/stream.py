"""Single-owner, bounded arrival-order input for an injected LiDAR/IMU engine."""
from collections import deque
from dataclasses import asdict, dataclass
import math
import threading

import numpy as np


class StreamError(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    maximum_pending_imu: int = 4096
    maximum_pending_clouds: int = 32
    maximum_pending_bytes: int = 64 * 1024 * 1024
    maximum_points_per_cloud: int = 2_000_000
    maximum_imu_gap_ns: int = 50_000_000
    maximum_cloud_gap_ns: int = 500_000_000
    initial_wait_seconds: float = 2.0
    maximum_wait_seconds: float = 0.75
    maximum_sensor_silence_seconds: float = 0.75

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name.endswith("_seconds"):
                valid = type(value) in (int, float) and math.isfinite(value) and value > 0
            else:
                valid = type(value) is int and value > 0
            if not valid:
                raise ValueError("explicit_finite_positive_stream_limits_required")
        if self.maximum_points_per_cloud < 10:
            raise ValueError("at_least_ten_points_per_cloud_required")


@dataclass(frozen=True)
class Scan:
    header_ns: int
    points: np.ndarray
    offsets: np.ndarray
    received_at: float
    size: int


class SensorStream:
    """Caller serializes callbacks and ticks a monotonic watchdog, outside ROS."""

    def __init__(self, native, hypothesis, *, limits=None, on_frame=None):
        if hypothesis not in ("header_is_scan_start", "header_is_scan_end"):
            raise ValueError("explicit_scan_time_hypothesis_required")
        self.native, self.hypothesis = native, hypothesis
        self.limits = Limits() if limits is None else limits
        if not isinstance(self.limits, Limits):
            raise ValueError("validated_stream_limits_required")
        self.on_frame = on_frame
        self.owner = threading.get_ident()
        self.state, self.failure = "running", None
        self.imu, self.clouds = deque(), deque()
        self.origin_ns = self.last_imu_ns = self.last_cloud_ns = None
        self.latest_imu_time = self.inserted_imu_time = None
        self.last_received = self.first_received = None
        self.last_imu_received = self.last_cloud_received = None
        self.last_processed_time = None
        self.pending_bytes = 0
        self.counts = dict.fromkeys((
            "receivedImu", "receivedClouds", "insertedImu", "processedScans",
            "initializationScans", "estimatedPoses", "skippedBeforeImu",
            "skippedAfterImuAtEndOfInput", "discardedOnFailure",
            "maximumPendingImu", "maximumPendingClouds", "maximumPendingBytes",
        ), 0)
        self.maximum_wait = 0.

    def _active(self):
        if threading.get_ident() != self.owner:
            raise StreamError("single_owner_thread_required")
        if self.state != "running":
            raise StreamError("stream_not_running")

    def _fail(self, reason):
        self.state, self.failure = "failed", reason
        self.counts["discardedOnFailure"] += len(self.clouds)
        self.clouds.clear()
        self.imu.clear()
        self.pending_bytes = 0
        raise StreamError(reason)

    def _advance(self, received_at):
        self._active()
        if (type(received_at) not in (int, float) or not math.isfinite(received_at)
                or (self.last_received is not None and received_at < self.last_received)):
            self._fail("monotonic_arrival_clock_required")
        self.last_received = received_at
        if self.first_received is None:
            self.first_received = received_at
        for previous in (self.last_imu_received, self.last_cloud_received):
            limit = (self.limits.initial_wait_seconds if previous is None
                     else self.limits.maximum_sensor_silence_seconds)
            elapsed = received_at - (self.first_received if previous is None else previous)
            if elapsed > limit:
                self._fail("sensor_arrival_deadline_exceeded")
        if self.clouds:
            limit = (self.limits.initial_wait_seconds if self.origin_ns is None
                     else self.limits.maximum_wait_seconds)
            if received_at - self.clouds[0].received_at > limit:
                self._fail("scan_wait_deadline_exceeded")

    def _stamp(self, stamp, previous, maximum_gap):
        if (type(stamp) is not int or stamp < 0 or
                (previous is not None and (stamp <= previous or stamp - previous > maximum_gap))):
            self._fail("ordered_continuous_integer_sensor_time_required")

    def _measure_buffers(self):
        for name, value in (("maximumPendingImu", len(self.imu)),
                            ("maximumPendingClouds", len(self.clouds)),
                            ("maximumPendingBytes", self.pending_bytes)):
            self.counts[name] = max(self.counts[name], value)

    def push_imu(self, stamp_ns, acceleration, angular, received_at):
        self._advance(received_at)
        self._stamp(stamp_ns, self.last_imu_ns, self.limits.maximum_imu_gap_ns)
        try:
            acceleration, angular = (np.asarray(value, dtype=np.float64) for value in (acceleration, angular))
        except (TypeError, ValueError, OverflowError):
            self._fail("finite_three_axis_imu_required")
        if any(value.shape != (3,) or not np.isfinite(value).all() for value in (acceleration, angular)):
            self._fail("finite_three_axis_imu_required")
        if len(self.imu) >= self.limits.maximum_pending_imu:
            self._fail("imu_buffer_capacity_exceeded")
        if self.origin_ns is None:
            self.origin_ns = stamp_ns
        relative = (stamp_ns - self.origin_ns) / 1e9 + 1000.
        self.imu.append((relative, acceleration.copy(), angular.copy()))
        self.last_imu_ns, self.latest_imu_time = stamp_ns, relative
        self.last_imu_received = received_at
        self.counts["receivedImu"] += 1
        self._measure_buffers()
        self._drain(received_at)

    def push_cloud(self, header_ns, points, offsets, received_at):
        self._advance(received_at)
        self._stamp(header_ns, self.last_cloud_ns, self.limits.maximum_cloud_gap_ns)
        try:
            points, offsets = (np.asarray(value, dtype=np.float64) for value in (points, offsets))
        except (TypeError, ValueError, OverflowError):
            self._fail("finite_complete_cloud_with_explicit_second_offsets_required")
        if (points.ndim != 2 or points.shape[1] != 3 or not 10 <= len(points) <= self.limits.maximum_points_per_cloud
                or offsets.shape != (len(points),) or not np.isfinite(points).all()
                or not np.isfinite(offsets).all() or offsets.min() < 0
                or offsets.max() > .2 or np.ptp(offsets) <= 0):
            self._fail("finite_complete_cloud_with_explicit_second_offsets_required")
        size = len(points) * 4 * np.dtype(np.float64).itemsize
        if (len(self.clouds) >= self.limits.maximum_pending_clouds
                or self.pending_bytes + size > self.limits.maximum_pending_bytes):
            self._fail("cloud_buffer_capacity_exceeded")
        points, offsets = (np.array(value, dtype=np.float64, copy=True, order="C") for value in (points, offsets))
        points.flags.writeable = offsets.flags.writeable = False
        self.clouds.append(Scan(header_ns, points, offsets, received_at, size))
        self.pending_bytes += size
        self.last_cloud_ns = header_ns
        self.last_cloud_received = received_at
        self.counts["receivedClouds"] += 1
        self._measure_buffers()
        self._drain(received_at)

    def _pop_cloud(self):
        scan = self.clouds.popleft()
        self.pending_bytes -= scan.size
        return scan

    def _drain(self, now):
        while self.clouds and self.origin_ns is not None:
            scan = self.clouds[0]
            stamp = (scan.header_ns - self.origin_ns) / 1e9 + 1000.
            if self.hypothesis == "header_is_scan_end":
                stamp -= float(scan.offsets.max())
            end = stamp + float(scan.offsets.max())
            if stamp < 1000.:
                self._pop_cloud()
                self.counts["skippedBeforeImu"] += 1
                continue
            if self.latest_imu_time < end:
                return
            if self.last_processed_time is not None and stamp <= self.last_processed_time:
                self._fail("algorithm_scan_time_must_increase")
            try:
                # Retain future IMU samples: only insert through the next scan's end.
                while self.inserted_imu_time is None or self.inserted_imu_time < end:
                    imu_stamp, acceleration, angular = self.imu.popleft()
                    self.native.imu(imu_stamp, acceleration, angular)
                    self.inserted_imu_time = imu_stamp
                    self.counts["insertedImu"] += 1
                pose, statistics = self.native.frame(stamp, scan.points, scan.offsets)
                statistics = np.asarray(statistics, dtype=np.float64)
                if (statistics.shape != (4,) or not np.isfinite(statistics).all() or np.any(statistics < 0)
                        or np.any(statistics[2:] != np.floor(statistics[2:]))):
                    raise ValueError("invalid_native_frame_statistics")
                if pose is not None:
                    pose = np.asarray(pose, dtype=np.float64)
                    if (pose.shape != (4, 4) or not np.isfinite(pose).all()
                            or not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-7, rtol=0)
                            or not np.allclose(pose[:3, :3].T @ pose[:3, :3], np.eye(3), atol=1e-5, rtol=0)
                            or not np.isclose(np.linalg.det(pose[:3, :3]), 1, atol=1e-5, rtol=0)):
                        raise ValueError("invalid_native_pose")
                self._pop_cloud()
                self.last_processed_time = stamp
                self.counts["processedScans"] += 1
                self.counts["initializationScans" if pose is None else "estimatedPoses"] += 1
                self.maximum_wait = max(self.maximum_wait, now - scan.received_at)
                if self.on_frame:
                    self.on_frame(scan.header_ns, stamp, pose, statistics.copy())
            except Exception as error:
                try:
                    self._fail("native_or_result_consumer_failed")
                except StreamError as stopped:
                    raise stopped from error

    def tick(self, monotonic_now):
        """Live owner must call periodically even when sensors stop producing."""
        self._advance(monotonic_now)
        self._drain(monotonic_now)

    def finish(self):
        """Explicit EOF/stop, not evidence that a map is saved or navigation-ready."""
        self._active()
        self._drain(self.last_received if self.last_received is not None else 0.)
        self.counts["skippedAfterImuAtEndOfInput"] += len(self.clouds)
        self.clouds.clear()
        self.imu.clear()
        self.pending_bytes = 0
        self.state = "finished"
        return self.snapshot()

    def snapshot(self):
        self._active_owner_only()
        return {
            "state": self.state, "failure": self.failure, "counts": self.counts.copy(),
            "pendingImu": len(self.imu), "pendingClouds": len(self.clouds),
            "pendingCloudBytes": self.pending_bytes, "maximumObservedScanWaitSeconds": self.maximum_wait,
            "limits": asdict(self.limits),
        }

    def _active_owner_only(self):
        if threading.get_ident() != self.owner:
            raise StreamError("single_owner_thread_required")
