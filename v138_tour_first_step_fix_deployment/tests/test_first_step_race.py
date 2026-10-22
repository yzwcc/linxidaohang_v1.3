from __future__ import annotations

from collections import namedtuple
from concurrent.futures import Future
import importlib.util
import os
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest


HERE = Path(__file__).resolve().parent.parent
NAV_SOURCE = Path(os.environ.get("LINGXI_REFERENCE_ROOT", HERE / "reference")) / "navigation"
sys.path.insert(0, str(NAV_SOURCE))


class FakeTimer:
    def __init__(self, callback):
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.callback()


class FakeNavigation:
    def __init__(self):
        self.calls = []
        self.next_id = 100
        self.start_behavior = None

    def reserve_task_id(self):
        self.next_id += 1
        return self.next_id

    def get_status(self):
        return {"status": "idle"}

    def start(self, point, **kwargs):
        self.calls.append((point, kwargs))
        if self.start_behavior:
            return self.start_behavior(point, kwargs)
        return HANDLE(
            task_id=kwargs.get("task_id"),
            goal_id=point.id,
            point_name=point.name,
            accepted=True,
            status="started",
            reason="",
        )


HANDLE = namedtuple(
    "NavigationSessionHandle",
    "task_id goal_id point_name accepted status reason",
    defaults=[""],
)


@pytest.fixture
def bridge_module():
    def stub(name, **attributes):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        return module

    def forbidden(*args, **kwargs):
        raise AssertionError("hardware adapters must not be constructed in these tests")

    prefix = "robot_local_navigation_v3."
    stubs = {
        "rclpy": stub("rclpy"),
        "rclpy.node": stub("rclpy.node", Node=object),
        prefix + "navigation_client": stub(
            prefix + "navigation_client",
            NavigationClient=forbidden,
            NavigationSessionHandle=HANDLE,
        ),
        prefix + "sdk_motion_controller": stub(
            prefix + "sdk_motion_controller",
            DEFAULT_EMOJI_PRIORITY=0,
            SdkMotionController=forbidden,
        ),
        prefix + "tts_speaker": stub(prefix + "tts_speaker", TtsSpeaker=forbidden),
    }
    path = Path(os.environ.get(
        "LINGXI_BRIDGE_SOURCE",
        HERE / "source/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py",
    ))
    spec = importlib.util.spec_from_file_location(prefix + "_first_step_test_bridge", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def rig(bridge_module):
    module = bridge_module
    bridge = module.LingxiNavBridge.__new__(module.LingxiNavBridge)
    bridge.get_logger = Mock(return_value=Mock())
    bridge._auto_return_lock = threading.RLock()
    bridge._auto_return_state = module.AUTO_RETURN_IDLE
    bridge._auto_return_generation = 0
    bridge._task_run_generation = 0
    bridge._task_step_generation = 0
    bridge._arrival_action_timer = None
    bridge._auto_return_timer = None
    bridge._task_id = "tour"
    bridge._task_name = "Test tour"
    bridge._task_steps = [
        {"point_id": "p1", "speech_text": "First"},
        {"point_id": "p2", "speech_text": "Second"},
    ]
    bridge._task_failed_steps = []
    bridge._task_index = -1
    bridge._task_current_goal_id = ""
    bridge._task_last_goal_id = ""
    bridge._task_navigation_session_id = None
    bridge._task_navigation_retry_count = 0
    bridge._task_cancel_requested = False
    bridge._task_resume_index = -1
    bridge._task_pause_origin = ""
    bridge._task_interruption_wait_started_at = 0.0
    bridge._task_paused_phase = ""
    bridge._task_arrival_event = None
    bridge._task_arrival_effects_until = 0.0
    bridge._task_linkcraft_completed = False
    bridge._task_wait_started_at = 0.0
    bridge._task_wait_sec = 0.0
    bridge._task_paused_wait_sec = 0.0
    bridge._task_wait_interaction_active = False
    bridge._tour_reply_generation = 0
    bridge._tour_reply_until = 0.0
    bridge._tour_interaction_event_id = ""
    bridge._tour_interaction_ended = True
    bridge._tour_quiet_since = 100.0
    bridge._auto_return_source_goal_id = "p1"
    bridge._auto_return_target_id = ""
    bridge._auto_return_reason = ""
    bridge._task_silent_receipts = False
    bridge._tts_lock = threading.Lock()
    bridge._last_tts_at = 0.0
    bridge._tts_min_interval_sec = 0.0
    bridge.tts_speaker = SimpleNamespace(speak=Mock(return_value=Future()))
    bridge._finish_arrival_motion_after_wait = Mock(
        return_value={"ok": True, "release_guard_sec": 0.0}
    )
    bridge._restore_looping_arrival_emoji = Mock()
    timers = []
    bridge.create_timer = lambda _period, callback: timers.append(FakeTimer(callback)) or timers[-1]
    points = {
        point_id: SimpleNamespace(id=point_id, name=point_id)
        for point_id in ("p1", "p2")
    }

    def get_point(point_id):
        try:
            return points[point_id]
        except KeyError as exc:
            raise module.PointCatalogError(f"unknown point: {point_id}") from exc

    bridge.point_catalog = SimpleNamespace(get_by_id=get_point)
    bridge.navigation_client = FakeNavigation()
    bridge._mark_valid_interaction = Mock()
    bridge._task_status_locked = Mock(return_value={"active": True})
    bridge.timers = timers
    return SimpleNamespace(module=module, bridge=bridge, timers=timers)


def event(module, *, task_id, goal_id="p1", event_type=None, status="completed", reason=""):
    return module.NavigationEvent(
        type=event_type or module.NavigationEventType.COMPLETED,
        task_id=task_id,
        goal_id=goal_id,
        point_name=goal_id,
        status=status,
        reason=reason,
    )


def test_delayed_callback_from_old_run_cannot_complete_reused_point_or_session(rig):
    bridge = rig.bridge
    bridge.navigation_client.start_behavior = lambda point, kwargs: HANDLE(
        task_id=700,
        goal_id=point.id,
        point_name=point.name,
        accepted=True,
        status="started",
        reason="",
    )

    bridge._start_task_step(0, resumed=True)
    old_listener = bridge.navigation_client.calls[-1][1]["listener"]
    old_run = bridge._task_run_generation

    bridge._task_run_generation += 1
    bridge._task_step_generation = 0
    bridge._start_task_step(0, resumed=True)
    new_listener = bridge.navigation_client.calls[-1][1]["listener"]
    assert bridge._task_run_generation != old_run

    old_listener(event(rig.module, task_id=700, event_type=rig.module.NavigationEventType.FAILED,
                       status="failed", reason="late old-run callback"))

    assert bridge._task_index == 0
    assert bridge._task_current_goal_id == "p1"
    assert bridge._auto_return_state == rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP
    assert bridge._task_failed_steps == []
    assert len(bridge.navigation_client.calls) == 2

    new_listener(event(rig.module, task_id=700))
    assert bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_ARRIVAL_ACTION


def test_rejected_attempt_is_invalidated_before_retry_timer_fires(rig):
    bridge = rig.bridge
    attempts = []

    def reject_once(point, kwargs):
        attempts.append(point.id)
        if len(attempts) == 1:
            return HANDLE(None, point.id, point.name, False, "invalid_request", "temporary")
        return HANDLE(kwargs["task_id"], point.id, point.name, True, "started", "")

    bridge.navigation_client.start_behavior = reject_once
    result = bridge._start_task_step(0)
    old_listener = bridge.navigation_client.calls[0][1]["listener"]

    assert result.status == "waiting_for_navigation"
    assert bridge._task_index == 0
    assert bridge._task_failed_steps == []

    old_listener(event(rig.module, task_id=101, event_type=rig.module.NavigationEventType.COMPLETED))
    assert bridge._auto_return_state == rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP
    assert bridge._task_index == 0

    rig.timers[-1].fire()
    assert attempts == ["p1", "p1"]
    assert bridge._task_index == 0
    assert bridge._task_navigation_session_id == 102


def test_repeated_start_rejections_pause_on_first_step_without_advancing(rig):
    bridge = rig.bridge
    bridge.navigation_client.start_behavior = lambda point, _kwargs: HANDLE(
        None, point.id, point.name, False, "invalid_request", "not accepted"
    )

    result = bridge._start_task_step(0)
    assert result.status == "waiting_for_navigation"
    while rig.timers:
        timer = rig.timers.pop(0)
        if not timer.cancelled:
            timer.fire()
        if bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_PAUSED:
            break

    assert bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_PAUSED
    assert bridge._task_index == 0
    assert bridge._task_resume_index == 0
    assert bridge._task_failed_steps == []


def test_enabled_task_step_without_point_id_is_rejected_not_filtered(rig):
    bridge = rig.bridge
    bridge.active_map_name = "map"
    bridge.point_store = SimpleNamespace(load_tour_tasks=lambda _map: {
        "active_task": {
            "id": "tour",
            "name": "Test tour",
            "steps": [
                {"enabled": True, "point_id": ""},
                {"enabled": True, "point_id": "p2"},
            ],
        },
    })

    with pytest.raises(rig.module.PointCatalogError, match="step 1"):
        bridge._load_tour_task_points()


def test_concurrent_start_requests_claim_only_one_task(rig):
    bridge = rig.bridge
    barrier = threading.Barrier(2)
    steps = [{"point_id": "p1", "speech_text": "First"}]

    def load_task(_task_id):
        barrier.wait(timeout=3)
        return "tour", "Test tour", steps

    bridge._load_tour_task_points = load_task
    results = []
    errors = []

    def start(event_id):
        try:
            results.append(bridge.start_task({"event_id": event_id}))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=start, args=(f"event-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(result["status"] == "busy" for result in results) == [False, True]
    assert len(bridge.navigation_client.calls) == 1


def test_start_task_always_sends_first_enabled_point_first(rig):
    bridge = rig.bridge
    bridge._auto_return_state = rig.module.AUTO_RETURN_IDLE
    bridge._task_index = 1
    steps = list(bridge._task_steps)
    bridge._load_tour_task_points = Mock(return_value=("tour", "Test tour", steps))
    result = bridge.start_task({"event_id": "start-first"})
    assert result["accepted"]
    assert [call[0].id for call in bridge.navigation_client.calls] == ["p1"]
    assert bridge._task_index == 0


@pytest.mark.parametrize("status", ["busy", "cancelled", "transport_error", "invalid_request"])
def test_synchronous_rejection_schedules_one_retry_of_same_point(rig, status):
    bridge = rig.bridge

    def reject(point, kwargs):
        kwargs["listener"](event(
            rig.module, task_id=None, goal_id=point.id,
            event_type=rig.module.NavigationEventType.REJECTED, status=status,
        ))
        return HANDLE(None, point.id, point.name, False, status, status)

    bridge.navigation_client.start_behavior = reject
    result = bridge._start_task_step(0)
    assert result.status == "waiting_for_navigation"
    assert bridge._task_navigation_retry_count == 1
    assert bridge._task_index == 0
    assert bridge._task_failed_steps == []
    assert len(rig.timers) == 1


def test_synchronous_accept_and_complete_cannot_be_undone_by_start_return(rig):
    bridge = rig.bridge

    def complete(point, kwargs):
        for kind in (rig.module.NavigationEventType.ACCEPTED,
                     rig.module.NavigationEventType.STARTED,
                     rig.module.NavigationEventType.COMPLETED):
            kwargs["listener"](event(
                rig.module, task_id=kwargs["task_id"], goal_id=point.id, event_type=kind,
            ))
        return HANDLE(kwargs["task_id"], point.id, point.name, True, "started", "")

    bridge.navigation_client.start_behavior = complete
    bridge._start_task_step(0)
    assert bridge._auto_return_state == rig.module.AUTO_RETURN_TASK_ARRIVAL_ACTION
    assert bridge._task_index == 0
    assert bridge._task_arrival_event.goal_id == "p1"


@pytest.mark.parametrize("task_id", [None, "bad", 999, "101.0"])
def test_unmatched_completion_cannot_advance_current_step(rig, task_id):
    bridge = rig.bridge
    bridge._start_task_step(0)
    listener = bridge.navigation_client.calls[-1][1]["listener"]
    listener(event(rig.module, task_id=task_id))
    listener(event(rig.module, task_id=101, goal_id="p2"))
    assert bridge._auto_return_state == rig.module.AUTO_RETURN_NAVIGATING_TASK_STEP
    assert bridge._task_index == 0


def test_old_result_cannot_schedule_retry_over_a_new_attempt(rig):
    bridge = rig.bridge

    def replace_attempt(point, kwargs):
        bridge.navigation_client.start_behavior = None
        bridge._start_task_step(0, resumed=True)
        return HANDLE(kwargs["task_id"], point.id, point.name, False, "cancelled", "old")

    bridge.navigation_client.start_behavior = replace_attempt
    bridge._start_task_step(0)
    assert bridge._task_navigation_session_id == 102
    assert bridge._task_navigation_retry_count == 0
    assert not rig.timers


@pytest.mark.parametrize("event_type", ["FAILED", "TIMEOUT"])
def test_real_navigation_failure_still_continues_remaining_tour(rig, event_type):
    bridge = rig.bridge
    bridge._start_task_step(0)
    listener = bridge.navigation_client.calls[-1][1]["listener"]
    listener(event(
        rig.module, task_id=101, event_type=getattr(rig.module.NavigationEventType, event_type),
        status=event_type.lower(), reason="real accepted navigation failure",
    ))
    assert len(bridge._task_failed_steps) == 1
    rig.timers[-1].fire()
    assert [call[0].id for call in bridge.navigation_client.calls] == ["p1", "p2"]
    assert bridge._task_index == 1


def test_generation_change_before_step_initialization_has_no_effect(rig):
    bridge = rig.bridge
    bridge._auto_return_generation = 10
    result = bridge._start_task_step(1, expected_generation=9)
    assert result.status == "stale_task_step"
    assert not bridge.navigation_client.calls
    bridge._finish_arrival_motion_after_wait.assert_not_called()
    assert bridge._task_index == -1


def test_cancelled_queued_retry_cannot_restart_a_new_run(rig):
    bridge = rig.bridge
    bridge.navigation_client.start_behavior = lambda point, kwargs: HANDLE(
        None, point.id, point.name, False, "busy", "",
    )
    bridge._start_task_step(0)
    old_timer = rig.timers[-1]
    bridge._auto_return_generation += 1
    bridge._task_run_generation += 1
    bridge.navigation_client.start_behavior = None
    bridge._start_task_step(0)
    old_timer.fire()
    assert len(bridge.navigation_client.calls) == 2
    assert bridge._task_index == 0


@pytest.mark.parametrize("disabled", [False, True])
def test_task_loader_validates_missing_point_and_preserves_enabled_order(rig, disabled):
    bridge = rig.bridge
    bridge.active_map_name = "map"
    bridge.point_store = SimpleNamespace(load_tour_tasks=lambda _map: {
        "active_task": {"id": "tour", "steps": [
            {"point_id": "missing", "enabled": not disabled},
            {"point_id": "p1"}, {"point_id": "p2"},
        ]},
    })
    if not disabled:
        with pytest.raises(rig.module.PointCatalogError, match="step 1"):
            bridge._load_tour_task_points()
    else:
        assert [step["point_id"] for step in bridge._load_tour_task_points()[2]] == ["p1", "p2"]


def test_callback_dispatch_keeps_validation_and_state_transition_together(rig):
    bridge = rig.bridge
    bridge._start_task_step(0)
    listener = bridge.navigation_client.calls[-1][1]["listener"]
    ownership = []
    bridge.handle_navigation_event = lambda _event: ownership.append(
        bridge._auto_return_lock._is_owned()
    )
    listener(event(rig.module, task_id=101))
    assert ownership == [True]
