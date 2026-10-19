"""Conservative completion for a robot that has stopped just outside the goal."""
from __future__ import annotations

import math
import os


class NearGoalArrival:
    RADIUS_M = 0.45
    DWELL_SEC = 5.0
    MAX_SPEED_MPS = 0.05
    MAX_ANGULAR_SPEED_RADPS = 0.08
    MAX_DRIFT_M = 0.04
    MAX_SAMPLE_GAP_SEC = 0.5
    MAX_STAMP_AGE_SEC = 1.0

    def __init__(self):
        self.enabled = os.environ.get("NAV_NEAR_GOAL_ENABLED", "1").strip().lower() in {
            "1", "true", "yes",
        }
        self.reset()

    def reset(self):
        self.since = None
        self.last_time = None
        self.last_stamp = None
        self.anchor = None
        self.samples = 0

    def update(self, msg, sample, *, now, ros_now_sec, yaw_tolerance, stop_enabled):
        try:
            pose, twist = msg.pose.pose, msg.twist.twist
            stamp = float(msg.header.stamp.sec) + float(msg.header.stamp.nanosec) / 1e9
            values = (
                pose.position.x, pose.position.y, pose.position.z,
                pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w,
                twist.linear.x, twist.linear.y, twist.linear.z,
                twist.angular.x, twist.angular.y, twist.angular.z,
                sample.distance_to_goal, sample.linear_speed_mps, now, ros_now_sec, stamp,
            )
            norm = sum(float(v) ** 2 for v in (
                pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w,
            ))
            yaw = abs(float(sample.yaw_error))
            valid = (
                self.enabled and stop_enabled
                and all(math.isfinite(float(v)) for v in values)
                and 0.81 <= norm <= 1.21
                and stamp > 0 and -0.1 <= ros_now_sec - stamp <= self.MAX_STAMP_AGE_SEC
                and 0 <= sample.distance_to_goal <= self.RADIUS_M
                and yaw <= min(0.25, float(yaw_tolerance) if yaw_tolerance is not None else 0.25)
                and 0 <= sample.linear_speed_mps <= self.MAX_SPEED_MPS
                and math.hypot(float(twist.linear.x), float(twist.linear.y)) <= self.MAX_SPEED_MPS
                and abs(float(twist.angular.z)) <= self.MAX_ANGULAR_SPEED_RADPS
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            self.reset()
            return False
        xy = (float(pose.position.x), float(pose.position.y))
        continuous = (
            self.last_time is not None
            and 0 < now - self.last_time <= self.MAX_SAMPLE_GAP_SEC
            and stamp > self.last_stamp
            and stamp - self.last_stamp <= self.MAX_SAMPLE_GAP_SEC
            and math.hypot(xy[0] - self.anchor[0], xy[1] - self.anchor[1]) <= self.MAX_DRIFT_M
        )
        if not continuous:
            self.since, self.anchor, self.samples = now, xy, 0
        self.last_time, self.last_stamp = now, stamp
        self.samples += 1
        return now - self.since >= self.DWELL_SEC and self.samples >= 11
