from __future__ import annotations

import time
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future

from aimdk_msgs.msg import TtsPriorityLevel
from aimdk_msgs.srv import PlayTts
from robot_local_navigation_v3.tts_receipt import response_receipt


DEFAULT_TTS_SERVICE = "/aimdk_5Fmsgs/srv/PlayTts"
DEFAULT_TTS_DOMAIN = "robot_local_navigation_v3"


class TtsSpeaker:
    """Small adapter for the AimDK PlayTts service."""

    def __init__(
        self,
        node,
        service_name: str = DEFAULT_TTS_SERVICE,
        domain: str = DEFAULT_TTS_DOMAIN,
        wait_timeout_sec: float = 1.0,
    ):
        self.node = node
        self.service_name = service_name
        self.domain = domain
        self.wait_timeout_sec = float(wait_timeout_sec)
        self.client = node.create_client(PlayTts, service_name)

    def speak(
        self,
        text: str,
        *,
        interrupt: bool = True,
        # Lingxi submits an L6 placeholder for the same turn; L7 prevents it
        # from preempting the robot-local reply while staying below alerts.
        priority: int = TtsPriorityLevel.SYSTEM_L7,
        priority_weight: int = 0,
        trace_id: str | None = None,
        done_callback: Callable[[object], None] | None = None,
        retry_submit: Callable[[Callable[[], None]], bool] | None = None,
    ):
        text = str(text or "").strip()
        if not text:
            return None
        if not self.client.wait_for_service(timeout_sec=self.wait_timeout_sec):
            self.node.get_logger().warning(f"TTS service is not available: {self.service_name}")
            return None

        request = PlayTts.Request()
        request.header.header.stamp = self.node.get_clock().now().to_msg()
        request.tts_req.text = text
        request.tts_req.priority_level.value = int(priority)
        request.tts_req.priority_weight = int(priority_weight)
        request.tts_req.domain = self.domain
        request.tts_req.trace_id = trace_id or self._make_trace_id()
        request.tts_req.is_interrupted = bool(interrupt)

        # Retry only a confirmed priority rejection. An unknown/late result
        # could already be playing, so it must never cause another submission.
        future = Future()
        started = time.monotonic()

        def submit(attempt):
            if future.done():
                return
            try:
                def send():
                    request.tts_req.trace_id = trace + f"_{attempt}"
                    pending = self.client.call_async(request)
                    pending.add_done_callback(lambda result: finish(result, attempt))

                if attempt and (time.monotonic() - started > 1.5
                                or retry_submit is None or not retry_submit(send)):
                    future.set_result(last_rejection[0])
                    return
                if not attempt:
                    send()
            except Exception as exc:
                future.set_exception(exc)

        def finish(pending, attempt):
            try:
                response = pending.result()
                receipt = response_receipt(response)
                if (receipt["accepted"] is False
                        and receipt["message"].strip().lower() == "priority_rejected"
                        and attempt < 2 and retry_submit is not None
                        and time.monotonic() - started < 1.0):
                    last_rejection[0] = response
                    timer = threading.Timer(0.25 * (attempt + 1), submit, (attempt + 1,))
                    timer.daemon = True
                    timer.start()
                    return
                future.set_result(response)
            except Exception as exc:
                future.set_exception(exc)

        trace = request.tts_req.trace_id
        last_rejection = [None]
        if done_callback is not None:
            future.add_done_callback(done_callback)
        else:
            future.add_done_callback(lambda fut: self._log_result(fut, text))
        submit(0)
        return future

    def _make_trace_id(self) -> str:
        return f"{self.domain}_{uuid.uuid4().hex}"

    def _log_result(self, future, text: str) -> None:
        try:
            response = future.result()
        except Exception as exc:
            self.node.get_logger().error(f"TTS request failed: {exc}")
            return

        receipt = response_receipt(response)
        if receipt["accepted"] is True:
            self.node.get_logger().info(f"TTS request accepted: {text}")
        else:
            self.node.get_logger().warning(f"TTS request {receipt['status']}: {receipt['message']}")
