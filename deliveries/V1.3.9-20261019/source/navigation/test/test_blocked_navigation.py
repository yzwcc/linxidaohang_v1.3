from __future__ import annotations

from robot_local_navigation_v3.blocked_navigation import BlockedNavigationDetector


def pose(x: float = 0.0, y: float = 0.0) -> dict[str, float]:
    return {"x": x, "y": y}


def update(detector, now, *, distance=2.0, linear=0.0, angular=0.0, x=0.0):
    return detector.update(
        current_pose=pose(x=x),
        distance_to_goal=distance,
        arrival_tolerance_m=0.25,
        linear_speed_mps=linear,
        angular_speed_radps=angular,
        now=now,
    )


def test_blocked_reminder_starts_after_five_seconds_and_repeats_every_five_seconds():
    detector = BlockedNavigationDetector(blocked_after_sec=2.0, reminder_interval_sec=5.0)

    assert update(detector, 0.0) is None
    assert update(detector, 4.9) is None
    first = update(detector, 5.0)
    assert first is not None
    assert first.stationary_sec == 5.0
    assert update(detector, 9.9) is None
    second = update(detector, 10.0)
    assert second is not None
    assert second.stationary_sec == 10.0


def test_movement_resets_reminder_and_arrival_area_is_quiet():
    detector = BlockedNavigationDetector(blocked_after_sec=2.0, reminder_interval_sec=5.0)

    update(detector, 0.0)
    assert update(detector, 5.0) is not None
    assert update(detector, 6.0, linear=0.2, x=0.2) is None
    assert update(detector, 11.0) is None
    assert update(detector, 12.0, distance=0.30) is None
    assert detector.status(now=12.0)["active"] is False


def test_rotation_is_not_reported_as_people_blocking():
    detector = BlockedNavigationDetector(blocked_after_sec=2.0, reminder_interval_sec=5.0)

    update(detector, 0.0)
    assert update(detector, 5.0, angular=0.3) is None
    assert update(detector, 9.0, angular=0.3) is None
