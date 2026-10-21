from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test.test_tour_bridge_behavior import bridge_module, rig


@pytest.mark.parametrize("kind", ["user", "task"])
@pytest.mark.parametrize("event_type", ["FAILED", "TIMEOUT"])
@pytest.mark.parametrize("silent_flag", [True, False, "true", None])
def test_voice_execution_failure_is_log_only_regardless_of_receipt_flag(rig, kind, event_type, silent_flag):
    bridge, module = rig.bridge, rig.module
    bridge.speak = Mock()
    bridge._is_user_navigation_event = Mock(return_value=kind == "user")
    bridge._is_task_navigation_event = Mock(return_value=kind == "task")
    bridge._user_navigation_silent_receipts = silent_flag
    bridge._task_silent_receipts = silent_flag
    event = module.NavigationEvent(type=getattr(module.NavigationEventType, event_type),
        task_id=123, goal_id="p1", point_name="p1", status="failed", reason="blocked")
    bridge._speak_navigation_problem(event, auto_return=False)
    bridge.speak.assert_not_called()
    bridge.get_logger().warning.assert_called()


def test_unclassified_non_return_navigation_failure_is_also_log_only(rig):
    bridge = rig.bridge
    bridge.speak = Mock()
    bridge._is_user_navigation_event = Mock(return_value=False)
    bridge._is_task_navigation_event = Mock(return_value=False)
    bridge._user_navigation_silent_receipts = False
    bridge._speak_navigation_problem(rig.event, auto_return=False)
    bridge.speak.assert_not_called()


def test_automatic_return_problem_keeps_existing_tts_policy(rig):
    bridge = rig.bridge
    bridge.speak = Mock()
    bridge._speak_navigation_problem(rig.event, auto_return=True)
    bridge.speak.assert_called_once()


def test_silent_task_does_not_mute_configured_narration(rig):
    bridge = rig.bridge
    bridge._task_silent_receipts = True
    bridge.speak = Mock()
    bridge._run_arrival_tts({"text": "Configured tour narration"}, rig.event)
    bridge.speak.assert_called_once()
    assert bridge.speak.call_args.args[0]["text"] == "Configured tour narration"


@pytest.mark.parametrize("silent,event_id,expected", [
    (True, "voice-event", True), (False, "voice-event", False),
    ("true", "voice-event", False), (True, "", False),
])
def test_start_task_captures_only_explicit_boolean_silent_scope(rig, silent, event_id, expected):
    bridge, module = rig.bridge, rig.module
    bridge._auto_return_state = module.AUTO_RETURN_IDLE
    bridge._load_tour_task_points = Mock(return_value=("tour", "Tour", [{"point_id": "p1"}]))
    bridge._start_task_step = Mock(return_value=SimpleNamespace(
        task_id=4, goal_id="p1", point_name="p1", accepted=True, status="accepted", reason=""))
    result = bridge.start_task({"event_id": event_id, "silent_control_reply": silent})
    assert result["accepted"] is True
    assert bridge._task_silent_receipts is expected
