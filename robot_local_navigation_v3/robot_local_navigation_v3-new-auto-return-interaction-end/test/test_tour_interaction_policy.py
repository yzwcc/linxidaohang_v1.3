from robot_local_navigation_v3.tour_interaction_policy import (
    WAKE_ACTION_ALLOW,
    WAKE_ACTION_BLOCK,
    WAKE_ACTION_PAUSE,
    is_tour_execution_state,
    should_block_voice_output,
    should_protect_tour_interaction,
    wake_interaction_action,
)


def test_recognizes_all_active_tour_phases():
    for state in (
        "navigating_task_step",
        "task_arrival_action",
        "task_linkcraft_action",
    ):
        assert is_tour_execution_state(state)


def test_allows_chat_and_voice_output_while_walking():
    assert not should_protect_tour_interaction("navigating_task_step", "chat")
    assert not should_block_voice_output("navigating_task_step", "voice-event-1")


def test_blocks_voice_navigation_during_tour():
    assert should_protect_tour_interaction("navigating_task_step", "navigate")


def test_arrival_requires_wake_pause_before_chat_and_tts():
    assert should_protect_tour_interaction("task_arrival_action", "chat")
    assert should_block_voice_output("task_arrival_action", "voice-event-1")


def test_linkcraft_blocks_wake_pause_and_chat():
    assert should_protect_tour_interaction("task_linkcraft_action", "wake")
    assert should_protect_tour_interaction("task_linkcraft_action", "pause_task")
    assert should_protect_tour_interaction("task_linkcraft_action", "chat")
    assert should_block_voice_output("task_linkcraft_action", "voice-event-1")


def test_linkcraft_keeps_safety_stop_available():
    for intent in ("end_task", "stop_task", "stop_navigation"):
        assert not should_protect_tour_interaction("task_linkcraft_action", intent)


def test_allows_explicit_task_controls_and_safety_stop():
    for intent in (
        "pause_task",
        "resume_task",
        "end_task",
        "stop_task",
        "stop_navigation",
    ):
        assert not should_protect_tour_interaction("navigating_task_step", intent)


def test_wake_action_depends_on_tour_phase():
    assert wake_interaction_action("navigating_task_step") == WAKE_ACTION_ALLOW
    assert wake_interaction_action("task_arrival_action") == WAKE_ACTION_PAUSE
    assert wake_interaction_action("task_linkcraft_action") == WAKE_ACTION_BLOCK
    assert wake_interaction_action("task_paused") == WAKE_ACTION_ALLOW
    assert wake_interaction_action("idle") == WAKE_ACTION_ALLOW


def test_paused_task_allows_normal_interaction():
    assert not should_protect_tour_interaction("task_paused", "chat")
    assert not should_block_voice_output("task_paused", "voice-event-1")


def test_task_tts_without_cloud_event_is_not_blocked():
    assert not should_block_voice_output("task_arrival_action", "")


def test_task_arrival_tts_is_not_mistaken_for_cloud_interaction():
    assert not should_block_voice_output("task_arrival_action", "arrival-tour-step-1")


def test_navigation_problem_tts_is_not_mistaken_for_cloud_interaction():
    assert not should_block_voice_output("navigating_task_step", "nav-problem-tour-step-1")


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项讲解语音防打断测试")
