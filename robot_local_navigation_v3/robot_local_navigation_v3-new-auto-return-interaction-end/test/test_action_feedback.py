from robot_local_navigation_v3.action_feedback import with_rejection_message


def test_rejected_navigation_reason_is_mirrored_to_legacy_message_field():
    payload = {
        "ok": False,
        "accepted": False,
        "status": "path_safety_rejected",
        "reason": "尚未配置机器人实测外接半径，已拒绝下发导航",
    }

    result = with_rejection_message(payload)

    assert result == {**payload, "message": payload["reason"]}
    assert payload == {
        "ok": False,
        "accepted": False,
        "status": "path_safety_rejected",
        "reason": "尚未配置机器人实测外接半径，已拒绝下发导航",
    }


def test_existing_message_is_not_overwritten():
    payload = {"ok": False, "accepted": False, "message": "specific message", "reason": "reason"}
    assert with_rejection_message(payload) is payload


def test_accepted_actions_are_unchanged():
    payload = {"ok": True, "accepted": True, "status": "started"}
    assert with_rejection_message(payload) is payload


def test_rejection_without_reason_is_unchanged():
    payload = {"ok": False, "accepted": False, "status": "rejected"}
    assert with_rejection_message(payload) is payload
