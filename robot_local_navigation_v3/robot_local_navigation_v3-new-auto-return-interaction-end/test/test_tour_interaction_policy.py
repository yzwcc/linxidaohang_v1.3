from robot_local_navigation_v3.tour_interaction_policy import (
    is_tour_execution_state,
    should_block_voice_output,
    should_protect_tour_interaction,
)


def test_protects_all_active_tour_phases():
    for state in (
        "navigating_task_step",
        "task_arrival_action",
        "task_linkcraft_action",
    ):
        assert is_tour_execution_state(state)
        assert should_protect_tour_interaction(state, "chat")
        assert should_block_voice_output(state, "voice-event-1")


def test_blocks_voice_navigation_during_tour():
    assert should_protect_tour_interaction("navigating_task_step", "navigate")


def test_allows_explicit_task_controls_and_safety_stop():
    for intent in (
        "pause_task",
        "resume_task",
        "end_task",
        "stop_task",
        "stop_navigation",
    ):
        assert not should_protect_tour_interaction("navigating_task_step", intent)


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
