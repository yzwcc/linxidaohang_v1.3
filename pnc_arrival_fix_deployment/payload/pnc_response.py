from __future__ import annotations

from typing import Any


PNC_STATE_SUCCESS = 1
PNC_STATE_FAILURE = 2
PNC_STATE_ABORTED = 3
PNC_STATE_TIMEOUT = 4
PNC_STATE_INVALID = 5
PNC_STATE_IN_MANUAL = 6
PNC_STATE_NOT_READY = 100
PNC_STATE_PENDING = 200
PNC_STATE_CREATED = 300
PNC_STATE_RUNNING = 400


def _coerce_int(value: Any) -> int | None:
    """将 ROS 枚举字段或普通值转换为整数，无法转换时返回 None。"""

    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _enum_value(value: Any) -> int | None:
    """读取 CommonState.value，同时兼容旧版本直接返回整数的状态字段。"""

    if value is None:
        return None
    nested = getattr(value, "value", None)
    return _coerce_int(value if nested is None else nested)


def _enum_text(value: Any) -> str:
    for attr in ("name", "label", "status"):
        text = getattr(value, attr, None)
        if text is not None and not callable(text):
            normalized = str(text).strip().lower()
            if normalized:
                return normalized
    return str(value).strip().lower() if value is not None else ""


def parse_pnc_task_response(message: Any) -> dict[str, Any]:
    """解析 PNC/CommonTaskResponse，返回可审计的状态分类。

    CommonTaskResponse 的 state.value=1 才代表成功；header.code=0 仅代表
    消息处理成功，不能单独当作导航到达。部分旧 PNC 版本将完成状态放在
    task_status=4 中，因此仅在字段名明确为 task_status 时兼容该值。
    """

    task_id = _coerce_int(getattr(message, "task_id", None))
    state = getattr(message, "state", None)
    state_value = _enum_value(state)
    state_text = _enum_text(state)
    task_status = getattr(message, "task_status", None)
    task_status_value = _enum_value(task_status)
    status_text = " ".join(
        text
        for text in (state_text, _enum_text(task_status))
        if text and text not in {"0", "none"}
    )

    if state_value == PNC_STATE_SUCCESS:
        disposition = "completed"
    elif state_value in {
        PNC_STATE_FAILURE,
        PNC_STATE_ABORTED,
        PNC_STATE_TIMEOUT,
        PNC_STATE_INVALID,
        PNC_STATE_IN_MANUAL,
        PNC_STATE_NOT_READY,
    }:
        disposition = "failed"
    elif state_value in {PNC_STATE_PENDING, PNC_STATE_CREATED, PNC_STATE_RUNNING}:
        disposition = "running"
    elif task_status_value == 4 and hasattr(message, "task_status"):
        disposition = "completed"
    elif any(token in status_text for token in ("success", "completed", "complete", "done", "finish")):
        disposition = "completed"
    elif any(token in status_text for token in ("failure", "failed", "abort", "timeout", "invalid", "manual")):
        disposition = "failed"
    else:
        disposition = "unknown"

    return {
        "task_id": task_id,
        "state_value": state_value,
        "state_text": state_text,
        "task_status_value": task_status_value,
        "disposition": disposition,
        "header_code": _coerce_int(getattr(getattr(message, "header", None), "code", None)),
    }
