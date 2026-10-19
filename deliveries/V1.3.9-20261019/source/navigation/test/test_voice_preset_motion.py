from robot_local_navigation_v3.voice_preset_motion import (
    resolve_voice_preset_motion,
    voice_preset_motion_block_reason,
)


def test_voice_motion_catalog_resolves_documented_defaults():
    assert resolve_voice_preset_motion("handshake") == ("handshake", "right_hand")
    assert resolve_voice_preset_motion("sweetheart") == ("sweetheart", "both_hands")
    assert resolve_voice_preset_motion("wave", "left_hand") == ("wave", "left_hand")


def test_voice_motion_catalog_rejects_unknown_or_invalid_area():
    assert resolve_voice_preset_motion("invented_motion") is None
    assert resolve_voice_preset_motion("handshake", "whole_body") is None
    assert resolve_voice_preset_motion("hug", "right_hand") is None


def test_voice_motion_runs_only_when_navigation_and_tour_are_idle():
    assert voice_preset_motion_block_reason("idle", False) == ""
    assert voice_preset_motion_block_reason("navigating", False) == "navigation_active"
    assert voice_preset_motion_block_reason("idle", True) == "tour_active"
