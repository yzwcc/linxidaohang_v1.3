from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from nav_msgs.msg import Odometry

from robot_local_navigation_v3.arrival_hysteresis import (
    effective_arrival_tolerance,
    is_arrival_sample_stable,
)
from robot_local_navigation_v3.models import ArrivalPolicyConfig, NavPoint


@dataclass(frozen=True)
class ArrivalSample:
    current_pose: dict[str, Any]
    distance_to_goal: float
    yaw_error: float | None
    linear_speed_mps: float
    in_region: bool
    completed: bool


class ArrivalPolicy:
    """Stateful odometry-based arrival policy for a single navigation goal."""

    def __init__(self, config: ArrivalPolicyConfig, goal: NavPoint):
        self.config = config
        self.goal = goal
        self._stable_since: float | None = None
        self._completed = False

    def reset(self) -> None:
        self._stable_since = None
        self._completed = False

    def update(self, msg: Odometry) -> ArrivalSample:
        pose = msg.pose.pose
        x = float(pose.position.x)
        y = float(pose.position.y)
        distance = math.hypot(self.goal.pose.position.x - x, self.goal.pose.position.y - y)
        yaw_error = self._yaw_error(msg)
        linear_speed_mps = self._linear_speed(msg)
        in_region = distance <= self.position_tolerance

        stability_candidate = is_arrival_sample_stable(
            in_region,
            yaw_error,
            self.config.yaw_tolerance_rad,
            linear_speed_mps,
            require_low_speed=self.config.require_low_speed,
            low_speed_threshold_mps=self.config.low_speed_threshold_mps,
        )
        if stability_candidate:
            if self._stable_since is None:
                self._stable_since = time.monotonic()
        else:
            self._stable_since = None

        completed = self._is_completed()
        if completed:
            self._completed = True

        return ArrivalSample(
            current_pose={
                "x": x,
                "y": y,
                "z": float(pose.position.z),
                "qx": float(pose.orientation.x),
                "qy": float(pose.orientation.y),
                "qz": float(pose.orientation.z),
                "qw": float(pose.orientation.w),
            },
            distance_to_goal=distance,
            yaw_error=yaw_error,
            linear_speed_mps=linear_speed_mps,
            in_region=in_region,
            completed=self._completed,
        )

    @property
    def position_tolerance(self) -> float:
        return effective_arrival_tolerance(
            float(self.config.position_tolerance_m),
            float(self.goal.navigation.target_pose_radius),
        )

    def _is_completed(self) -> bool:
        if self._completed:
            return True
        if self._stable_since is None:
            return False
        if (time.monotonic() - self._stable_since) < self.config.stable_duration_sec:
            return False
        return True

    def _yaw_error(self, msg: Odometry) -> float | None:
        if self.config.yaw_tolerance_rad is None:
            return None
        current_yaw = _yaw_from_quaternion(
            x=float(msg.pose.pose.orientation.x),
            y=float(msg.pose.pose.orientation.y),
            z=float(msg.pose.pose.orientation.z),
            w=float(msg.pose.pose.orientation.w),
        )
        return _normalize_angle(self.goal.yaw - current_yaw)

    def _linear_speed(self, msg: Odometry) -> float:
        linear = msg.twist.twist.linear
        return math.hypot(float(linear.x), float(linear.y))


def _yaw_from_quaternion(*, x: float, y: float, z: float, w: float) -> float:
    siny = 2.0 * (w * z + x * y)
    cosy = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny, cosy)


def _normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle
