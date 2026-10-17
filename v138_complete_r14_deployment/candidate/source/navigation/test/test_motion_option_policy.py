from robot_local_navigation_v3.motion_option_policy import (
    ArrivalMotionTracker,
    SPECIAL_MOTION_ALIASES,
    SPECIAL_MOTION_ALLOWED_AREAS,
    is_empty_motion,
    is_request_acknowledged,
    motion_requires_explicit_stop,
    parse_numeric_area_id,
)


def test_random_speech_motion_id_and_domain_boundaries():
    motion_id = SPECIAL_MOTION_ALIASES["random_speech"]
    assert motion_id == 6001
    assert 21 in SPECIAL_MOTION_ALLOWED_AREAS[motion_id]
    assert 48 in SPECIAL_MOTION_ALLOWED_AREAS[motion_id]
    assert 20 not in SPECIAL_MOTION_ALLOWED_AREAS[motion_id]
    assert 49 not in SPECIAL_MOTION_ALLOWED_AREAS[motion_id]
    assert motion_requires_explicit_stop(motion_id)
    assert not motion_requires_explicit_stop(1002)


def test_empty_motion_uses_zero_and_interrupts_only_without_animation_path():
    motion_id = SPECIAL_MOTION_ALIASES["empty_motion"]
    assert motion_id == 0
    assert SPECIAL_MOTION_ALLOWED_AREAS[motion_id] == {0}
    assert is_empty_motion(motion_id)
    assert not is_empty_motion(motion_id, "/tmp/custom_animation")


def test_numeric_string_domains_are_parsed_for_sdk_requests():
    assert parse_numeric_area_id("21") == 21
    assert parse_numeric_area_id("48") == 48


def test_arrival_motion_tracker_stops_each_started_motion_once():
    tracker = ArrivalMotionTracker()
    assert tracker.mark_started(6001)
    assert tracker.snapshot() == {
        "active": True,
        "motion_id": 6001,
        "stop_required": True,
        "completion": "running",
    }
    assert tracker.claim_stop()
    assert not tracker.claim_stop()
    assert tracker.snapshot()["completion"] == "interrupted"


def test_arrival_motion_tracker_natural_completion_never_requests_empty_motion():
    tracker = ArrivalMotionTracker()
    assert tracker.mark_started(1002)
    assert tracker.completion_action_after_wait() == "natural"
    assert not tracker.claim_stop()
    assert tracker.snapshot() == {
        "active": False,
        "motion_id": None,
        "stop_required": False,
        "completion": "natural",
    }


def test_continuous_speech_motion_requests_one_explicit_stop_after_wait():
    tracker = ArrivalMotionTracker()
    assert tracker.mark_started(6001)
    assert tracker.completion_action_after_wait() == "interrupt"
    assert tracker.active
    assert tracker.claim_stop()
    assert tracker.completion_action_after_wait() == "idle"
    assert not tracker.claim_stop()


def test_empty_motion_does_not_arm_tracker_but_force_clears_legacy_motion():
    tracker = ArrivalMotionTracker()
    assert not tracker.mark_started(0)
    assert not tracker.claim_stop()
    assert tracker.claim_stop(force=True)
    assert tracker.snapshot()["stop_required"] is False
    assert parse_numeric_area_id("right_hand") is None


def test_documented_nonzero_header_code_cannot_be_overridden_by_running_state():
    accepted_states = (1, 200, 300, 400)
    assert is_request_acknowledged(0, 400, accepted_states)
    assert not is_request_acknowledged(17, 400, accepted_states)
    assert is_request_acknowledged(None, 400, accepted_states)


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项特殊动作规则测试")
