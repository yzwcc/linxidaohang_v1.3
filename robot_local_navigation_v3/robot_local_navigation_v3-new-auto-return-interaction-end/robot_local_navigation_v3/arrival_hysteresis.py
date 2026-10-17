from __future__ import annotations


ARRIVAL_HYSTERESIS_MIN_M = 0.35
ARRIVAL_HYSTERESIS_MULTIPLIER = 1.25
MIN_EFFECTIVE_ARRIVAL_TOLERANCE_M = 0.20


def effective_arrival_tolerance(configured_tolerance: float, point_tolerance: float) -> float:
    """返回适合实体机器人停车精度的业务到达半径。"""

    configured = max(0.0, float(configured_tolerance))
    point = max(0.0, float(point_tolerance))
    return max(MIN_EFFECTIVE_ARRIVAL_TOLERANCE_M, min(configured, point))


def is_within_arrival_hysteresis(distance_to_goal: float, position_tolerance: float) -> bool:
    """允许进入过目标区域后的轻微定位或制动回弹。"""

    threshold = max(
        ARRIVAL_HYSTERESIS_MIN_M,
        max(0.0, float(position_tolerance)) * ARRIVAL_HYSTERESIS_MULTIPLIER,
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

    return bool(in_strict_region) or (
        (bool(strict_region_entered) or bool(allow_initial_hysteresis))
        and is_within_arrival_hysteresis(distance_to_goal, position_tolerance)
    )


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
