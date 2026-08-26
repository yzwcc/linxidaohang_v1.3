from robot_local_navigation_v3.motion_option_policy import (
    ArrivalMotionTracker,
    SPECIAL_MOTION_ALIASES,
    SPECIAL_MOTION_ALLOWED_AREAS,
    is_empty_motion,
    parse_numeric_area_id,
)


def test_random_speech_motion_id_and_domain_boundaries():
    motion_id = SPECIAL_MOTION_ALIASES["random_speech"]
    assert motion_id == 6001
    assert 21 in SPECIAL_MOTION_ALLOWED_AREAS[motion_id]
    assert 48 in SPECIAL_MOTION_ALLOWED_AREAS[motion_id]
    assert 20 not in SPECIAL_MOTION_ALLOWED_AREAS[motion_id]
    assert 49 not in SPECIAL_MOTION_ALLOWED_AREAS[motion_id]


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
    assert tracker.claim_stop()
    assert not tracker.claim_stop()


def test_empty_motion_does_not_arm_tracker_but_force_clears_legacy_motion():
    tracker = ArrivalMotionTracker()
    assert not tracker.mark_started(0)
    assert not tracker.claim_stop()
    assert tracker.claim_stop(force=True)
    assert parse_numeric_area_id("right_hand") is None


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项特殊动作规则测试")
