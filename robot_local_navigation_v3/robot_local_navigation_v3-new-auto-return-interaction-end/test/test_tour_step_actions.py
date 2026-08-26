from robot_local_navigation_v3.tour_step_actions import (
    merge_task_arrival_actions,
    normalize_tour_step,
)


POINT_ACTIONS = [
    {"type": "motion", "motion": "wave", "area": "right_hand"},
    {"type": "emoji", "emotion": "calm", "mode": "once"},
    {"type": "tts", "text": "点位默认讲解"},
]


def test_old_step_inherits_point_actions():
    actions = merge_task_arrival_actions({"point_id": "p1"}, POINT_ACTIONS)
    assert actions == POINT_ACTIONS


def test_explicit_null_disables_motion_and_emoji():
    actions = merge_task_arrival_actions(
        {"point_id": "p1", "arrival_motion": None, "arrival_emoji": None},
        POINT_ACTIONS,
    )
    assert actions == [{"type": "tts", "text": "点位默认讲解"}]


def test_step_effects_and_speech_override_point_defaults():
    actions = merge_task_arrival_actions(
        {
            "point_id": "p1",
            "speech_text": "任务专属讲解",
            "arrival_motion": {"motion": "salute", "area": "right_hand"},
            "arrival_emoji": {"emotion": "happy", "mode": "loop"},
        },
        POINT_ACTIONS,
    )
    assert {action["type"] for action in actions} == {"motion", "emoji", "tts"}
    assert next(action for action in actions if action["type"] == "motion")["motion"] == "salute"
    assert next(action for action in actions if action["type"] == "emoji") == {
        "type": "emoji",
        "emotion": "happy",
        "mode": "loop",
    }
    assert next(action for action in actions if action["type"] == "tts")["text"] == "任务专属讲解"


def test_real_task_clap_overrides_point_hug():
    actions = merge_task_arrival_actions(
        {
            "point_id": "1787297468993:过度点",
            "speech_text": "大家好，这里是火花机器人，我是灵犀",
            "arrival_motion": {"motion": "clap", "area": "whole_body", "interrupt": True, "force": True},
            "arrival_emoji": {"emotion": "extreme_happy", "mode": "once"},
        },
        [
            {"type": "motion", "motion": "hug", "area": "whole_body"},
            {"type": "emoji", "emotion": "calm", "mode": "once"},
            {"type": "tts", "text": "点位默认讲解"},
        ],
    )
    motion = next(action for action in actions if action["type"] == "motion")
    assert motion == {
        "type": "motion",
        "motion": "clap",
        "area": "whole_body",
        "interrupt": True,
        "force": True,
    }


def test_real_task_cheer_overrides_point_lightwave():
    actions = merge_task_arrival_actions(
        {
            "point_id": "1787297468993:远征开门",
            "speech_text": "欢迎欢迎",
            "arrival_motion": {"motion": "cheer", "area": "whole_body", "interrupt": True, "force": True},
        },
        [{"type": "motion", "motion": "lightwave", "area": "whole_body"}],
    )
    assert actions[0]["motion"] == "cheer"


def test_normalization_preserves_explicit_disable_and_defaults_motion_flags():
    step = normalize_tour_step({
        "point_id": "p1",
        "arrival_motion": {"motion": "nod", "area": "head"},
        "arrival_emoji": None,
    })
    assert step is not None
    assert step["arrival_motion"] == {
        "motion": "nod",
        "area": "head",
        "interrupt": True,
        "force": True,
    }
    assert "arrival_emoji" in step and step["arrival_emoji"] is None


def test_normalization_preserves_optional_linkcraft_action_snapshot():
    step = normalize_tour_step({
        "point_id": "p1",
        "linkcraft_action": {
            "resource_key": "linkcraft_resource_onnx_taiji",
            "resource_version": "0.0.1",
            "name": "太极",
            "resource_type": "BODY_MONTION",
            "duration_sec": 31.22,
        },
    })
    assert step is not None
    assert step["linkcraft_action"]["name"] == "太极"
    assert step["linkcraft_action"]["duration_sec"] == 31.22


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项讲解动作与表情测试")
