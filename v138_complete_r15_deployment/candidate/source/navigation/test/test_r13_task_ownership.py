from unittest.mock import Mock

from test.test_tour_bridge_behavior import bridge_module, rig


def test_cancelled_old_start_cannot_fail_a_resumed_task(rig):
    bridge, module = rig.bridge, rig.module
    bridge._task_failed_steps = []
    bridge._reserve_navigation_task_id = Mock(side_effect=[101, 102])

    def start(point, **kwargs):
        if kwargs["task_id"] == 101:
            bridge.pause_task({})
            assert bridge._start_task_step(0, resumed=True).accepted
            return module.NavigationSessionHandle(
                task_id=None, goal_id=point.id, point_name=point.name,
                accepted=False, status="cancelled", reason="cancelled during preflight",
            )
        return module.NavigationSessionHandle(
            task_id=102, goal_id=point.id, point_name=point.name,
            accepted=True, status="started",
        )

    bridge.navigation_client.start.side_effect = start
    bridge._start_task_step(0)
    assert bridge._task_navigation_session_id == 102
    assert bridge._task_failed_steps == []
    assert bridge._auto_return_state == module.AUTO_RETURN_NAVIGATING_TASK_STEP
    rig.clock.advance(module.TASK_FAILURE_ADVANCE_DELAY_SEC + 0.1)
    assert bridge._task_index == 0


def test_busy_rejection_at_same_goal_preserves_original_user_session(rig):
    bridge, module = rig.bridge, rig.module
    bridge._begin_user_navigation("p1", 101)
    bridge.point_catalog.resolve = Mock(return_value=Mock(point=bridge.point_catalog.get_by_id("p1"), matched_by="name"))
    bridge._reserve_navigation_task_id = Mock(return_value=102)
    bridge.navigation_client.get_status.return_value = {"status": "navigating"}
    bridge.navigation_client.start.side_effect = None
    bridge.navigation_client.start.return_value = module.NavigationSessionHandle(
        task_id=None, goal_id="p1", point_name="p1", accepted=False, status="busy"
    )
    bridge._cancel_task_for_new_intent = Mock(return_value=False)
    assert not bridge.start_navigation({"target_name": "p1"})["accepted"]
    assert bridge._auto_return_state == module.AUTO_RETURN_NAVIGATING_USER_TARGET
    assert bridge._user_navigation_session_id == 101
