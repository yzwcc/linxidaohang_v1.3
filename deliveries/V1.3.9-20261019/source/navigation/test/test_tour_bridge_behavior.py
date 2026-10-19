"""Exercise the real bridge without constructing ROS nodes or opening sockets."""

from __future__ import annotations

from collections import namedtuple
from concurrent.futures import Future
import importlib.util
import robot_local_navigation_v3
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest


class FakeTimer:
    def __init__(self, clock, period, callback):
        self.clock = clock
        self.period = period
        self.deadline = clock.now + period
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def fire_queued(self):
        # ROS can already have queued a callback when cancel() is called.
        self.callback()


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.timers = []

    def monotonic(self):
        return self.now

    def create_timer(self, period, callback):
        assert period > 0
        timer = FakeTimer(self, period, callback)
        self.timers.append(timer)
        return timer

    def advance(self, seconds):
        target = self.now + seconds
        for _ in range(1000):
            ready = [t for t in self.timers if not t.cancelled and t.deadline <= target + 1e-9]
            if not ready:
                self.now = max(self.now, target)
                return
            timer = min(ready, key=lambda t: t.deadline)
            self.now = timer.deadline
            timer.deadline += timer.period
            timer.callback()
        raise AssertionError("timer loop did not settle")


@pytest.fixture
def bridge_module():
    def stub(name, **attributes):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        return module

    def forbidden(*args, **kwargs):
        raise AssertionError("a real hardware adapter must never be constructed")

    handle = namedtuple("NavigationSessionHandle", "task_id goal_id point_name accepted status reason", defaults=[""])
    prefix = "robot_local_navigation_v3."
    stubs = {
        "rclpy": stub("rclpy"),
        "rclpy.node": stub("rclpy.node", Node=object),
        prefix + "navigation_client": stub(prefix + "navigation_client", NavigationClient=forbidden, NavigationSessionHandle=handle),
        prefix + "sdk_motion_controller": stub(prefix + "sdk_motion_controller", DEFAULT_EMOJI_PRIORITY=0, SdkMotionController=forbidden),
        prefix + "tts_speaker": stub(prefix + "tts_speaker", TtsSpeaker=forbidden),
    }
    path = Path(robot_local_navigation_v3.__file__).parent / "lingxi_nav_bridge.py"
    spec = importlib.util.spec_from_file_location(prefix + "_behavior_test_bridge", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def rig(bridge_module):
    module = bridge_module
    clock = FakeClock()
    module.time = clock
    bridge = module.LingxiNavBridge.__new__(module.LingxiNavBridge)
    bridge.get_logger = Mock(return_value=Mock())
    bridge.create_timer = clock.create_timer
    bridge._auto_return_lock = threading.RLock()
    bridge._auto_return_generation = 0
    bridge._arrival_action_timer = None
    bridge._auto_return_timer = None
    bridge._auto_return_source_goal_id = "p1"
    bridge._tour_reply_generation = 0
    bridge._clear_task_locked("test_setup")
    bridge._auto_return_state = module.AUTO_RETURN_NAVIGATING_TASK_STEP
    bridge._last_valid_interaction_at = clock.now
    bridge._looping_arrival_emoji_active = False
    bridge._arrival_motion = module.ArrivalMotionTracker()
    bridge._tts_lock = threading.Lock()
    bridge._last_tts_at = 0.0
    bridge._tts_min_interval_sec = 5.0
    bridge.auto_return_enabled = False
    bridge.auto_return_idle_sec = 30.0
    bridge.active_map_name = "test-map"
    bridge._task_id = "tour"
    bridge._task_name = "Test tour"
    bridge._task_index = 0
    bridge._task_current_goal_id = "p1"
    bridge._task_last_goal_id = "p1"
    bridge._task_steps = [
        {"point_id": "p1", "speech_text": "Point one explanation", "wait_sec": 10},
        {"point_id": "p2", "speech_text": "Point two explanation", "wait_sec": 10},
    ]
    points = {key: SimpleNamespace(id=key, name=key, is_start=False, business=SimpleNamespace(on_arrival=[]))
              for key in ("p1", "p2")}
    bridge.point_catalog = SimpleNamespace(get_by_id=points.__getitem__, get_start_point=lambda *args: None)
    bridge.navigation_client = Mock()
    bridge.navigation_client.defaults = SimpleNamespace(
        task_failure_policy=SimpleNamespace(
            continue_after_failure=True,
            max_retries_per_step=1,
            retry_delay_sec=1.0,
            announce_skip=False,
        ),
        dense_crowd=SimpleNamespace(enabled=False),
    )
    bridge.navigation_client.get_status.return_value = {"status": "idle"}
    bridge.navigation_client.start.side_effect = lambda point, **kwargs: module.NavigationSessionHandle(
        task_id=2, goal_id=point.id, point_name=point.name, accepted=True, status="accepted"
    )
    bridge.motion_controller = Mock()
    bridge.tts_speaker = Mock()
    futures = []

    def speak(*args, **kwargs):
        future = Future()
        futures.append(future)
        return future

    bridge.tts_speaker.speak.side_effect = speak
    event = module.NavigationEvent(type=module.NavigationEventType.COMPLETED, task_id=1,
                                   goal_id="p1", point_name="p1", status="completed")
    bridge._begin_task_arrival_action(event)
    yield SimpleNamespace(module=module, clock=clock, bridge=bridge, event=event, futures=futures)
    bridge.get_logger().error.assert_not_called()


def begin_wait(rig):
    rig.bridge._begin_task_wait(rig.event, "effects_finished")


def wake(rig, event_id="turn-1"):
    return rig.bridge.wake_interaction({"event_id": event_id})


def mark(rig, event_id="turn-1"):
    return rig.bridge.mark_interaction({"event_id": event_id, "source": "asr", "intent": "chat"})


def end(rig, event_id="turn-1"):
    return rig.bridge.end_interaction({"event_id": event_id, "source": "audio2tts_done"})


def reply(rig, text="A robot reply", event_id="turn-1"):
    return rig.bridge.speak({"text": text, "event_id": event_id, "force": True})


def acknowledge(rig, index=-1, accepted=True):
    rig.futures[index].set_result(SimpleNamespace(tts_resp=SimpleNamespace(is_success=accepted)))


def test_new_interaction_invalidates_an_already_queued_resume(rig):
    wake(rig)
    end(rig)
    old_timer = rig.bridge._arrival_action_timer
    rig.clock.advance(4)
    mark(rig, "turn-2")
    old_timer.fire_queued()
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_PAUSED
    assert not rig.bridge.tts_speaker.speak.called
    end(rig, "turn-2")
    rig.clock.advance(4.99)
    assert not rig.bridge.tts_speaker.speak.called
    rig.clock.advance(0.01)
    assert rig.bridge.tts_speaker.speak.call_args.args[0] == "Point one explanation"
    rig.bridge.navigation_client.start.assert_not_called()


def test_resume_falls_back_to_last_activity_when_end_hook_is_missing(rig):
    wake(rig)
    rig.clock.advance(4)
    mark(rig)
    rig.clock.advance(rig.module.TOUR_INTERACTION_FALLBACK_SEC + 4.99)
    assert not rig.bridge.tts_speaker.speak.called
    rig.clock.advance(0.01)
    assert rig.bridge.tts_speaker.speak.call_count == 1
    rig.bridge.navigation_client.start.assert_not_called()


def test_manual_pause_overrides_voice_pause_even_after_timer_was_queued(rig):
    wake(rig)
    end(rig)
    timer = rig.bridge._arrival_action_timer
    rig.bridge.pause_task({})
    timer.fire_queued()
    mark(rig, "later")
    end(rig, "later")
    rig.clock.advance(300)
    assert rig.bridge._task_status()["interruption"]["active"] is False
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_PAUSED
    rig.bridge.tts_speaker.speak.assert_not_called()
    result = rig.bridge.resume_task({})
    assert result["resumed"]
    assert rig.bridge.tts_speaker.speak.call_count == 1
    rig.bridge.navigation_client.start.assert_not_called()


def test_manual_pause_during_arrival_never_auto_resumes(rig):
    rig.bridge.pause_task({})
    mark(rig)
    end(rig)
    rig.clock.advance(300)
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_PAUSED
    rig.bridge.tts_speaker.speak.assert_not_called()


def test_navigation_pause_still_resumes_navigation(rig):
    rig.bridge._auto_return_state = rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP
    rig.bridge.pause_task({"auto_resume_after_silence": True})
    rig.clock.advance(300)
    rig.bridge.navigation_client.start.assert_not_called()
    assert rig.bridge.resume_task({})["resumed"]
    assert rig.bridge.navigation_client.start.call_args.args[0].id == "p1"


def test_wait_is_full_continuous_silence_after_the_latest_interaction(rig):
    begin_wait(rig)
    old_timer = rig.bridge._arrival_action_timer
    rig.clock.advance(8)
    mark(rig)
    rig.clock.advance(2)
    old_timer.fire_queued()
    assert rig.bridge._task_index == 0
    assert rig.bridge._task_wait_status_locked()["interaction_active"]
    end(rig)
    rig.clock.advance(9.99)
    rig.bridge.navigation_client.start.assert_not_called()
    rig.clock.advance(0.01)
    assert rig.bridge.navigation_client.start.call_count == 1
    assert rig.bridge._task_index == 1
    old_timer.fire_queued()
    assert rig.bridge.navigation_client.start.call_count == 1


def test_wait_has_fallback_when_interaction_end_is_lost(rig):
    begin_wait(rig)
    rig.clock.advance(8)
    mark(rig)
    rig.clock.advance(rig.module.TOUR_INTERACTION_FALLBACK_SEC + 9.99)
    rig.bridge.navigation_client.start.assert_not_called()
    rig.clock.advance(0.01)
    assert rig.bridge._task_index == 1


def test_blocked_reminder_preserves_looped_tour_state_and_does_not_advance(rig):
    from dataclasses import replace
    rig.bridge._task_loop_count = 3
    rig.bridge._task_loop_index = 2
    rig.bridge._auto_return_state = rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP
    rig.bridge._task_navigation_task_id = 1
    rig.bridge.speak = Mock(return_value={"ok": True})
    event = replace(rig.event, type=rig.module.NavigationEventType.BLOCKED, status="blocked")
    rig.bridge.handle_navigation_event(event)
    assert rig.bridge.speak.call_args.args[0]["text"] == "请大家让让我"
    assert rig.bridge._task_index == 0
    assert rig.bridge._task_loop_index == 2
    rig.bridge.navigation_client.start.assert_not_called()


def test_stale_blocked_event_does_not_interrupt_current_tour(rig):
    from dataclasses import replace
    rig.bridge._auto_return_state = rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP
    rig.bridge._task_navigation_task_id = 999
    rig.bridge.speak = Mock()
    event = replace(rig.event, type=rig.module.NavigationEventType.BLOCKED,
                    status="blocked", goal_id="p1", task_id=1)
    rig.bridge.handle_navigation_event(event)
    rig.bridge.speak.assert_not_called()


def test_looped_task_restarts_at_step_one_only_after_the_final_step(rig):
    task_ids = iter([10, 20, 30])
    rig.bridge.navigation_client.start.side_effect = lambda point, **kwargs: rig.module.NavigationSessionHandle(
        task_id=next(task_ids), goal_id=point.id, point_name=point.name, accepted=True, status="accepted"
    )
    rig.bridge._task_loop_count = 2
    rig.bridge._task_loop_index = 1

    begin_wait(rig)
    rig.clock.advance(10)
    assert rig.bridge._task_index == 1
    assert rig.bridge._task_loop_index == 1

    second_point = rig.module.NavigationEvent(
        type=rig.module.NavigationEventType.COMPLETED,
        task_id=10,
        goal_id="p2",
        point_name="p2",
        status="completed",
    )
    rig.bridge._begin_task_arrival_action(second_point)
    rig.bridge._begin_task_wait(second_point, "effects_finished")
    rig.clock.advance(10)
    assert rig.bridge._task_index == 0
    assert rig.bridge._task_loop_index == 2
    assert rig.bridge._task_status_locked()["loops_remaining"] == 0

    late_first_loop_event = rig.module.NavigationEvent(
        type=rig.module.NavigationEventType.COMPLETED,
        task_id=10,
        goal_id="p1",
        point_name="p1",
        status="completed",
    )
    rig.bridge.handle_navigation_event(late_first_loop_event)
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP

    current_second_loop_event = rig.module.NavigationEvent(
        type=late_first_loop_event.type,
        task_id=20,
        goal_id="p1",
        point_name="p1",
        status=late_first_loop_event.status,
    )
    rig.bridge._begin_task_arrival_action(current_second_loop_event)
    rig.bridge._begin_task_wait(current_second_loop_event, "effects_finished")
    rig.clock.advance(10)
    assert rig.bridge._task_index == 1
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP

    final_second_loop_event = rig.module.NavigationEvent(
        type=late_first_loop_event.type,
        task_id=30,
        goal_id="p2",
        point_name="p2",
        status=late_first_loop_event.status,
    )
    rig.bridge._begin_task_arrival_action(final_second_loop_event)
    rig.bridge._begin_task_wait(final_second_loop_event, "effects_finished")
    rig.clock.advance(10)
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_IDLE
    assert rig.bridge.navigation_client.start.call_count == 3


def test_pausing_a_looped_task_preserves_the_current_loop(rig):
    rig.bridge._task_loop_count = 3
    rig.bridge._task_loop_index = 2
    result = rig.bridge.pause_task({})
    assert result["paused"]
    assert rig.bridge._task_status_locked()["loop_index"] == 2
    assert rig.bridge._task_status_locked()["loop_count"] == 3


@pytest.mark.parametrize("waiting", [True, False])
def test_late_or_duplicate_end_does_not_change_current_timer(rig, waiting):
    begin_wait(rig) if waiting else wake(rig)
    mark(rig, "old")
    end(rig, "old")
    timer = rig.bridge._arrival_action_timer
    assert not end(rig, "old")["ended"]
    assert rig.bridge._arrival_action_timer is timer
    mark(rig, "new")
    timer = rig.bridge._arrival_action_timer
    assert not end(rig, "old")["ended"]
    assert not end(rig, "")["ended"]
    assert rig.bridge._arrival_action_timer is timer
    assert end(rig, "new")["ended"]


@pytest.mark.parametrize("waiting", [True, False])
def test_reply_ack_and_cloud_end_cannot_shorten_local_playback(rig, waiting):
    begin_wait(rig) if waiting else wake(rig)
    mark(rig)
    text = "x" * 80
    assert reply(rig, text)["accepted"]
    rig.clock.advance(12)
    assert rig.bridge._task_index == 0
    acknowledge(rig)
    end(rig)
    duration = rig.bridge._arrival_action_wait_sec("tts", {"text": text}) + rig.module.ARRIVAL_COMPLETION_GUARD_SEC
    quiet = 10 if waiting else 5
    rig.clock.advance(duration + quiet - 0.01)
    assert rig.bridge._task_index == 0
    assert rig.bridge.tts_speaker.speak.call_count == 1
    rig.clock.advance(0.01)
    if waiting:
        assert rig.bridge._task_index == 1
    else:
        assert rig.bridge.tts_speaker.speak.call_count == 2
        rig.bridge.navigation_client.start.assert_not_called()


def test_reply_without_mark_or_end_still_holds_and_resets_wait(rig):
    begin_wait(rig)
    rig.clock.advance(9)
    reply(rig, "x" * 40, event_id="")
    acknowledge(rig)
    quiet_start = rig.bridge._tour_reply_until
    rig.clock.advance(quiet_start - rig.clock.now + 9.99)
    rig.bridge.navigation_client.start.assert_not_called()
    rig.clock.advance(0.01)
    assert rig.bridge._task_index == 1


@pytest.mark.parametrize("failure", ["rejected", "exception", "unavailable"])
def test_failed_reply_releases_hold_without_waiting_for_timeout(rig, failure):
    begin_wait(rig)
    mark(rig)
    if failure == "unavailable":
        rig.bridge.tts_speaker.speak.side_effect = lambda *args, **kwargs: None
    reply(rig)
    if failure == "rejected":
        acknowledge(rig, accepted=False)
    elif failure == "exception":
        rig.futures[-1].set_exception(RuntimeError("offline test failure"))
    end(rig)
    rig.clock.advance(10)
    assert rig.bridge._task_index == 1


def test_unresolved_tts_request_has_bounded_fallback(rig):
    begin_wait(rig)
    mark(rig)
    reply(rig)
    end(rig)
    rig.clock.advance(rig.module.ARRIVAL_TTS_MAX_WAIT_SEC + 9.99)
    assert rig.bridge._task_index == 0
    rig.clock.advance(0.01)
    assert rig.bridge._task_index == 1


def test_reply_timeout_ack_cannot_extend_the_next_points_wait(rig):
    begin_wait(rig)
    reply(rig)
    rig.clock.advance(rig.module.ARRIVAL_TTS_MAX_WAIT_SEC + 10)
    event = rig.module.NavigationEvent(type=rig.module.NavigationEventType.COMPLETED,
                                       task_id=2, goal_id="p2", point_name="p2", status="completed")
    rig.bridge._begin_task_arrival_action(event)
    rig.bridge._begin_task_wait(event, "next_point")
    timer = rig.bridge._arrival_action_timer
    acknowledge(rig)
    assert rig.bridge._arrival_action_timer is timer
    assert rig.bridge._tour_reply_until == 0.0


def test_wait_status_counts_down_after_estimated_reply_completion(rig):
    begin_wait(rig)
    reply(rig)
    acknowledge(rig)
    assert rig.bridge._task_wait_status_locked()["interaction_active"]
    rig.clock.advance(rig.bridge._tour_reply_until - rig.clock.now + 3)
    status = rig.bridge._task_wait_status_locked()
    assert not status["interaction_active"]
    assert status["remaining_sec"] == 7.0


def test_old_reply_ack_cannot_release_a_new_reply_hold(rig):
    begin_wait(rig)
    reply(rig, event_id="old")
    rig.clock.advance(2)
    reply(rig, event_id="new")
    deadline = rig.bridge._arrival_action_timer.deadline
    acknowledge(rig, index=0)
    assert rig.bridge._arrival_action_timer.deadline == deadline
    acknowledge(rig)
    assert rig.bridge._arrival_action_timer.deadline < deadline


def test_late_reply_ack_does_not_end_new_user_interaction(rig):
    begin_wait(rig)
    mark(rig, "old")
    reply(rig, event_id="old")
    mark(rig, "new")
    acknowledge(rig)
    assert rig.bridge._tour_interaction_event_id == "new"
    assert not rig.bridge._tour_interaction_ended
    assert not end(rig, "old")["ended"]


def test_wait_manual_resume_restarts_full_wait_without_navigation(rig):
    begin_wait(rig)
    rig.clock.advance(8)
    rig.bridge.pause_task({})
    rig.clock.advance(300)
    assert rig.bridge.resume_task({})["resumed"]
    rig.clock.advance(9.99)
    rig.bridge.navigation_client.start.assert_not_called()
    rig.clock.advance(0.01)
    assert rig.bridge._task_index == 1


def test_stale_arrival_timer_cannot_replay_after_pause(rig):
    rig.bridge._schedule_arrival_actions(rig.event, task_arrival=True)
    old_timer = rig.bridge._arrival_action_timer
    wake(rig)
    end(rig)
    old_timer.fire_queued()
    rig.bridge.tts_speaker.speak.assert_not_called()
    rig.clock.advance(5)
    assert rig.bridge.tts_speaker.speak.call_count == 1
    old_timer.fire_queued()
    assert rig.bridge.tts_speaker.speak.call_count == 1


def start_linkcraft(rig):
    rig.bridge._task_steps[0]["linkcraft_action"] = {
        "resource_key": "linkcraft_resource_test", "resource_version": "1",
        "name": "Test resource", "duration_sec": 3,
    }
    rig.bridge._begin_linkcraft_or_advance(rig.event)
    return rig.bridge.motion_controller.execute_action_resource.call_args.kwargs["on_result"]


@pytest.mark.parametrize("event_id", ["voice-1", "", "arrival-old", "nav-problem-old"])
def test_linkcraft_blocks_wake_chat_pause_and_all_tts(rig, event_id):
    start_linkcraft(rig)
    timer = rig.bridge._arrival_action_timer
    assert not wake(rig, event_id)["accepted"]
    assert not mark(rig, event_id)["marked"]
    assert not end(rig, event_id)["ended"]
    assert not rig.bridge.pause_task({})["accepted"]
    assert not reply(rig, event_id=event_id)["accepted"]
    assert rig.bridge._arrival_action_timer is timer
    rig.bridge.tts_speaker.speak.assert_not_called()
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_LINKCRAFT_ACTION


def test_accepted_linkcraft_invalidates_old_submit_timeout(rig):
    on_result = start_linkcraft(rig)
    timeout = rig.bridge._arrival_action_timer
    on_result(True, "ok")
    timer = rig.bridge._arrival_action_timer
    timeout.fire_queued()
    on_result(True, "duplicate")
    assert rig.bridge._arrival_action_timer is timer
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_LINKCRAFT_ACTION
    rig.clock.advance(5)
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_WAITING


def test_linkcraft_retry_ignores_result_from_previous_attempt(rig):
    first = start_linkcraft(rig)
    first(False, "busy")
    rig.clock.advance(rig.module.LINKCRAFT_BUSY_RETRY_DELAY_SEC)
    timer = rig.bridge._arrival_action_timer
    second = rig.bridge.motion_controller.execute_action_resource.call_args.kwargs["on_result"]
    first(True, "late success")
    assert rig.bridge._arrival_action_timer is timer
    second(True, "ok")
    rig.clock.advance(5)
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_WAITING


def test_completed_linkcraft_is_not_replayed_by_arrival_resume(rig):
    start_linkcraft(rig)(True, "ok")
    rig.clock.advance(5)
    assert rig.bridge._task_linkcraft_completed
    # Exercise a retained arrival checkpoint after resource completion.
    rig.bridge._auto_return_state = rig.module.AUTO_RETURN_TASK_ARRIVAL_ACTION
    wake(rig)
    end(rig)
    rig.clock.advance(5)
    acknowledge(rig)
    rig.clock.advance(rig.bridge._arrival_action_timer.deadline - rig.clock.now)
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_WAITING
    assert rig.bridge.motion_controller.execute_action_resource.call_count == 1
    rig.bridge.navigation_client.start.assert_not_called()


def test_arrival_tts_ack_waits_for_playback_before_linkcraft(rig):
    rig.bridge._task_steps[0]["linkcraft_action"] = {
        "resource_key": "linkcraft_resource_test", "resource_version": "1", "duration_sec": 3,
    }
    rig.bridge._schedule_arrival_actions(rig.event, task_arrival=True)
    rig.clock.advance(rig.module.ARRIVAL_SETTLE_SEC)
    rig.clock.advance(15)
    rig.bridge.motion_controller.execute_action_resource.assert_not_called()
    acknowledge(rig)
    rig.clock.advance(rig.bridge._tour_reply_until - rig.clock.now - 0.01)
    rig.bridge.motion_controller.execute_action_resource.assert_not_called()
    rig.clock.advance(0.01)
    assert rig.bridge.motion_controller.execute_action_resource.call_count == 1


@pytest.mark.parametrize("phase", ["paused", "waiting", "linkcraft"])
def test_stop_makes_all_queued_callbacks_and_reply_results_inert(rig, phase):
    if phase == "paused":
        wake(rig)
        reply(rig)
        end(rig)
    elif phase == "waiting":
        begin_wait(rig)
        reply(rig)
    else:
        start_linkcraft(rig)(True, "ok")
    old_timers = list(rig.clock.timers)
    assert rig.bridge.stop_task({})["stopped"]
    for timer in old_timers:
        timer.fire_queued()
    for future in rig.futures:
        future.set_result(SimpleNamespace(tts_resp=SimpleNamespace(is_success=True)))
    rig.clock.advance(300)
    assert rig.bridge._task_steps == []
    rig.bridge.navigation_client.start.assert_not_called()


def test_final_wait_completes_once_and_never_navigates(rig):
    rig.bridge._task_steps = rig.bridge._task_steps[:1]
    begin_wait(rig)
    timer = rig.bridge._arrival_action_timer
    rig.clock.advance(10)
    timer.fire_queued()
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_IDLE
    assert rig.bridge._task_steps == []
    rig.bridge.navigation_client.start.assert_not_called()


def test_consecutive_same_point_uses_actual_arrival_flow(rig):
    rig.bridge._task_steps[1]["point_id"] = "p1"
    begin_wait(rig)
    rig.clock.advance(10)
    assert rig.bridge._task_index == 1
    rig.bridge.navigation_client.start.assert_not_called()
    rig.clock.advance(rig.module.ARRIVAL_SETTLE_SEC)
    assert rig.bridge.tts_speaker.speak.call_args.args[0] == "Point two explanation"


def test_unreachable_task_step_retries_then_skips_and_continues(rig):
    rig.bridge._auto_return_state = rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP
    rig.bridge._task_navigation_task_id = 1
    failed = rig.module.NavigationEvent(
        type=rig.module.NavigationEventType.TIMEOUT,
        task_id=1,
        goal_id="p1",
        point_name="p1",
        status="timeout",
        reason="navigation timeout",
        distance_to_goal=0.8,
    )
    rig.bridge.handle_navigation_event(failed)
    assert rig.bridge._task_status_locked()["failure_recovery"]["pending"]
    assert rig.bridge.navigation_client.start.call_count == 0

    rig.clock.advance(1.0)
    assert rig.bridge.navigation_client.start.call_count == 1
    assert rig.bridge._task_index == 0
    assert rig.bridge._task_status_locked()["failure_recovery"]["retry_count"] == 1

    retry_failed = rig.module.NavigationEvent(
        type=rig.module.NavigationEventType.FAILED,
        task_id=2,
        goal_id="p1",
        point_name="p1",
        status="failed",
        reason="blocked",
        distance_to_goal=0.7,
    )
    rig.bridge.handle_navigation_event(retry_failed)
    rig.clock.advance(1.0)
    assert rig.bridge._task_index == 1
    assert rig.bridge.navigation_client.start.call_count == 2
    assert rig.bridge._task_status_locked()["failure_recovery"]["skipped_steps"][0]["point_id"] == "p1"


def test_retries_of_a_later_step_always_submit_navigation(rig):
    rig.bridge._start_task_step(1)
    rig.bridge.navigation_client.start.reset_mock()
    failed = rig.module.NavigationEvent(
        type=rig.module.NavigationEventType.TIMEOUT,
        task_id=2, goal_id="p2", point_name="p2", status="timeout",
        reason="not reached", distance_to_goal=0.9,
    )
    rig.bridge.handle_navigation_event(failed)
    rig.clock.advance(1.0)
    rig.bridge.navigation_client.start.assert_called_once()
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP
    rig.bridge.tts_speaker.speak.assert_not_called()


def test_skipped_point_cannot_be_reused_as_a_successful_arrival(rig):
    rig.bridge._task_steps.append(dict(rig.bridge._task_steps[1]))
    rig.bridge._start_task_step(1)
    for event_type in (rig.module.NavigationEventType.TIMEOUT, rig.module.NavigationEventType.FAILED):
        rig.bridge.handle_navigation_event(rig.module.NavigationEvent(
            type=event_type, task_id=2, goal_id="p2", point_name="p2",
            status=event_type.value, reason="not reached", distance_to_goal=0.9,
        ))
        rig.clock.advance(1.0)
    assert rig.bridge._task_index == 2
    assert rig.bridge.navigation_client.start.call_count == 3
    assert rig.bridge._auto_return_state == rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP
    rig.bridge.tts_speaker.speak.assert_not_called()
