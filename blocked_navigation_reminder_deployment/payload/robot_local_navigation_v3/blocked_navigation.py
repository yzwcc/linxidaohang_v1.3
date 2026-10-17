from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BlockedNavigationNotice:
    stationary_sec: float
    reason: str


class BlockedNavigationDetector:
    """Detect a stationary, not-yet-arrived navigation session.

    This is intentionally a conservative odometry-side detector. It does not
    disable PNC avoidance or claim that the obstacle is definitely a person;
    it only provides a polite reminder when the robot has made no meaningful
    translational progress for a sustained interval.
    """

    def __init__(
        self,
        *,
        blocked_after_sec: float = 2.0,
        reminder_interval_sec: float = 5.0,
        moving_speed_threshold_mps: float = 0.03,
        moving_angular_speed_threshold_radps: float = 0.12,
        arrival_margin_m: float = 0.10,
    ) -> None:
        self.blocked_after_sec = max(0.5, float(blocked_after_sec))
        self.reminder_interval_sec = max(1.0, float(reminder_interval_sec))
        self.moving_speed_threshold_mps = max(0.0, float(moving_speed_threshold_mps))
        self.moving_angular_speed_threshold_radps = max(
            0.0, float(moving_angular_speed_threshold_radps)
        )
        self.arrival_margin_m = max(0.0, float(arrival_margin_m))
        self._stationary_since: float | None = None
        self._last_notice_at: float | None = None
        self._last_position: tuple[float, float] | None = None
        self._last_position_at: float | None = None

    def reset(self) -> None:
        self._stationary_since = None
        self._last_notice_at = None
        self._last_position = None
        self._last_position_at = None

    def update(
        self,
        *,
        current_pose: dict[str, Any],
        distance_to_goal: float,
        arrival_tolerance_m: float,
        linear_speed_mps: float,
        angular_speed_radps: float,
        now: float | None = None,
    ) -> BlockedNavigationNotice | None:
        now = time.monotonic() if now is None else float(now)
        position = (
            float(current_pose.get("x", 0.0)),
            float(current_pose.get("y", 0.0)),
        )
        observed_speed = 0.0
        if self._last_position is not None and self._last_position_at is not None:
            elapsed = now - self._last_position_at
            if elapsed > 1e-6:
                observed_speed = math.hypot(
                    position[0] - self._last_position[0],
                    position[1] - self._last_position[1],
                ) / elapsed
        self._last_position = position
        self._last_position_at = now

        not_at_goal = float(distance_to_goal) > max(0.0, float(arrival_tolerance_m)) + self.arrival_margin_m
        moving = (
            max(abs(float(linear_speed_mps)), observed_speed)
            > self.moving_speed_threshold_mps
            or abs(float(angular_speed_radps))
            > self.moving_angular_speed_threshold_radps
        )
        if not not_at_goal or moving:
            self._stationary_since = None
            self._last_notice_at = None
            return None

        if self._stationary_since is None:
            self._stationary_since = now
            return None

        stationary_sec = now - self._stationary_since
        if stationary_sec < self.blocked_after_sec:
            return None
        if self._last_notice_at is None and stationary_sec < self.reminder_interval_sec:
            return None
        if (
            self._last_notice_at is not None
            and now - self._last_notice_at < self.reminder_interval_sec
        ):
            return None

        self._last_notice_at = now
        return BlockedNavigationNotice(
            stationary_sec=stationary_sec,
            reason=f"no translational progress for {stationary_sec:.1f}s",
        )

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else float(now)
        stationary_sec = (
            None
            if self._stationary_since is None
            else max(0.0, now - self._stationary_since)
        )
        return {
            "active": self._stationary_since is not None,
            "stationary_sec": stationary_sec,
            "last_notice_at": self._last_notice_at,
            "reminder_interval_sec": self.reminder_interval_sec,
        }
