"""PlayTts acknowledgement is distinct from submission and playback completion."""
from __future__ import annotations

import threading


def response_receipt(response):
    body = getattr(response, "tts_resp", None)
    success = getattr(body, "is_success", None)
    header = getattr(getattr(response, "header", None), "header", None)
    message = str(getattr(body, "error_message", "") or getattr(header, "message", "") or "")
    if success is True:
        return {"ok": True, "accepted": True, "status": "accepted", "message": message}
    if success is False:
        return {"ok": False, "accepted": False, "status": "rejected", "message": message}
    return {"ok": True, "accepted": None, "status": "ack_unknown", "message": "Malformed PlayTts acknowledgement"}


def future_receipt(future):
    try:
        return response_receipt(future.result())
    except Exception as exc:
        return {"ok": True, "accepted": None, "status": "ack_unknown", "message": str(exc)}


def wait_receipt(future, timeout_sec):
    # Only HTTP worker threads may wait here; the ROS executor must stay free.
    ready = threading.Event()
    future.add_done_callback(lambda _: ready.set())
    if ready.wait(timeout_sec):
        return future_receipt(future)
    return {"ok": True, "accepted": None, "status": "ack_pending",
            "message": "PlayTts acknowledgement pending; do not resubmit"}
