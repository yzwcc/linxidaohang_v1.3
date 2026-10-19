from __future__ import annotations

import ast
from pathlib import Path
import textwrap


BRIDGE = (
    Path(__file__).resolve().parents[1]
    / "robot_local_navigation_v3"
    / "lingxi_nav_bridge.py"
)
NAVIGATION_CLIENT = BRIDGE.with_name("navigation_client.py")


def method_calls(method_name: str) -> set[str]:
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name:
            calls: set[str] = set()
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                if isinstance(call.func, ast.Attribute):
                    calls.add(call.func.attr)
                elif isinstance(call.func, ast.Name):
                    calls.add(call.func.id)
            return calls
    raise AssertionError(f"method not found: {method_name}")


def methods_calling(callee_name: str) -> set[str]:
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    callers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if callee_name in method_calls(node.name):
            callers.add(node.name)
    return callers


def class_method_names(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"class not found: {class_name}")


def test_normal_arrival_completion_never_submits_empty_interrupt():
    normal_methods = (
        "_start_task_step",
        "_schedule_next_task_step_after_arrival",
        "_on_task_arrival_wait_done",
        "_schedule_auto_return_after_arrival",
        "_on_arrival_action_wait_done",
    )
    for method_name in normal_methods:
        calls = method_calls(method_name)
        assert "_stop_arrival_motion" not in calls
        assert (
            "_finish_arrival_motion_after_wait" in calls
            or "_finish_task_arrival_and_continue" in calls
        )


def test_explicit_pause_and_stop_still_interrupt_an_active_arrival_motion():
    assert "_stop_arrival_motion" in method_calls("pause_task")
    assert "_stop_arrival_motion" in method_calls("stop_task")


def test_voice_interruption_has_a_quiet_resume_path_while_manual_pause_remains_explicit():
    source = BRIDGE.read_text(encoding="utf-8")
    assert "auto_resume_after_silence" in source
    assert "_schedule_voice_interruption_resume_locked" in source
    assert "_on_voice_interruption_resume" in source
    assert "VOICE_INTERRUPTION_RESUME_SEC" in source


def test_empty_motion_is_rejected_as_normal_arrival_content():
    assert "is_empty_motion" in method_calls("_run_arrival_motion")


def test_wait_completion_delegates_empty_interrupt_to_the_motion_policy():
    calls = method_calls("_finish_arrival_motion_after_wait")
    assert "completion_action_after_wait" in calls
    assert "_stop_arrival_motion" in calls
    assert "play_preset_motion" not in calls


def test_empty_interrupt_is_confined_to_explicit_interrupt_paths():
    assert methods_calling("_stop_arrival_motion") == {
        "_cancel_task_for_new_intent",
        "_finish_task_without_return",
        "_finish_arrival_motion_after_wait",
        "_interrupt_linkcraft_action",
        "pause_task",
        "stop_arrival_motion",
        "stop_task",
    }


def test_pnc_failure_callback_keeps_its_async_failure_handler():
    methods = class_method_names(NAVIGATION_CLIENT, "NavigationSession")
    assert "handle_pnc_response" in methods
    assert "_fail_async" in methods
    assert "_fail" in methods


def test_arrival_fallback_requires_fresh_strict_entry_and_combined_stability():
    source = NAVIGATION_CLIENT.read_text(encoding="utf-8")
    start = source.index("    def handle_odom(")
    end = source.index("    def handle_pnc_response(", start)
    method = source[start:end]
    assert "is_post_entry_arrival_latch_active" in method
    assert "is_arrival_sample_stable" in method
    assert "widened_post_entry_fallback" in method
    assert "POST_ENTRY_LOW_SPEED_THRESHOLD_MPS" in method


def test_timeout_arrival_recovery_is_opt_in_for_tour_steps_only():
    bridge_source = BRIDGE.read_text(encoding="utf-8")
    task_start = bridge_source.index("    def _start_task_step(")
    task_end = bridge_source.index("    def _begin_task_arrival_action(", task_start)
    task_method = bridge_source[task_start:task_end]
    assert "allow_timeout_arrival_recovery=True" in task_method

    client_source = NAVIGATION_CLIENT.read_text(encoding="utf-8")
    start = client_source.index("    def start(")
    start_end = client_source.index("    def cancel(", start)
    start_method = client_source[start:start_end]
    assert "allow_timeout_arrival_recovery: bool = False" in start_method
    timeout_start = client_source.index("    def _on_timeout(")
    timeout_end = client_source.index("    def _fail_async(", timeout_start)
    timeout_method = client_source[timeout_start:timeout_end]
    assert "self.allow_timeout_arrival_recovery" in timeout_method
    assert "require_strict_region_entry=False" in timeout_method


def test_tour_arrival_actions_do_not_inject_fallback_speech_or_use_undefined_flag():
    source = BRIDGE.read_text(encoding="utf-8")
    start = source.index("    def _run_on_arrival_actions(")
    end = source.index("    def _schedule_arrival_actions(", start)
    method = source[start:end]
    assert "fallback speech" not in method
    tree = ast.parse(textwrap.dedent(method))
    assert not any(
        isinstance(node, ast.Name) and node.id == "task_arrival"
        for node in ast.walk(tree)
    )


def test_task_wait_runs_after_step_effects_and_advances_once():
    assert "_begin_linkcraft_or_advance" in method_calls("_finish_task_arrival_and_continue")
    assert "_begin_task_wait" in method_calls("_begin_linkcraft_or_advance")
    wait_done_calls = method_calls("_on_task_wait_done")
    assert "_advance_task_after_arrival_locked" in wait_done_calls
    assert "_start_task_step" in wait_done_calls


def test_task_wait_can_pause_and_resume_without_restarting_the_step():
    assert "_resume_task_wait" in method_calls("resume_task")
    resume_calls = method_calls("_resume_task_wait")
    assert "_restart_task_wait_after_interaction_locked" in resume_calls
    assert "_start_task_step" not in resume_calls


def test_task_wait_resets_after_each_completed_interaction():
    mark_calls = method_calls("_mark_valid_interaction")
    end_calls = method_calls("_end_valid_interaction")
    reset_calls = method_calls("_restart_task_wait_after_interaction_locked")
    assert "_cancel_arrival_action_timer_locked" in mark_calls
    assert "_restart_task_wait_after_interaction_locked" in end_calls
    assert "_on_task_wait_done" in reset_calls
    assert "create_timer" in reset_calls


def test_task_wait_callbacks_keep_generation_and_goal_guards():
    source = BRIDGE.read_text(encoding="utf-8")
    assert "generation != self._auto_return_generation" in source
    assert "self._task_current_goal_id != goal_id" in source


def test_consecutive_steps_at_same_point_reuse_arrival_without_new_navigation():
    source = BRIDGE.read_text(encoding="utf-8")
    start = source.index("    def _start_task_step(")
    end = source.index("    def _begin_task_arrival_action(", start)
    method = source[start:end]
    assert "self._task_last_goal_id == point.id" in method
    assert "reason=\"consecutive_same_point\"" in method
    assert method.index("if reuse_current_arrival:") < method.index("self.navigation_client.start(")
    assert "self.handle_navigation_event(NavigationEvent(" in method
    assert "not resumed" in method
