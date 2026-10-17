from __future__ import annotations


TOUR_EXECUTION_STATES = frozenset(
    {
        "navigating_task_step",
        "task_arrival_action",
        "task_linkcraft_action",
    }
)

TOUR_CONTROL_INTENTS = frozenset(
    {
        "pause_task",
        "resume_task",
        "end_task",
        "stop_task",
        "stop_navigation",
    }
)

NAVIGATING_TASK_ALLOWED_INTENTS = TOUR_CONTROL_INTENTS | frozenset({"chat", "wake"})
LINKCRAFT_ALLOWED_INTENTS = frozenset(
    {
        "end_task",
        "stop_task",
        "stop_navigation",
    }
)

WAKE_ACTION_ALLOW = "allow"
WAKE_ACTION_PAUSE = "pause"
WAKE_ACTION_BLOCK = "block"

TOUR_INTERNAL_VOICE_EVENT_PREFIXES = (
    "arrival-",
    "nav-problem-",
)


def is_tour_execution_state(state: str) -> bool:
    return str(state or "").strip() in TOUR_EXECUTION_STATES


def should_protect_tour_interaction(state: str, intent: str) -> bool:
    """按讲解阶段限制会抢占路线、讲解或灵创资源的语音意图。"""

    normalized_state = str(state or "").strip()
    normalized_intent = str(intent or "").strip().lower()
    if normalized_state == "navigating_task_step":
        return normalized_intent not in NAVIGATING_TASK_ALLOWED_INTENTS
    if normalized_state == "task_linkcraft_action":
        return normalized_intent not in LINKCRAFT_ALLOWED_INTENTS
    if normalized_state == "task_arrival_action":
        return normalized_intent not in TOUR_CONTROL_INTENTS
    return False


def wake_interaction_action(state: str) -> str:
    """返回唤醒在当前讲解阶段应执行的动作。"""

    normalized_state = str(state or "").strip()
    if normalized_state == "task_arrival_action":
        return WAKE_ACTION_PAUSE
    if normalized_state == "task_linkcraft_action":
        return WAKE_ACTION_BLOCK
    return WAKE_ACTION_ALLOW


def should_block_voice_output(state: str, event_id: str) -> bool:
    """到点讲解和灵创阶段拦截云端回复；行进中允许正常问答。"""

    normalized_event_id = str(event_id or "").strip()
    if not normalized_event_id or normalized_event_id.startswith(TOUR_INTERNAL_VOICE_EVENT_PREFIXES):
        return False
    return str(state or "").strip() in {
        "task_arrival_action",
        "task_linkcraft_action",
    }
