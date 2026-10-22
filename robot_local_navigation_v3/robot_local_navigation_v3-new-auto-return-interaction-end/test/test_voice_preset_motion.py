from robot_local_navigation_v3.voice_preset_motion import (
    VOICE_PRESET_MOTIONS,
    resolve_voice_command,
    resolve_voice_preset_motion,
    submit_voice_preset_motion,
)


class FakeMotionController:
    def __init__(self):
        self.calls = []
        self.future = object()

    def services_ready(self):
        return {"motion": True}

    def play_preset_motion(self, motion, area, *, interrupt):
        self.calls.append((motion, area, interrupt))
        return self.future


def test_all_r15_actions_have_default_area_and_voice_command():
    commands = {
        "wave": "请你挥挥手",
        "handshake": "请和我握个手",
        "raise_hand": "麻烦你举手",
        "flying_kiss": "送一个飞吻",
        "sweetheart": "给大家比个爱心",
        "high_five": "来个击掌",
        "salute": "请敬礼",
        "horizontal_arm": "双手平举",
        "hug": "做个拥抱",
        "cheer": "做个加油动作",
        "clap": "请鼓掌",
        "handx": "双手打叉",
        "chestwave": "请在胸前挥手",
        "bow": "麻烦鞠躬",
        "bye": "挥手告别",
        "lightwave": "发射动感光波",
        "scratch_head": "请你挠挠头",
        "scratch_back": "请挠屁股",
    }
    assert set(commands) == set(VOICE_PRESET_MOTIONS)
    for name, command in commands.items():
        motion = VOICE_PRESET_MOTIONS[name]
        assert resolve_voice_preset_motion(name) == (name, motion.default_area)
        assert resolve_voice_command(command) == (name, motion.default_area)


def test_handshake_sweetheart_and_arm_area_selection():
    assert resolve_voice_command("请用左手握手") == ("handshake", "left_hand")
    assert resolve_voice_command("请用右手挥手") == ("wave", "right_hand")
    assert resolve_voice_command("请双手比心") == ("sweetheart", "both_hands")


def test_unknown_motion_and_incompatible_area_are_rejected():
    assert resolve_voice_preset_motion("invented_motion") is None
    assert resolve_voice_preset_motion("handshake", "whole_body") is None
    assert resolve_voice_preset_motion("hug", "right_hand") is None


def test_questions_negations_quotes_reports_and_conditions_are_not_commands():
    for text in (
        "你会握手吗",
        "能不能给我握个手",
        "不要握手",
        "别比心",
        "别挥手",
        "他说握手",
        "刚才说请挥手",
        "如果可以就握手",
        "假如方便请比心",
        "“握手”",
        "\"请挥手\"",
        "我想了解如何握手",
    ):
        assert resolve_voice_command(text) is None, text
    assert resolve_voice_command("请你挥手！") == ("wave", "right_hand")


def test_bridge_submission_rechecks_raw_command_and_never_interrupts():
    controller = FakeMotionController()
    accepted = submit_voice_preset_motion(
        "请和我握手",
        "handshake",
        "right_hand",
        "idle",
        False,
        controller,
    )
    assert accepted["accepted"] is True
    assert accepted["submitted"] is True
    assert "握手动作请求已受理" in accepted["message"]
    assert controller.calls == [("handshake", "right_hand", False)]

    mismatched = submit_voice_preset_motion(
        "请比心",
        "handshake",
        "right_hand",
        "idle",
        False,
        controller,
    )
    assert mismatched["accepted"] is False
    assert mismatched["status"] == "command_rejected"
    assert len(controller.calls) == 1


def test_bridge_refuses_navigation_and_tour_states_before_sdk_submission():
    for navigation_status, task_active, expected in (
        ("navigating", False, "navigation_active"),
        ("idle", True, "tour_active"),
    ):
        controller = FakeMotionController()
        result = submit_voice_preset_motion(
            "请比心",
            "sweetheart",
            "both_hands",
            navigation_status,
            task_active,
            controller,
        )
        assert result["accepted"] is False
        assert result["status"] == expected
        assert controller.calls == []


def test_bridge_refuses_unavailable_service_and_unsubmitted_future():
    class UnavailableController(FakeMotionController):
        def services_ready(self):
            return {"motion": False}

    result = submit_voice_preset_motion(
        "请挥手",
        "wave",
        "right_hand",
        "idle",
        False,
        UnavailableController(),
    )
    assert result["accepted"] is False
    assert result["status"] == "motion_service_unavailable"

    controller = FakeMotionController()
    controller.play_preset_motion = lambda *args, **kwargs: None
    result = submit_voice_preset_motion(
        "请挥手",
        "wave",
        "right_hand",
        "idle",
        False,
        controller,
    )
    assert result["accepted"] is False
    assert result["status"] == "not_submitted"


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
