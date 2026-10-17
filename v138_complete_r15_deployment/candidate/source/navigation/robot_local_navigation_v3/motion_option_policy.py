from __future__ import annotations


SPECIAL_MOTION_ALIASES: dict[str, int] = {
    "empty_motion": 0,
    "random_speech": 6001,
}

SPECIAL_MOTION_ALLOWED_AREAS: dict[int, set[int]] = {
    0: {0},
    6001: set(range(21, 49)),
}

CONTINUOUS_MOTION_IDS = frozenset({6001})


def is_empty_motion(motion_id: int, ani_path: str = "") -> bool:
    return int(motion_id) == 0 and not str(ani_path or "").strip()


def motion_requires_explicit_stop(motion_id: int, ani_path: str = "") -> bool:
    """Return whether a motion is known to run until an explicit 0/0 interrupt."""

    return not str(ani_path or "").strip() and int(motion_id) in CONTINUOUS_MOTION_IDS


def is_request_acknowledged(
    header_code: object,
    state_value: object,
    accepted_states: tuple[object, ...],
) -> bool:
    """Prefer the documented header code and use state only for legacy replies."""

    if header_code is not None:
        try:
            return int(header_code) == 0
        except (TypeError, ValueError):
            return False
    return state_value in accepted_states


class ArrivalMotionTracker:
    """Track a finite arrival motion without treating submission as completion."""

    def __init__(self) -> None:
        self.active = False
        self.motion_id: int | None = None
        self.stop_required = False
        self.completion = "idle"

    def mark_started(self, motion_id: int, ani_path: str = "") -> bool:
        self.active = not is_empty_motion(motion_id, ani_path)
        self.motion_id = int(motion_id) if self.active else None
        self.stop_required = (
            motion_requires_explicit_stop(motion_id, ani_path) if self.active else False
        )
        self.completion = "running" if self.active else "idle"
        return self.active

    def mark_naturally_completed(self) -> bool:
        """Clear a finite action after its configured wait without sending 0/0."""

        if self.stop_required:
            return False
        was_active = self.active
        self.active = False
        self.motion_id = None
        self.stop_required = False
        self.completion = "natural" if was_active else self.completion
        return was_active

    def completion_action_after_wait(self) -> str:
        """Choose natural completion or an explicit stop for a continuous motion."""

        if not self.active:
            return "idle"
        if self.stop_required:
            return "interrupt"
        self.mark_naturally_completed()
        return "natural"

    def claim_stop(self, *, force: bool = False) -> bool:
        should_stop = self.active or bool(force)
        self.active = False
        self.motion_id = None
        self.stop_required = False
        if should_stop:
            self.completion = "interrupted"
        return should_stop

    def snapshot(self) -> dict[str, object]:
        return {
            "active": self.active,
            "motion_id": self.motion_id,
            "stop_required": self.stop_required,
            "completion": self.completion,
        }


def parse_numeric_area_id(area: object) -> int | None:
    text = str(area).strip()
    return int(text) if text.isdigit() else None
