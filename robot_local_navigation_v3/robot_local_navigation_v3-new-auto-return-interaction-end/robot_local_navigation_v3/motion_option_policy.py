from __future__ import annotations


SPECIAL_MOTION_ALIASES: dict[str, int] = {
    "empty_motion": 0,
    "random_speech": 6001,
}

SPECIAL_MOTION_ALLOWED_AREAS: dict[int, set[int]] = {
    0: {0},
    6001: set(range(21, 49)),
}


def is_empty_motion(motion_id: int, ani_path: str = "") -> bool:
    return int(motion_id) == 0 and not str(ani_path or "").strip()


class ArrivalMotionTracker:
    """Tracks whether an arrival motion still needs an explicit empty-motion stop."""

    def __init__(self) -> None:
        self.active = False

    def mark_started(self, motion_id: int, ani_path: str = "") -> bool:
        self.active = not is_empty_motion(motion_id, ani_path)
        return self.active

    def claim_stop(self, *, force: bool = False) -> bool:
        should_stop = self.active or bool(force)
        self.active = False
        return should_stop


def parse_numeric_area_id(area: object) -> int | None:
    text = str(area).strip()
    return int(text) if text.isdigit() else None
