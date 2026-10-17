from __future__ import annotations

import ast
from pathlib import Path


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
