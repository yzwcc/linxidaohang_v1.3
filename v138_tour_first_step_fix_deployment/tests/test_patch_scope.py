import ast
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]


def methods(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LingxiNavBridge")
    return {node.name: ast.dump(node, include_attributes=False)
            for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_patch_preserves_r20_voice_suppression_and_unrelated_methods():
    baseline = methods(HERE / "reference/runtime/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py")
    patched = methods(HERE / "source/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py")
    permitted = {
        "__init__", "start_task", "handle_navigation_event", "_load_tour_task_points",
        "_start_task_step", "_queue_task_navigation_retry", "_on_task_navigation_retry",
        "_on_task_failure_advance", "_clear_task_locked",
        "_is_task_navigation_event_locked",
    }
    changed = {name for name in baseline if baseline[name] != patched[name]}
    assert changed <= permitted, "unexpected changed methods: " + str(changed - permitted)
    added = set(patched) - set(baseline)
    assert added == {
        "_make_task_navigation_listener",
        "_is_current_task_step_locked",
        "_is_current_task_navigation_callback_locked",
    }
    assert not set(baseline) - set(patched)
    assert baseline["_speak_navigation_problem"] == patched["_speak_navigation_problem"]
