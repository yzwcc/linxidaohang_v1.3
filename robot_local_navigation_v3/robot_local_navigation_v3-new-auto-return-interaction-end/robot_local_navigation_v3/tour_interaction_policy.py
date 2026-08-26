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

TOUR_INTERNAL_VOICE_EVENT_PREFIXES = (
    "arrival-",
    "nav-problem-",
)


def is_tour_execution_state(state: str) -> bool:
    return str(state or "").strip() in TOUR_EXECUTION_STATES


def should_protect_tour_interaction(state: str, intent: str) -> bool:
    """讲解执行期间只允许明确的任务控制和安全停止指令。"""

    normalized_intent = str(intent or "").strip().lower()
    return is_tour_execution_state(state) and normalized_intent not in TOUR_CONTROL_INTENTS


def should_block_voice_output(state: str, event_id: str) -> bool:
    """拦截云端普通回复，但允许讲解自身播报和导航故障提示。"""

    normalized_event_id = str(event_id or "").strip()
    if not normalized_event_id or normalized_event_id.startswith(TOUR_INTERNAL_VOICE_EVENT_PREFIXES):
        return False
    return is_tour_execution_state(state)
