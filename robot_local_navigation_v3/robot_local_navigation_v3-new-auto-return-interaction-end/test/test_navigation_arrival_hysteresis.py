from robot_local_navigation_v3.arrival_hysteresis import (
    effective_arrival_tolerance,
    is_arrival_stability_candidate,
    is_within_arrival_hysteresis,
    is_yaw_within_tolerance,
)


def test_uses_observed_robot_parking_tolerance():
    assert effective_arrival_tolerance(0.15, 0.15) == 0.20


def test_preserves_larger_pair_tolerance():
    assert effective_arrival_tolerance(0.8, 0.7) == 0.7


def test_accepts_small_post_arrival_drift():
    assert is_within_arrival_hysteresis(0.225, 0.15)


def test_accepts_hysteresis_boundary():
    assert is_within_arrival_hysteresis(0.35, 0.15)


def test_rejects_robot_that_moved_away_from_goal():
    assert not is_within_arrival_hysteresis(0.351, 0.15)


def test_does_not_start_fallback_before_entering_strict_region():
    assert not is_arrival_stability_candidate(False, False, 0.25, 0.15)


def test_resume_accepts_robot_already_parked_in_braking_hysteresis():
    assert is_arrival_stability_candidate(
        False,
        False,
        0.226,
        0.20,
        allow_initial_hysteresis=True,
    )


def test_resume_still_rejects_robot_outside_braking_hysteresis():
    assert not is_arrival_stability_candidate(
        False,
        False,
        0.351,
        0.20,
        allow_initial_hysteresis=True,
    )


def test_restarts_stability_after_reentering_braking_hysteresis():
    assert is_arrival_stability_candidate(False, True, 0.327, 0.15)


def test_clears_stability_after_leaving_braking_hysteresis():
    assert not is_arrival_stability_candidate(False, True, 0.351, 0.15)


def test_scales_for_larger_configured_tolerance():
    assert is_within_arrival_hysteresis(0.74, 0.6)
    assert not is_within_arrival_hysteresis(0.76, 0.6)


def test_rejects_fallback_when_heading_is_wrong():
    assert not is_yaw_within_tolerance(0.4, 0.1745)


def test_accepts_fallback_when_heading_is_within_tolerance():
    assert is_yaw_within_tolerance(0.1, 0.1745)


def test_requires_heading_sample_when_heading_check_is_enabled():
    assert not is_yaw_within_tolerance(None, 0.1745)


def test_allows_missing_heading_when_heading_check_is_disabled():
    assert is_yaw_within_tolerance(None, None)


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项导航到达滞回测试")
