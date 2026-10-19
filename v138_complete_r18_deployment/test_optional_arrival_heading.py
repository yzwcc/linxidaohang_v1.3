"""Cover supported yaw-check opt-out at the policy and real-session boundaries."""
from types import SimpleNamespace as NS
import threading
from unittest.mock import Mock, patch

import pytest

from robot_local_navigation_v3.near_goal_arrival import NearGoalArrival
from test.test_near_goal_arrival import odom


def test_disabled_heading_still_completes_after_full_stationary_dwell():
    policy = NearGoalArrival()
    sample = NS(distance_to_goal=.424, yaw_error=None, linear_speed_mps=0.0)
    for index in range(26):
        now = 100 + index * .2
        result = policy.update(odom(now), sample, now=now, ros_now_sec=now,
                               yaw_tolerance=None, stop_enabled=True)
        assert result is (index == 25)


@pytest.mark.parametrize("unsafe", ["moving", "rotating", "stale", "invalid_pose", "stop_disabled"])
def test_disabled_heading_keeps_all_other_evidence_requirements(unsafe):
    policy = NearGoalArrival()
    sample = NS(distance_to_goal=.424, yaw_error=None, linear_speed_mps=0.0)
    for index in range(40):
        now = 100 + index * .2
        msg = odom(now)
        if unsafe == "moving":
            msg.twist.twist.linear.x = .1
        elif unsafe == "rotating":
            msg.twist.twist.angular.z = .2
        elif unsafe == "invalid_pose":
            msg.pose.pose.orientation.w = 0.0
        assert not policy.update(msg, sample, now=now,
                                 ros_now_sec=now + (10 if unsafe == "stale" else 0),
                                 yaw_tolerance=None, stop_enabled=unsafe != "stop_disabled")


def test_missing_heading_is_rejected_when_it_is_required():
    policy = NearGoalArrival()
    sample = NS(distance_to_goal=.424, yaw_error=None, linear_speed_mps=0.0)
    for index in range(40):
        now = 100 + index * .2
        assert not policy.update(odom(now), sample, now=now, ros_now_sec=now,
                                 yaw_tolerance=.1745, stop_enabled=True)


def test_actual_navigation_session_with_disabled_yaw_finishes_once():
    from test.test_pnc_discovery import navigation_client as module
    from robot_local_navigation_v3.models import NavigationDefaults
    clock = [100.0]
    defaults = NavigationDefaults()
    defaults.arrival_policy.position_tolerance_m = .15
    defaults.arrival_policy.yaw_tolerance_rad = None
    defaults.stop_policy.hold_sec = 0
    node = Mock()
    node.get_clock.return_value.now.side_effect = lambda: NS(nanoseconds=int(clock[0] * 1e9))
    point = NS(id="p", name="optional-heading", yaw=0.0,
               pose=NS(position=NS(x=0.0, y=0.0)), navigation=NS(target_pose_radius=.15))
    finished, order = threading.Event(), []
    session = module.NavigationSession(
        node=node, publisher=Mock(), defaults=defaults, point=point,
        listener=lambda event: order.append(event.type), mode="execute",
        republish_period_sec=1, progress_period_sec=1, timeout_sec=120,
        allow_initial_arrival_hysteresis=False, on_finished=lambda _: finished.set(), task_id=123,
    )
    session._status = "navigating"
    session._send_stop = Mock(side_effect=lambda **_: order.append("stop"))
    with patch.object(module.time, "monotonic", side_effect=lambda: clock[0]):
        for index in range(27):
            clock[0] = 100 + index * .2
            session.handle_odom(odom(clock[0]))
        assert finished.wait(1)
        session._on_timeout()
        session.handle_pnc_response({"task_id": 123, "disposition": "failed"})
    assert session._status == "completed"
    session._send_stop.assert_called_once()
    assert order.count(module.NavigationEventType.COMPLETED) == 1
    assert order.index("stop") < order.index(module.NavigationEventType.COMPLETED)
