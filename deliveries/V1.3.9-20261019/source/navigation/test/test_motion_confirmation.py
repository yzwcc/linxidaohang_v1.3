"""Load production methods and inject acknowledgement loss without ROS."""
import ast
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace as NS

import pytest


@pytest.fixture
def controller():
    spec = importlib.util.find_spec("robot_local_navigation_v3")
    path = Path(next(iter(spec.submodule_search_locations))) / "sdk_motion_controller.py"
    tree = ast.parse(path.read_bytes())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SdkMotionController")
    names = {"play_preset_motion_and_wait", "_handle_motion_result", "_notify_motion_result"}
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    program = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls,
    ], type_ignores=[])
    env = {
        "CommonState": NS(SUCCESS=1, RUNNING=400, CREATED=200, PENDING=300),
        "threading": threading, "DEFAULT_MOTION_CONFIRMATION_TIMEOUT_SEC": 6,
        "MOTION_CONFIRMATION_UNKNOWN": "暂未收到动作确认，请先观察机器人状态",
        "is_request_acknowledged": lambda code, state, accepted: int(code) == 0 if code is not None else state in accepted,
    }
    exec(compile(ast.fix_missing_locations(program), str(path), "exec"), env)
    value = env["SdkMotionController"]()
    value.node = NS(get_logger=lambda: NS(info=lambda *_: None, warning=lambda *_: None))
    value.retries = []
    value._schedule_motion_retry = lambda *a, **k: value.retries.append(a)
    return value


def test_lost_reply_never_resubmits_physical_motion(controller):
    def lost():
        raise OSError("reply lost after submission")

    def submit(*args, on_result, cancel_event, **kwargs):
        future = NS(result=lost)
        controller._handle_motion_result(future, NS(motion_id=1003, area_id=2), 1, on_result, cancel_event)
        return future

    controller.play_preset_motion = submit
    result = controller.play_preset_motion_and_wait("handshake", timeout_sec=0.1)
    assert controller.retries == []
    assert result["status"] == "confirmation_unknown"
    assert result["accepted"] is False


def test_timeout_cancels_late_resubmission(controller):
    cancellations = []
    controller.play_preset_motion = lambda *a, cancel_event, **k: cancellations.append(cancel_event) or object()
    result = controller.play_preset_motion_and_wait("handshake", timeout_sec=0.1)
    assert result["status"] == "confirmation_timeout"
    assert "重试" not in result["message"]
    assert cancellations[0].is_set()


@pytest.mark.parametrize("code,state,expected", [(0, 0, True), (7, 1, False), (None, 1, True), (None, None, None)])
def test_sdk_acknowledgement_is_not_completion_or_permission_to_retry(controller, code, state, expected):
    replies = []
    future = NS(result=lambda: NS(response=NS(state=NS(value=state), header=NS(code=code))))
    controller._handle_motion_result(future, NS(motion_id=1003, area_id=2), 1,
                                     lambda *args: replies.append(args), threading.Event())
    assert controller.retries == []
    assert len(replies) == 1
    assert replies[0][0] is (expected is True)
    if expected is None:
        assert "暂未收到" in replies[0][1]


def test_late_reply_after_timeout_is_ignored(controller):
    cancelled = threading.Event()
    cancelled.set()
    future = NS(result=lambda: pytest.fail("late response must not be inspected"))
    controller._handle_motion_result(future, NS(), 1, lambda *_: pytest.fail("late callback"), cancelled)
    assert controller.retries == []
