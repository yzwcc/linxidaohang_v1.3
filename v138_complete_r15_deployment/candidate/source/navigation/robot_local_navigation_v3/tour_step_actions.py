from __future__ import annotations

from typing import Any

from robot_local_navigation_v3.linkcraft_resources import normalize_linkcraft_action


MIN_TASK_WAIT_SEC = 0.0
MAX_TASK_WAIT_SEC = 300.0
DEFAULT_TASK_WAIT_SEC = 0.0


def normalize_tour_step(item: Any) -> dict[str, Any] | None:
    """Normalize one tour step while preserving inherit/disable semantics."""

    if isinstance(item, str):
        point_id = item.strip()
        return {
            "point_id": point_id,
            "enabled": True,
            "speech_text": "",
            "wait_sec": DEFAULT_TASK_WAIT_SEC,
        } if point_id else None
    if not isinstance(item, dict):
        return None

    point_id = str(item.get("point_id", item.get("id", ""))).strip()
    if not point_id:
        return None
    step: dict[str, Any] = {
        "point_id": point_id,
        "enabled": bool(item.get("enabled", True)),
        "speech_text": str(item.get("speech_text", item.get("text", ""))).strip(),
        "wait_sec": normalize_task_wait_sec(item.get("wait_sec", DEFAULT_TASK_WAIT_SEC)),
    }
    if "arrival_motion" in item:
        step["arrival_motion"] = _normalize_motion(item.get("arrival_motion"))
    if "arrival_emoji" in item:
        step["arrival_emoji"] = _normalize_emoji(item.get("arrival_emoji"))
    if "linkcraft_action" in item:
        step["linkcraft_action"] = normalize_linkcraft_action(item.get("linkcraft_action"))
    return step


def normalize_task_wait_sec(value: Any) -> float:
    try:
        wait_sec = float(value)
    except (TypeError, ValueError):
        wait_sec = DEFAULT_TASK_WAIT_SEC
    return min(MAX_TASK_WAIT_SEC, max(MIN_TASK_WAIT_SEC, wait_sec))


def merge_task_arrival_actions(
    step: dict[str, Any] | None,
    point_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply task-step overrides to point defaults and return executable actions."""

    step = step if isinstance(step, dict) else {}
    actions = [dict(action) for action in point_actions if isinstance(action, dict)]

    if "arrival_motion" in step:
        actions = [action for action in actions if action.get("type") != "motion"]
        motion = _normalize_motion(step.get("arrival_motion"))
        if motion:
            actions.append({"type": "motion", **motion})

    if "arrival_emoji" in step:
        actions = [action for action in actions if action.get("type") != "emoji"]
        emoji = _normalize_emoji(step.get("arrival_emoji"))
        if emoji:
            actions.append({"type": "emoji", **emoji})

    speech_text = str(step.get("speech_text", "")).strip()
    if speech_text:
        actions = [action for action in actions if action.get("type") != "tts"]
        actions.append({"type": "tts", "text": speech_text})
    return actions


def _normalize_motion(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    motion = str(value.get("motion", "")).strip()
    if not motion:
        return None
    return {
        "motion": motion,
        "area": str(value.get("area", "right_hand")).strip() or "right_hand",
        "interrupt": bool(value.get("interrupt", True)),
        "force": bool(value.get("force", True)),
    }


def _normalize_emoji(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    emotion = str(value.get("emotion", "")).strip()
    if not emotion:
        return None
    emoji: dict[str, Any] = {
        "emotion": emotion,
        "mode": str(value.get("mode", "once")).strip() or "once",
    }
    if value.get("priority") is not None:
        try:
            emoji["priority"] = int(value["priority"])
        except (TypeError, ValueError):
            pass
    return emoji
