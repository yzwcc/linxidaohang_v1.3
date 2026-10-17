from test.test_tour_bridge_behavior import bridge_module, rig


def test_failed_step_cannot_reuse_unreached_same_point(rig):
    bridge = rig.bridge
    bridge._task_failed_steps = []
    bridge._task_steps = [{"point_id": "p1"}, {"point_id": "p1"}]
    bridge._task_last_goal_id = ""
    bridge.navigation_client.start.side_effect = None
    bridge.navigation_client.start.return_value = rig.module.NavigationSessionHandle(
        task_id=100, goal_id="p1", point_name="p1", accepted=True, status="started"
    )
    bridge._start_task_step(0)
    bridge._continue_task_after_navigation_failure(rig.module.NavigationEvent(
        type=rig.module.NavigationEventType.FAILED, task_id=100,
        goal_id="p1", point_name="p1", status="failed", reason="blocked"
    ))
    rig.clock.advance(rig.module.TASK_FAILURE_ADVANCE_DELAY_SEC)
    assert bridge.navigation_client.start.call_count == 2
    assert bridge._task_index == 1


def test_permanent_start_rejection_does_not_stick_forever(rig):
    bridge = rig.bridge
    bridge._task_failed_steps = []
    bridge._task_last_goal_id = ""
    bridge.navigation_client.start.side_effect = None
    bridge.navigation_client.start.return_value = rig.module.NavigationSessionHandle(
        task_id=None, goal_id="p1", point_name="p1", accepted=False,
        status="invalid_request", reason="invalid"
    )
    bridge._start_task_step(0)
    assert len(bridge._task_failed_steps) == 1
    rig.clock.advance(rig.module.TASK_FAILURE_ADVANCE_DELAY_SEC)
    assert bridge.navigation_client.start.call_count == 2
