from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))

from pnc_response import parse_pnc_task_response


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


def test_common_task_response_timeout_is_not_completed():
    result = parse_pnc_task_response(Response(123, 4))
    assert result["disposition"] == "failed"


def test_header_success_code_alone_is_not_arrival():
    response = Response(123, 0)
    result = parse_pnc_task_response(response)
    assert result["header_code"] == 0
    assert result["disposition"] == "unknown"


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"通过 {len(tests)} 项 PNC 回验解析测试")
