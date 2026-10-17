from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable


class NavigationEventType(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    STARTED = "started"
    PROGRESS = "progress"
    BLOCKED = "blocked"
    ARRIVED_REGION = "arrived_region"
    STOP_SENT = "stop_sent"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class NavigationEvent:
    type: NavigationEventType
    task_id: int | None
    goal_id: str
    point_name: str
    status: str
    current_pose: dict[str, Any] | None = None
    distance_to_goal: float | None = None
    yaw_error: float | None = None
    reason: str = ""
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if self.created_at <= 0:
            object.__setattr__(self, "created_at", time.time())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = self.type.value
        return payload


NavigationEventListener = Callable[[NavigationEvent], None]


def emit_safely(
    listener: NavigationEventListener | None,
    event: NavigationEvent,
    logger: Any | None = None,
) -> None:
    if listener is None:
        return
    try:
        listener(event)
    except Exception as exc:
        if logger is not None:
            logger.error(f"Navigation event listener failed: {exc}")
