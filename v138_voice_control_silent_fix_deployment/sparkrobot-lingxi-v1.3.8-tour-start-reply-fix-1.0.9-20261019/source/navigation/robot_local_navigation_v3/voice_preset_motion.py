from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoicePresetMotion:
    name: str
    default_area: str
    allowed_areas: frozenset[str]


VOICE_PRESET_MOTIONS: dict[str, VoicePresetMotion] = {
    "wave": VoicePresetMotion("wave", "right_hand", frozenset({"left_hand", "right_hand"})),
    "handshake": VoicePresetMotion("handshake", "right_hand", frozenset({"left_hand", "right_hand"})),
    "raise_hand": VoicePresetMotion("raise_hand", "right_hand", frozenset({"left_hand", "right_hand"})),
    "flying_kiss": VoicePresetMotion("flying_kiss", "right_hand", frozenset({"left_hand", "right_hand"})),
    "sweetheart": VoicePresetMotion("sweetheart", "both_hands", frozenset({"left_hand", "right_hand", "both_hands"})),
    "high_five": VoicePresetMotion("high_five", "right_hand", frozenset({"left_hand", "right_hand"})),
    "salute": VoicePresetMotion("salute", "right_hand", frozenset({"left_hand", "right_hand"})),
    "horizontal_arm": VoicePresetMotion("horizontal_arm", "both_hands", frozenset({"left_hand", "right_hand", "both_hands"})),
    "hug": VoicePresetMotion("hug", "whole_body", frozenset({"whole_body"})),
    "cheer": VoicePresetMotion("cheer", "whole_body", frozenset({"whole_body"})),
    "clap": VoicePresetMotion("clap", "whole_body", frozenset({"whole_body"})),
    "handx": VoicePresetMotion("handx", "whole_body", frozenset({"whole_body"})),
    "chestwave": VoicePresetMotion("chestwave", "right_hand", frozenset({"left_hand", "right_hand"})),
    "bow": VoicePresetMotion("bow", "whole_body", frozenset({"whole_body"})),
    "bye": VoicePresetMotion("bye", "whole_body", frozenset({"whole_body"})),
    "lightwave": VoicePresetMotion("lightwave", "whole_body", frozenset({"whole_body"})),
    "scratch_head": VoicePresetMotion("scratch_head", "whole_body", frozenset({"whole_body"})),
    "scratch_back": VoicePresetMotion("scratch_back", "whole_body", frozenset({"whole_body"})),
}


def resolve_voice_preset_motion(name: object, area: object = None) -> tuple[str, str] | None:
    normalized_name = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    motion = VOICE_PRESET_MOTIONS.get(normalized_name)
    if motion is None:
        return None
    normalized_area = str(area or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized_area:
        normalized_area = motion.default_area
    if normalized_area not in motion.allowed_areas:
        return None
    return motion.name, normalized_area


def voice_preset_motion_block_reason(navigation_status: object, task_active: object) -> str:
    if str(navigation_status or "idle").strip().lower() != "idle":
        return "navigation_active"
    if bool(task_active):
        return "tour_active"
    return ""
