from types import SimpleNamespace as NS
import math
import threading
from unittest.mock import Mock, patch

import pytest

from robot_local_navigation_v3.near_goal_arrival import NearGoalArrival


def odom(t, distance=0.424, speed=0.0, yaw=0.06, angular=0.0):
    return NS(
        header=NS(stamp=NS(sec=int(t), nanosec=round((t - int(t)) * 1e9))),
        pose=NS(pose=NS(position=NS(x=distance, y=0.0, z=0.0),
                        orientation=NS(x=0.0, y=0.0, z=math.sin(yaw / 2), w=math.cos(yaw / 2)))),
        twist=NS(twist=NS(linear=NS(x=speed, y=0.0, z=0.0),
                          angular=NS(x=0.0, y=0.0, z=angular))),
    )


def feed(policy, t, *, distance=0.424, speed=0.0, yaw=0.06, angular=0.0,
         stamp=None, ros_now=None, stop=True):
    msg = odom(t if stamp is None else stamp, distance, speed, yaw, angular)
    sample = NS(distance_to_goal=distance, yaw_error=yaw, linear_speed_mps=abs(speed))
    return policy.update(msg, sample, now=t, ros_now_sec=t if ros_now is None else ros_now,
                         yaw_tolerance=0.2, stop_enabled=stop)


@pytest.mark.parametrize("distance", [0.3589467, 0.4241447, 0.45])
def test_field_stopped_traces_require_full_five_second_dwell(distance):
    policy = NearGoalArrival()
    for i in range(25):
        assert not feed(policy, 100 + i * 0.2, distance=distance)
    assert feed(policy, 105, distance=distance)


@pytest.mark.parametrize("kwargs", [
    {"distance": 0.451}, {"speed": 0.051}, {"yaw": 0.201}, {"angular": 0.081},
    {"stamp": 99.0}, {"stamp": 0.0}, {"ros_now": 110.0}, {"stop": False},
    {"distance": float("nan")}, {"yaw": float("nan")}, {"speed": float("inf")},
])
def test_invalid_or_unsafe_samples_never_complete(kwargs):
    policy = NearGoalArrival()
    for i in range(60):
        assert not feed(policy, 100 + i * 0.2, **kwargs)


@pytest.mark.parametrize("break_kind", ["gap", "backwards", "replay", "drift", "motion", "rotation"])
def test_interrupted_evidence_starts_a_new_full_dwell(break_kind):
    policy = NearGoalArrival()
    for i in range(25):
        assert not feed(policy, 100 + i * 0.2)
    if break_kind == "gap":
        assert not feed(policy, 106)
    elif break_kind == "backwards":
        assert not feed(policy, 104)
    elif break_kind == "replay":
        assert not feed(policy, 105, stamp=104.8)
    elif break_kind == "drift":
        assert not feed(policy, 105, distance=0.37)
    elif break_kind == "motion":
        assert not feed(policy, 105, speed=0.1)
    else:
        assert not feed(policy, 105, angular=0.1)
    assert not feed(policy, 106.2)


def test_frozen_stamp_and_invalid_quaternion_cannot_accumulate_dwell():
    policy = NearGoalArrival()
    for i in range(60):
        assert not feed(policy, 100 + i * 0.05, stamp=100)
    msg = odom(200)
    msg.pose.pose.orientation.w = 0
    sample = NS(distance_to_goal=0.4, yaw_error=0.0, linear_speed_mps=0.0)
    assert not policy.update(msg, sample, now=200, ros_now_sec=200, yaw_tolerance=.2, stop_enabled=True)


def test_disable_switch(monkeypatch):
    monkeypatch.setenv("NAV_NEAR_GOAL_ENABLED", "0")
    for i in range(60):
        assert not feed(NearGoalArrival(), 100 + i * .2)


def test_real_session_completes_once_via_stop_before_listener_and_ignores_late_failure():
    from test.test_pnc_discovery import navigation_client as module
    from robot_local_navigation_v3.models import NavigationDefaults
    clock = [100.0]
    defaults = NavigationDefaults()
    defaults.arrival_policy.position_tolerance_m = 0.15
    defaults.stop_policy.hold_sec = 0
    node = Mock()
    node.get_clock.return_value.now.side_effect = lambda: NS(nanoseconds=int(clock[0] * 1e9))
    point = NS(id="p", name="field-point", yaw=0.0, pose=NS(position=NS(x=0.0, y=0.0)),
               navigation=NS(target_pose_radius=.15))
    finished, order = threading.Event(), []
    session = module.NavigationSession(
        node=node, publisher=Mock(), defaults=defaults, point=point,
        listener=lambda e: order.append(e.type), mode="execute", republish_period_sec=1,
        progress_period_sec=1, timeout_sec=120, allow_initial_arrival_hysteresis=False,
        on_finished=lambda _: finished.set(), task_id=123,
    )
    session._status = "navigating"
    session._send_stop = Mock(side_effect=lambda **kw: order.append("stop"))
    with patch.object(module.time, "monotonic", side_effect=lambda: clock[0]):
        for i in range(27):
            clock[0] = 100 + i * .2
            session.handle_odom(odom(clock[0], distance=.4241447))
        assert finished.wait(2)
        session.handle_pnc_response({"task_id": 123, "disposition": "failed"})
        session._on_timeout()
        session.handle_odom(odom(106))
    assert session._status == "completed"
    session._send_stop.assert_called_once()
    assert order.count(module.NavigationEventType.COMPLETED) == 1
    assert order.index("stop") < order.index(module.NavigationEventType.COMPLETED)


def test_cancelled_session_never_accepts_near_goal():
    from test.test_pnc_discovery import navigation_client as module
    session = module.NavigationSession.__new__(module.NavigationSession)
    session._lock = threading.RLock()
    session._status = "cancelled"
    session._complete_async = Mock()
    session.handle_odom(odom(100))
    session._complete_async.assert_not_called()
