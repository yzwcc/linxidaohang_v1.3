from __future__ import annotations

from robot_local_navigation_v3.pnc_response import (
    claim_bounded_retry,
    classify_pnc_response_support_error,
    parse_pnc_task_response,
)


class State:
    def __init__(self, value: int):
        self.value = value


class Header:
    code = 0


class Response:
    header = Header()

    def __init__(self, task_id: int, state: int):
        self.task_id = task_id
        self.state = State(state)


def test_common_task_response_success_is_completed():
    result = parse_pnc_task_response(Response(123, 1))
    assert result["task_id"] == 123
    assert result["disposition"] == "completed"


def test_header_success_code_alone_is_not_arrival():
    result = parse_pnc_task_response(Response(123, 0))
    assert result["header_code"] == 0
    assert result["disposition"] == "unknown"


def test_missing_generated_pnc_message_is_permanently_unsupported():
    error = AttributeError("module 'aimdk_msgs.msg' has no attribute 'PncTaskResponse'")
    assert classify_pnc_response_support_error(error) == "unsupported"
    assert classify_pnc_response_support_error(RuntimeError("ROS graph temporarily unavailable")) == "transient"


def test_pnc_discovery_retry_budget_is_strictly_bounded():
    attempts = 0
    for _ in range(15):
        allowed, attempts = claim_bounded_retry(attempts, 15)
        assert allowed
    allowed, attempts = claim_bounded_retry(attempts, 15)
    assert not allowed
    assert attempts == 15
