from __future__ import annotations

from typing import Any


def with_rejection_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Mirror a rejected operation's reason into the legacy message field."""

    if payload.get("ok") is not False and payload.get("accepted") is not False:
        return payload
    if str(payload.get("message", "")).strip():
        return payload
    reason = str(payload.get("reason", "")).strip()
    return {**payload, "message": reason} if reason else payload
