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
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _enum_value(value: Any) -> int | None:
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


def classify_pnc_response_support_error(error: BaseException) -> str:
    """Classify generated-message failures so permanent errors are not retried."""

    if isinstance(error, (AttributeError, ImportError, ModuleNotFoundError)):
        return "unsupported"
    message = str(error).lower()
    permanent_markers = (
        "has no attribute",
        "no module named",
        "unknown message type",
        "unsupported type support",
        "could not import",
    )
    return "unsupported" if any(marker in message for marker in permanent_markers) else "transient"


def claim_bounded_retry(attempts: int, limit: int) -> tuple[bool, int]:
    """Return one retry slot, or refuse without increasing beyond the limit."""

    current = max(0, int(attempts))
    maximum = max(0, int(limit))
    if current >= maximum:
        return False, current
    return True, current + 1


def parse_pnc_task_response(message: Any) -> dict[str, Any]:
    """Parse terminal PNC state without treating header.code=0 as arrival."""

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
