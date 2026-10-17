from __future__ import annotations

import time
from collections.abc import Callable

from aimdk_msgs.msg import TtsPriorityLevel
from aimdk_msgs.srv import PlayTts


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

        future = self.client.call_async(request)
        if done_callback is not None:
            future.add_done_callback(done_callback)
        else:
            future.add_done_callback(lambda fut: self._log_result(fut, text))
        return future

    def _make_trace_id(self) -> str:
        return f"{self.domain}_{int(time.time() * 1000)}"

    def _log_result(self, future, text: str) -> None:
        try:
            response = future.result()
        except Exception as exc:
            self.node.get_logger().error(f"TTS request failed: {exc}")
            return

        try:
            success = bool(response.tts_resp.is_success)
            message = str(response.tts_resp.error_message or response.header.header.message)
        except AttributeError:
            success = True
            message = ""

        if success:
            self.node.get_logger().info(f"TTS request accepted: {text}")
        else:
            self.node.get_logger().warning(f"TTS request rejected: {message}")
