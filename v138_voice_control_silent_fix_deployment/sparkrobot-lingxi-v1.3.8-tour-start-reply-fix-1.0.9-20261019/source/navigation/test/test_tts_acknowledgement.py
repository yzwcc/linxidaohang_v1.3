from concurrent.futures import Future
import importlib.util
from pathlib import Path
import threading
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from robot_local_navigation_v3.tts_receipt import future_receipt, response_receipt, wait_receipt
from test.test_tour_bridge_behavior import rig, bridge_module, begin_wait, mark, end, reply


def response(accepted=True, error=""):
    return NS(tts_resp=NS(is_success=accepted, error_message=error))


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), (None, None), ("true", None)])
def test_only_real_boolean_acknowledgements_are_definitive(value, expected):
    assert response_receipt(response(value))["accepted"] is expected
    assert response_receipt(NS())["accepted"] is None


def test_pending_exception_and_late_acceptance_never_invent_success():
    future = Future()
    assert wait_receipt(future, .001)["status"] == "ack_pending"
    future.set_result(response(True))
    assert wait_receipt(future, .001)["accepted"] is True
    failure = Future()
    failure.set_exception(RuntimeError("transport lost"))
    assert future_receipt(failure)["accepted"] is None


def test_rejection_reason_can_come_from_the_response_header():
    value = response(False)
    value.header = NS(header=NS(message="priority_rejected"))
    assert response_receipt(value)["message"] == "priority_rejected"


@pytest.mark.parametrize("failure", ["exception", "malformed"])
def test_unknown_ack_holds_tour_for_bounded_playback_then_full_quiet_period(rig, failure):
    begin_wait(rig)
    mark(rig)
    reply(rig, "x" * 200)
    if failure == "exception":
        rig.futures[-1].set_exception(RuntimeError("receipt lost"))
    else:
        rig.futures[-1].set_result(NS())
    end(rig)
    hold = rig.bridge._tour_reply_until - rig.clock.now
    assert 0 < hold <= rig.module.ARRIVAL_TTS_MAX_WAIT_SEC
    rig.clock.advance(hold + 9.99)
    rig.bridge.navigation_client.start.assert_not_called()
    rig.clock.advance(.01)
    assert rig.bridge.navigation_client.start.call_count == 1


@pytest.mark.parametrize("accepted", [True, False])
def test_http_worker_waits_outside_tour_lock_and_observes_actual_result(rig, accepted):
    begin_wait(rig)
    entered = threading.Event()
    result = []
    original = rig.bridge.tts_speaker.speak.side_effect
    def submit(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.set()
        return value
    rig.bridge.tts_speaker.speak.side_effect = submit
    thread = threading.Thread(target=lambda: result.append(rig.bridge.speak(
        {"text": "answer", "force": True}, wait_for_ack=True)))
    thread.start()
    assert entered.wait(1)
    with rig.bridge._auto_return_lock:
        rig.futures[-1].set_result(response(accepted, "priority_rejected" if not accepted else ""))
    thread.join(2)
    assert not thread.is_alive()
    assert result[0]["accepted"] is accepted
    assert rig.bridge.tts_speaker.speak.call_count == 1


def test_internal_ros_call_does_not_wait_or_report_accepted(rig):
    receipt = rig.bridge.speak({"text": "internal", "force": True})
    assert receipt["accepted"] is None and receipt["status"] == "ack_pending"
    retry = rig.bridge.tts_speaker.speak.call_args.kwargs["retry_submit"]
    send = Mock()
    assert retry(send)
    rig.bridge._auto_return_generation += 1
    assert not retry(send)
    send.assert_called_once()


@pytest.fixture
def speaker_module():
    msg, srv = ModuleType("aimdk_msgs.msg"), ModuleType("aimdk_msgs.srv")
    msg.TtsPriorityLevel = NS(SYSTEM_L7=7)
    srv.PlayTts = NS(Request=lambda: NS(header=NS(header=NS()),
        tts_req=NS(priority_level=NS())))
    path = Path(__file__).resolve().parents[1] / "robot_local_navigation_v3/tts_speaker.py"
    spec = importlib.util.spec_from_file_location("speaker_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", {"aimdk_msgs.msg": msg, "aimdk_msgs.srv": srv}):
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("outcome,attempts", [("recovered", 2), ("rejected", 3),
    ("unrelated", 1), ("unknown", 1), ("pending", 1), ("stale", 1)])
def test_bounded_retry_only_for_explicit_priority_rejection(speaker_module, outcome, attempts):
    module = speaker_module
    node = Mock()
    node.get_clock.return_value.now.return_value.to_msg.return_value = object()
    client = node.create_client.return_value
    client.wait_for_service.return_value = True
    values, requests, timers = [], [], []
    def call(request):
        requests.append((request.tts_req.trace_id, request.tts_req.priority_level.value))
        value = Future()
        values.append(value)
        return value
    client.call_async.side_effect = call
    class Timer:
        def __init__(self, interval, callback, args):
            self.callback, self.args = callback, args
        def start(self):
            timers.append(self)
    def retry(send):
        if outcome == "stale":
            return False
        send()
        return True
    with patch.object(module.threading, "Timer", Timer):
        speaker = module.TtsSpeaker(node)
        result = speaker.speak("answer", retry_submit=retry)
        for i in range(3):
            if outcome == "pending":
                break
            if outcome == "unknown":
                values[i].set_exception(RuntimeError("lost"))
            else:
                error = "unavailable" if outcome == "unrelated" else "priority_rejected"
                values[i].set_result(response(outcome == "recovered" and i == 1, error))
            if not timers:
                break
            timer = timers.pop(0)
            timer.callback(*timer.args)
            if result.done():
                break
    assert len(requests) == attempts
    assert len({item[0] for item in requests}) == attempts
    assert all(item[1] == 7 for item in requests)
    assert not timers
    if outcome == "recovered":
        assert future_receipt(result)["accepted"] is True
    elif outcome != "pending":
        assert future_receipt(result)["accepted"] is (None if outcome == "unknown" else False)
    else:
        assert not result.done()
