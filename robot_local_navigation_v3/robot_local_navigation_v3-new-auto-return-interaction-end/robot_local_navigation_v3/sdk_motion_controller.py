from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Callable

from aimdk_msgs.msg import CommonState, McControlArea, McPresetMotion, RequestHeader
from aimdk_msgs.srv import ExecuteActionResource, PlayEmoji, SetMcPresetMotion

from robot_local_navigation_v3.motion_option_policy import (
    SPECIAL_MOTION_ALIASES,
    SPECIAL_MOTION_ALLOWED_AREAS,
    is_empty_motion,
    is_request_acknowledged,
    parse_numeric_area_id,
)


PRESET_MOTION_SERVICE = "/aimdk_5Fmsgs/srv/SetMcPresetMotion"
FACE_EMOJI_SERVICE = "/face_ui_proxy/play_emoji"
ACTION_RESOURCE_SERVICE = "/aimdk_5Fmsgs/srv/ExecuteActionResource"
DEFAULT_EMOJI_PRIORITY = 65
DEFAULT_WAIT_TIMEOUT_SEC = 0.5
DEFAULT_MIN_MOTION_INTERVAL_SEC = 0.8
DEFAULT_MOTION_RETRY_DELAY_SEC = 1.5

# Friendly alias -> documented SDK motion id. Legacy aliases use the locally
# generated aimdk_msgs constants so existing map files remain readable.
MOTION_ALIASES: dict[str, int] = {
    **SPECIAL_MOTION_ALIASES,
    "raise_hand": 1001,
    "wave": 1002,
    "wave_hand": McPresetMotion.WAVE_HAND,
    "handshake": 1003,
    "shake_hand": McPresetMotion.SHAKE_HAND,
    "flying_kiss": 1004,
    "sweetheart": 1007,
    "high_five": 1008,
    "horizontal_arm": 1010,
    "chestwave": 1011,
    "salute": 1013,
    "clap": 3017,
    "bow": 3001,
    "lightwave": 3007,
    "hug": 3008,
    "handx": 3009,
    "cheer": 3011,
    "bye": 3031,
    "scratch_head": 3024,
    "scratch_back": 3025,
    # Legacy aliases remain readable for existing maps, but are not offered by the console.
    "turn_wave": McPresetMotion.TURN_WAVE_HAND,
    "like": McPresetMotion.INTERACTION_LIKE,
    "ye": McPresetMotion.INTERACTION_YE,
    "sad": McPresetMotion.INTERACTION_SAD,
    "blowkiss": McPresetMotion.INTERACTION_BLOWKISS,
    "bassdance1": McPresetMotion.INTERACTION_BASSDANCE1,
    "bassdance2": McPresetMotion.INTERACTION_BASSDANCE2,
    "hitclap": McPresetMotion.HITCLAP,
    "speak": McPresetMotion.INTERACTION_SPEAK,
    "speech": McPresetMotion.INTERACTION_SPEAK,
    "clipfist": McPresetMotion.CLIPFIST,
    "photo": McPresetMotion.INTERACTION_PHOTOPOSTURE,
    "photo_triple": McPresetMotion.INTERACTION_PHOTOTRIPPLEPOSTURE,
    "nod": McPresetMotion.POINT_HEAD,
    "point_head": McPresetMotion.POINT_HEAD,
    "shake_head": McPresetMotion.SHAKE_HEAD,
}

AREA_ALIASES: dict[str, int] = {
    "none": 11,
    "no_area": McControlArea.NONE,
    "left": McControlArea.LEFT_HAND,
    "left_hand": McControlArea.LEFT_HAND,
    "right": McControlArea.RIGHT_HAND,
    "right_hand": McControlArea.RIGHT_HAND,
    "both_hands": 3,
    "head": McControlArea.HEAD,
    "waist": McControlArea.WAIST,
    "whole_body": 11,
}

MOTION_ALLOWED_AREAS: dict[int, set[int]] = {
    **SPECIAL_MOTION_ALLOWED_AREAS,
    1001: {1, 2}, 1002: {1, 2}, 1003: {1, 2}, 1004: {1, 2},
    1007: {1, 2, 3}, 1008: {1, 2}, 1010: {1, 2, 3}, 1011: {1, 2},
    1013: {1, 2},
    3001: {11}, 3007: {11}, 3008: {11}, 3009: {11}, 3011: {11},
    3017: {11}, 3024: {11}, 3025: {11}, 3031: {11},
}
MOTION_DEFAULT_AREAS: dict[int, int] = {
    motion_id: (11 if 11 in areas else (2 if 2 in areas else min(areas)))
    for motion_id, areas in MOTION_ALLOWED_AREAS.items()
}

# Friendly alias -> SDK PlayEmoji.Request constant.
EMOTION_ALIASES: dict[str, int] = {
    "blink": PlayEmoji.Request.EMOTION_IDLE_BLINK,
    "calm": PlayEmoji.Request.EMOTION_IDLE_CALM_1,
    "calm2": PlayEmoji.Request.EMOTION_IDLE_CALM_2,
    "game": PlayEmoji.Request.EMOTION_IDLE_GAME,
    "cute": PlayEmoji.Request.EMOTION_IDLE_CUTE_1,
    "cute2": PlayEmoji.Request.EMOTION_IDLE_CUTE_2,
    "cute3": PlayEmoji.Request.EMOTION_IDLE_CUTE_3,
    "cute4": PlayEmoji.Request.EMOTION_IDLE_CUTE_4,
    "eye_close": PlayEmoji.Request.EMOTION_EYE_CLOSE,
    "eye_open": PlayEmoji.Request.EMOTION_EYE_OPEN,
    "boring": PlayEmoji.Request.EMOTION_EYE_BORING_1,
    "abnormal": PlayEmoji.Request.EMOTION_EYE_ABNORMAL,
    "sleepy": PlayEmoji.Request.EMOTION_EYE_SLEEPY,
    "happy": PlayEmoji.Request.EMOTION_EYE_HAPPY,
    "extreme_happy": PlayEmoji.Request.EMOTION_EYE_EXTREMEHAPPY_1,
    "extreme_happy2": PlayEmoji.Request.EMOTION_EYE_EXTREMEHAPPY_2,
    "sad": PlayEmoji.Request.EMOTION_EYE_SAD,
    "sympathy": PlayEmoji.Request.EMOTION_EYE_SYMPATHY,
    "confuse": PlayEmoji.Request.EMOTION_EYE_CONFUSE,
    "shock": PlayEmoji.Request.EMOTION_EYE_SHOCK,
    "act_cute": PlayEmoji.Request.EMOTION_EYE_ACTCUTE,
    "serious": PlayEmoji.Request.EMOTION_EYE_SERIOUS,
    "thinking": PlayEmoji.Request.EMOTION_EYE_THINKING,
    "angry": PlayEmoji.Request.EMOTION_EYE_ANGRY,
    "extreme_angry": PlayEmoji.Request.EMOTION_EYE_EXTREMEANGRY,
    "adore": PlayEmoji.Request.EMOTION_EYE_ADORE,
    "extreme_adore": PlayEmoji.Request.EMOTION_EYE_EXTREMEADORE,
    "charge": PlayEmoji.Request.EMOTION_EYE_CHARGE,
}

EMOJI_MODE_ONCE = PlayEmoji.Request.EMOTION_MODE_ONCE
EMOJI_MODE_LOOP = PlayEmoji.Request.EMOTION_MODE_LOOP
EMOJI_MODE_ALIASES: dict[str, int] = {
    "once": EMOJI_MODE_ONCE,
    "loop": EMOJI_MODE_LOOP,
}


@dataclass(frozen=True)
class MotionRequestInfo:
    motion_id: int
    area_id: int
    interrupt: bool
    ani_path: str = ""
    play_timestamp: int = 0


class SdkMotionController:
    """Fail-safe adapter for SDK preset motion and face emoji services."""

    def __init__(
        self,
        node,
        *,
        motion_service: str = PRESET_MOTION_SERVICE,
        emoji_service: str = FACE_EMOJI_SERVICE,
        action_resource_service: str = ACTION_RESOURCE_SERVICE,
        wait_timeout_sec: float = DEFAULT_WAIT_TIMEOUT_SEC,
        min_motion_interval_sec: float = DEFAULT_MIN_MOTION_INTERVAL_SEC,
        motion_retry_delay_sec: float = DEFAULT_MOTION_RETRY_DELAY_SEC,
    ):
        self.node = node
        self.motion_service = motion_service
        self.emoji_service = emoji_service
        self.action_resource_service = action_resource_service
        self.wait_timeout_sec = max(0.0, float(wait_timeout_sec))
        self.min_motion_interval_sec = max(0.0, float(min_motion_interval_sec))
        self.motion_retry_delay_sec = max(0.0, float(motion_retry_delay_sec))
        self.motion_client = node.create_client(SetMcPresetMotion, motion_service)
        self.emoji_client = node.create_client(PlayEmoji, emoji_service)
        self.action_resource_client = node.create_client(
            ExecuteActionResource,
            action_resource_service,
        )
        self._last_motion_at = 0.0

    def services_ready(self) -> dict[str, bool]:
        return {
            "motion": self.motion_client.service_is_ready()
            or self.motion_client.wait_for_service(timeout_sec=0.0),
            "emoji": self.emoji_client.service_is_ready()
            or self.emoji_client.wait_for_service(timeout_sec=0.0),
            "action_resource": self.action_resource_client.service_is_ready()
            or self.action_resource_client.wait_for_service(timeout_sec=0.0),
        }

    def execute_action_resource(
        self,
        resource_key: str,
        resource_version: str,
        resource_type: str,
        *,
        trace_id: str = "",
        on_result: Callable[[bool, str], None] | None = None,
    ):
        """Submit one downloaded LinkCraft resource and report only acceptance."""

        key = str(resource_key or "").strip()
        version = str(resource_version or "").strip()
        kind = str(resource_type or "").strip().upper()
        if not key.lower().startswith("linkcraft_resource_") or not version:
            message = "灵创资源标识或版本无效"
            self.node.get_logger().warning(message)
            if on_result:
                on_result(False, message)
            return None
        if kind not in {"BODY_MONTION", "ARM_MONTION"}:
            message = f"灵创资源类型无效: {kind}"
            self.node.get_logger().warning(message)
            if on_result:
                on_result(False, message)
            return None
        if not self.action_resource_client.wait_for_service(timeout_sec=self.wait_timeout_sec):
            message = f"灵创资源服务不可用: {self.action_resource_service}"
            self.node.get_logger().warning(message)
            if on_result:
                on_result(False, message)
            return None

        request = ExecuteActionResource.Request()
        request.header.header.stamp = self.node.get_clock().now().to_msg()
        request.resource_key = key
        request.resource_version = version
        request.meta = json.dumps({
            "resource_type": kind,
            "trace_id": str(trace_id or ""),
            "source": "lingxi_navigation",
        }, ensure_ascii=False)
        future = self.action_resource_client.call_async(request)
        future.add_done_callback(
            lambda fut: self._handle_action_resource_result(fut, key, on_result)
        )
        self.node.get_logger().info(
            f"灵创资源已提交: resource_key={key}, version={version}, type={kind}"
        )
        return future

    def play_preset_motion(
        self,
        motion: str | int,
        area: str | int | None = None,
        *,
        interrupt: bool = False,
        ani_path: str = "",
        play_timestamp: int = 0,
        force: bool = False,
    ):
        now = time.monotonic()
        if not force and (now - self._last_motion_at) < self.min_motion_interval_sec:
            self.node.get_logger().debug("Skip SDK motion request due to rate limit")
            return None

        try:
            motion_id = self.resolve_motion_id(motion)
            # motion=0 with ani_path is the legacy custom-motion path, not the
            # empty interrupt action, so it retains the caller's control area.
            area_id = self.resolve_area_id(
                area,
                motion_id=None if str(ani_path or "").strip() else motion_id,
            )
        except ValueError as exc:
            self.node.get_logger().warning(str(exc))
            return None

        self.node.get_logger().info(
            f"SDK motion submitting: requested_motion={motion}, motion={motion_id}, "
            f"requested_area={area}, area={area_id}, interrupt={bool(interrupt)}"
        )
        future = self._send_motion_request(
            MotionRequestInfo(
                motion_id=motion_id,
                area_id=area_id,
                interrupt=bool(interrupt) or is_empty_motion(motion_id, ani_path),
                ani_path=str(ani_path or ""),
                play_timestamp=max(0, int(play_timestamp or 0)),
            )
        )
        if future is not None:
            self._last_motion_at = now
        return future

    def play_custom_motion(self, ani_path: str, *, area: str | int = "right_hand", interrupt: bool = False):
        path = str(ani_path or "").strip()
        if not path:
            self.node.get_logger().warning("ani_path is empty")
            return None
        return self.play_preset_motion(0, area, interrupt=interrupt, ani_path=path, force=True)

    def play_emoji(
        self,
        emotion: str | int = "calm",
        *,
        mode: str | int = EMOJI_MODE_ONCE,
        priority: int = DEFAULT_EMOJI_PRIORITY,
    ):
        emotion_id = self.resolve_emotion_id(emotion)
        if emotion_id <= 0:
            self.node.get_logger().warning(f"Unknown SDK emoji: {emotion}")
            return None
        try:
            mode_id = self.resolve_emoji_mode(mode)
        except ValueError as exc:
            self.node.get_logger().warning(str(exc))
            return None
        if not self.emoji_client.wait_for_service(timeout_sec=self.wait_timeout_sec):
            self.node.get_logger().debug(f"SDK emoji service unavailable: {self.emoji_service}")
            return None
        request = PlayEmoji.Request()
        request.header.header.stamp = self.node.get_clock().now().to_msg()
        request.emotion_id = int(emotion_id)
        request.mode = int(mode_id)
        request.priority = int(priority)
        future = self.emoji_client.call_async(request)
        future.add_done_callback(lambda fut: self._log_emoji_result(fut, emotion_id))
        return future

    def resolve_motion_id(self, motion: str | int) -> int:
        if isinstance(motion, int):
            return int(motion)
        text = str(motion or "").strip()
        if not text:
            raise ValueError("motion is required")
        if text.isdigit():
            return int(text)
        key = text.lower().replace("-", "_").replace(" ", "_")
        if key not in MOTION_ALIASES:
            raise ValueError(f"Unknown SDK motion: {motion}")
        return int(MOTION_ALIASES[key])

    def resolve_area_id(self, area: str | int | None, *, motion_id: int | None = None) -> int:
        default_area = MOTION_DEFAULT_AREAS.get(int(motion_id or 0), int(McControlArea.RIGHT_HAND))
        if isinstance(area, int):
            area_id = int(area)
        elif area is None or str(area).strip() == "":
            area_id = default_area
        else:
            key = str(area).strip().lower().replace("-", "_").replace(" ", "_")
            numeric_area_id = parse_numeric_area_id(key)
            if numeric_area_id is not None:
                area_id = numeric_area_id
            elif key not in AREA_ALIASES:
                raise ValueError(f"Unknown SDK motion area: {area}")
            else:
                area_id = int(AREA_ALIASES[key])
        allowed_areas = MOTION_ALLOWED_AREAS.get(int(motion_id or 0))
        if allowed_areas and area_id not in allowed_areas:
            self.node.get_logger().warning(
                f"Unsupported SDK motion/area combination; using documented default: "
                f"motion={motion_id}, area={area_id}, fallback={default_area}"
            )
            return default_area
        return area_id

    def resolve_emotion_id(self, emotion: str | int) -> int:
        if isinstance(emotion, int):
            return int(emotion)
        text = str(emotion or "").strip()
        if text.isdigit():
            return int(text)
        key = text.lower().replace("-", "_").replace(" ", "_")
        return int(EMOTION_ALIASES.get(key, 0))

    def resolve_emoji_mode(self, mode: str | int) -> int:
        if isinstance(mode, int):
            return int(mode)
        text = str(mode or "").strip()
        if text.isdigit():
            return int(text)
        key = text.lower().replace("-", "_").replace(" ", "_")
        if key not in EMOJI_MODE_ALIASES:
            raise ValueError(f"Unknown SDK emoji mode: {mode}")
        return int(EMOJI_MODE_ALIASES[key])

    def _send_motion_request(self, info: MotionRequestInfo, *, retries_remaining: int = 1):
        if not self.motion_client.wait_for_service(timeout_sec=self.wait_timeout_sec):
            self.node.get_logger().debug(f"SDK motion service unavailable: {self.motion_service}")
            return None

        request = SetMcPresetMotion.Request()
        request.header = RequestHeader()
        request.header.stamp = self.node.get_clock().now().to_msg()
        request.area = McControlArea()
        request.area.value = int(info.area_id)
        request.motion = McPresetMotion()
        request.motion.value = int(info.motion_id)
        request.interrupt = bool(info.interrupt)
        request.ani_path = info.ani_path
        request.play_timestamp = int(info.play_timestamp)

        future = self.motion_client.call_async(request)
        future.add_done_callback(
            lambda fut: self._handle_motion_result(fut, info, retries_remaining)
        )
        return future

    def _handle_motion_result(
        self,
        future,
        info: MotionRequestInfo,
        retries_remaining: int,
    ) -> None:
        try:
            response = future.result()
        except Exception as exc:
            self.node.get_logger().warning(f"SDK motion request failed: {exc}")
            self._schedule_motion_retry(info, retries_remaining)
            return
        task_response = getattr(response, "response", None)
        state_value = getattr(getattr(task_response, "state", None), "value", None)
        header = getattr(task_response, "header", None)
        code = getattr(header, "code", None)
        accepted_states = (
            CommonState.SUCCESS,
            CommonState.RUNNING,
            CommonState.CREATED,
            CommonState.PENDING,
        )
        accepted = is_request_acknowledged(code, state_value, accepted_states)
        if accepted:
            self.node.get_logger().info(
                f"SDK motion request acknowledged: motion={info.motion_id}, area={info.area_id}, "
                f"state={state_value}, code={code}; state is not a completion callback"
            )
            return
        self.node.get_logger().warning(
            f"SDK motion rejected: motion={info.motion_id}, area={info.area_id}, "
            f"state={state_value}, code={code}"
        )
        self._schedule_motion_retry(info, retries_remaining)

    def _schedule_motion_retry(self, info: MotionRequestInfo, retries_remaining: int) -> None:
        if retries_remaining <= 0:
            self.node.get_logger().warning(
                f"SDK motion retry exhausted: motion={info.motion_id}, area={info.area_id}"
            )
            return
        self.node.get_logger().info(
            f"SDK motion retry scheduled: motion={info.motion_id}, area={info.area_id}, "
            f"delay_sec={self.motion_retry_delay_sec:.1f}"
        )
        timer = threading.Timer(
            self.motion_retry_delay_sec,
            lambda: self._send_motion_request(info, retries_remaining=retries_remaining - 1),
        )
        timer.daemon = True
        timer.start()

    def _log_emoji_result(self, future, emotion_id: int) -> None:
        try:
            response = future.result()
        except Exception as exc:
            self.node.get_logger().debug(f"SDK emoji request failed: {exc}")
            return
        if not getattr(response, "success", False):
            message = getattr(response, "message", "")
            self.node.get_logger().warning(f"SDK emoji rejected: emotion={emotion_id}, message={message}")

    def _handle_action_resource_result(
        self,
        future,
        resource_key: str,
        on_result: Callable[[bool, str], None] | None,
    ) -> None:
        try:
            response = future.result()
            common_response = getattr(response, "header", None)
            status_value = getattr(getattr(common_response, "status", None), "value", None)
            code = getattr(getattr(common_response, "header", None), "code", None)
            message = str(getattr(common_response, "message", "") or "")
            accepted = is_request_acknowledged(
                code,
                status_value,
                (
                    CommonState.SUCCESS,
                    CommonState.PENDING,
                    CommonState.CREATED,
                    CommonState.RUNNING,
                ),
            )
        except Exception as exc:
            accepted = False
            message = f"灵创资源请求失败: {exc}"
        log = self.node.get_logger().info if accepted else self.node.get_logger().warning
        log(
            f"灵创资源{'已接受' if accepted else '被拒绝'}: "
            f"resource_key={resource_key}, message={message or '<none>'}"
        )
        if on_result:
            on_result(accepted, message)
