from __future__ import annotations

import math

ARRIVAL_HYSTERESIS_MIN_M = 0.35
ARRIVAL_HYSTERESIS_MULTIPLIER = 1.25
MIN_EFFECTIVE_ARRIVAL_TOLERANCE_M = 0.20
POST_ENTRY_HYSTERESIS_MIN_M = 0.75
POST_ENTRY_HYSTERESIS_MULTIPLIER = 3.0
POST_ENTRY_HYSTERESIS_MAX_M = 1.0
POST_ENTRY_HYSTERESIS_MAX_AGE_SEC = 30.0
POST_ENTRY_LOW_SPEED_THRESHOLD_MPS = 0.08
TIMEOUT_RECOVERY_MIN_RADIUS_M = 0.45
TIMEOUT_RECOVERY_MAX_RADIUS_M = 0.75
TIMEOUT_RECOVERY_MAX_SPEED_MPS = 0.15
TIMEOUT_RECOVERY_MAX_YAW_ERROR_RAD = math.radians(35.0)
TIMEOUT_RECOVERY_MIN_STABLE_SEC = 2.0


def effective_arrival_tolerance(configured_tolerance: float, point_tolerance: float) -> float:
    """返回适合实体机器人停车精度的业务到达半径。"""

    configured = max(0.0, float(configured_tolerance))
    point = max(0.0, float(point_tolerance))
    return max(MIN_EFFECTIVE_ARRIVAL_TOLERANCE_M, min(configured, point))


def arrival_hysteresis_threshold(
    position_tolerance: float,
    *,
    strict_region_entered: bool = False,
) -> float:
    """返回普通停车回弹圈或严格进圈后的转向漂移回弹圈。"""

    tolerance = max(0.0, float(position_tolerance))
    if strict_region_entered:
        return min(
            POST_ENTRY_HYSTERESIS_MAX_M,
            max(
                POST_ENTRY_HYSTERESIS_MIN_M,
                tolerance * POST_ENTRY_HYSTERESIS_MULTIPLIER,
            ),
        )
    return max(
        ARRIVAL_HYSTERESIS_MIN_M,
        tolerance * ARRIVAL_HYSTERESIS_MULTIPLIER,
    )


def is_within_arrival_hysteresis(
    distance_to_goal: float,
    position_tolerance: float,
    *,
    strict_region_entered: bool = False,
) -> bool:
    """允许停车回弹；只有严格进圈后才放宽原地转向产生的定位漂移。"""

    threshold = arrival_hysteresis_threshold(
        position_tolerance,
        strict_region_entered=strict_region_entered,
    )
    return max(0.0, float(distance_to_goal)) <= threshold


def is_arrival_stability_candidate(
    in_strict_region: bool,
    strict_region_entered: bool,
    distance_to_goal: float,
    position_tolerance: float,
    allow_initial_hysteresis: bool = False,
) -> bool:
    """严格到达过后，允许机器人重新进入制动回弹圈并重新稳定计时。"""

    if in_strict_region:
        return True
    if strict_region_entered:
        return is_within_arrival_hysteresis(
            distance_to_goal,
            position_tolerance,
            strict_region_entered=True,
        )
    if allow_initial_hysteresis:
        return is_within_arrival_hysteresis(distance_to_goal, position_tolerance)
    return False


def is_post_entry_arrival_latch_active(
    strict_region_entered: bool,
    strict_region_age_sec: float | None,
) -> bool:
    """严格进圈证据只在有限时间内允许扩大回弹半径。"""

    if not strict_region_entered or strict_region_age_sec is None:
        return False
    age_sec = float(strict_region_age_sec)
    return 0.0 <= age_sec <= POST_ENTRY_HYSTERESIS_MAX_AGE_SEC


def is_timeout_recovery_candidate(
    strict_region_entered: bool,
    distance_to_goal: float,
    position_tolerance: float,
    yaw_error: float | None,
    linear_speed_mps: float | None,
    *,
    require_strict_region_entry: bool = True,
) -> bool:
    """Allow bounded timeout recovery after a verified near-goal stop.

    Normal navigation requires prior strict-region entry. Tour tasks may opt in
    to the bounded near-goal mode because the physical controller can stop
    outside the configured radius while remaining aligned and stationary.
    """

    if require_strict_region_entry and not strict_region_entered:
        return False
    if linear_speed_mps is None:
        return False
    recovery_radius = min(
        TIMEOUT_RECOVERY_MAX_RADIUS_M,
        max(TIMEOUT_RECOVERY_MIN_RADIUS_M, float(position_tolerance) * 2.25),
    )
    if max(0.0, float(distance_to_goal)) > recovery_radius:
        return False
    if abs(float(linear_speed_mps)) > TIMEOUT_RECOVERY_MAX_SPEED_MPS:
        return False
    return yaw_error is None or abs(float(yaw_error)) <= TIMEOUT_RECOVERY_MAX_YAW_ERROR_RAD


def is_arrival_sample_stable(
    position_candidate: bool,
    yaw_error: float | None,
    yaw_tolerance: float | None,
    linear_speed_mps: float | None,
    *,
    require_low_speed: bool,
    low_speed_threshold_mps: float,
) -> bool:
    """位置、朝向与速度必须在同一采样上同时满足。"""

    if not position_candidate or not is_yaw_within_tolerance(yaw_error, yaw_tolerance):
        return False
    if not require_low_speed:
        return True
    if linear_speed_mps is None:
        return False
    return abs(float(linear_speed_mps)) <= max(0.0, float(low_speed_threshold_mps))


def is_yaw_within_tolerance(
    yaw_error: float | None,
    yaw_tolerance: float | None,
) -> bool:
    """朝向检查未启用时放行，启用后必须有有效角度且处于容差内。"""

    if yaw_tolerance is None:
        return True
    if yaw_error is None:
        return False
    return abs(float(yaw_error)) <= max(0.0, float(yaw_tolerance))
