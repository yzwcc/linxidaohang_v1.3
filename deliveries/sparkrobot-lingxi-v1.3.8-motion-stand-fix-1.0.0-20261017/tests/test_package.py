from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "payload" / "sdk_motion_controller.py"


def test_motion_controller_is_valid_python() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    assert any(isinstance(node, ast.ClassDef) and node.name == "SdkMotionController" for node in tree.body)


def test_stable_rejection_recovery_is_present() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    assert "STABILITY_REJECTION_MARKERS" in text
    assert "_request_stable_stand" in text
    assert "stable_recovery_attempted" in text
    assert "state_value == CommonState.NOT_READY" in text
    assert "failure_message=response_message" in text
