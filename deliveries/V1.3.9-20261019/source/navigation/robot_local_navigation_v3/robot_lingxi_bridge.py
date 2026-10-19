#!/usr/bin/env python3
"""HTTP bridge from the Java Lingxi agent to this robot's local AimDK services.

This file intentionally copies the structure of the reference
robot_local_navigation_v3 bridge while keeping the robot-specific surface small:
for Lingxi X2 live chat we only expose health/status, interaction marks, safe
unsupported navigation endpoints, and AimDK PlayTts.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

import rclpy
from rclpy.node import Node

from aimdk_msgs.msg import McActionCommand, McControlArea, McPresetMotion, TtsPriorityLevel
from aimdk_msgs.srv import ExecuteActionResource, PlayAudioFile, PlayMediaFile, PlayTts, SetMcAction, SetMcPresetMotion


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 18080
DEFAULT_NAV_PROXY_URL = "http://127.0.0.1:18082"
DEFAULT_TTS_SERVICE = "/aimdk_5Fmsgs/srv/PlayTts"
DEFAULT_AUDIO_SERVICE = "/aimdk_5Fmsgs/srv/PlayAudioFile"
DEFAULT_MEDIA_SERVICE = "/aimdk_5Fmsgs/srv/PlayMediaFile"
DEFAULT_PRESET_MOTION_SERVICE = "/aimdk_5Fmsgs/srv/SetMcPresetMotion"
DEFAULT_ACTION_RESOURCE_SERVICE = "/aimdk_5Fmsgs/srv/ExecuteActionResource"
DEFAULT_MC_ACTION_SERVICE = "/aimdk_5Fmsgs/srv/SetMcAction"
DEFAULT_TTS_DOMAIN = "lingxi_x2_live_bridge"
DEFAULT_ENTERTAINMENT_DOMAIN = "lingxi_x2_entertainment"
DEFAULT_AUDIO_PKG_NAME = "demo_client"
DEFAULT_SING_ENABLED = False
DEFAULT_SING_MODE = "tts"
DEFAULT_LYRICS_DIR = "/var/tmp/lingxi_song_lyrics"
DEFAULT_DANCE_PREPARE_TEXT = "请给我一点空间"
DEFAULT_DANCE_PREPARE_DELAY_SEC = 3.0
DEFAULT_RANDOM_DANCE_KEYS = "happy,taiji,tmall,aini_voice,karaoke"
DEFAULT_SONG_CATALOG = (
    "twinkle=/var/tmp/lingxi_songs/twinkle.wav|"
    "happy_birthday=/var/tmp/lingxi_songs/happy_birthday.wav|"
    "dongfanghong=/var/tmp/lingxi_songs/dongfanghong.wav"
)
SONG_DISPLAY_NAMES = {
    "twinkle": "小星星",
    "happy_birthday": "生日快乐",
    "dongfanghong": "东方红",
}
SONG_TTS_PERFORMANCES = {
    "twinkle": "我用自己的声音给大家唱小星星。啦，啦啦，啦啦啦。祝大家直播间玩得开心。",
    "happy_birthday": "我用自己的声音给大家唱生日快乐。祝你生日快乐，祝你天天开心。",
    "dongfanghong": "我用自己的声音给大家唱东方红。啦，啦啦，啦啦啦。愿大家精神饱满，心情明亮。",
}
DEFAULT_DANCE_CATALOG = (
    "happy:linkcraft_resource_onnx_01K88DMFAWQCKW5T71XX86F1X8:0.0.1|"
    "aini:linkcraft_resource_onnx_01KGQ8Q36PMZ18RRYNZH2SEYD9:0.0.1|"
    "aini_voice:linkcraft_resource_onnx_01KPVXB1ZP1D2FNWZWD6KKDWKT:0.0.1|"
    "tmall:linkcraft_resource_onnx_01KGQ8MY8DNV63DYK4T8PB0E76:0.0.1|"
    "taiji:linkcraft_resource_onnx_01KGQ8MDSC6T01FSQSH8KTP9BF:0.0.1|"
    "henan_taiji:linkcraft_resource_onnx_01KMM3SQWBFSVZE9W9XZ7YPR1H:0.0.1|"
    "wing_chun:linkcraft_resource_onnx_01KMM3Q5HAC67RBNHQCF877BCS:0.0.1|"
    "karaoke:linkcraft_resource_onnx_01KB2KQWVGXE6AQC2BW2RC7ZXA:0.0.1"
)
DEFAULT_DISABLED_DANCES = "aini,henan_taiji,wing_chun"
DANCE_DISPLAY_NAMES = {
    "happy": "快乐之舞",
    "aini": "爱你",
    "aini_voice": "爱你有声",
    "tmall": "天猫（律动纪元）",
    "taiji": "太极",
    "henan_taiji": "河南太极",
    "wing_chun": "咏春拳",
    "karaoke": "卡拉OK",
}
DANCE_ALIASES = {
    "happy": ("happy", "dance", "dancing", "default", "快乐", "快乐之舞", "跳舞", "跳个舞", "来段舞"),
    "aini": ("aini", "爱你", "王心凌", "爱你舞"),
    "aini_voice": ("爱你有声", "有声爱你", "带声音爱你"),
    "tmall": ("tmall", "天猫", "律动", "律动纪元"),
    "taiji": ("taiji", "tai chi", "taichi", "太极"),
    "henan_taiji": ("henan", "河南太极"),
    "wing_chun": ("wing", "chun", "wingchun", "咏春", "咏春拳"),
    "karaoke": ("karaoke", "kalaok", "卡拉ok", "卡拉OK", "卡拉欧克"),
}


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lingxi_robot_bridge")


def parse_song_catalog(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in str(raw or "").split("|"):
        if not item.strip() or "=" not in item:
            continue
        name, path = item.split("=", 1)
        name = name.strip()
        path = path.strip()
        if name and path:
            result[name] = path
    return result


def parse_dance_catalog(raw: str) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for item in str(raw or "").split("|"):
        parts = [part.strip() for part in item.split(":")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        result[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else "")
    return result


def parse_csv_set(raw: str) -> set[str]:
    return {item.strip() for item in str(raw or "").split(",") if item.strip()}


def select_catalog_key(text: str, aliases: dict[str, tuple[str, ...]], default_key: str) -> str:
    normalized = str(text or "").lower()
    best_key = ""
    best_len = -1
    for key, words in aliases.items():
        for word in words:
            candidate = word.lower()
            if candidate and candidate in normalized and len(candidate) > best_len:
                best_key = key
                best_len = len(candidate)
    if best_key:
        return best_key
    return default_key


def is_generic_dance_request(text: str) -> bool:
    normalized = str(text or "").lower()
    specific_aliases = [
        alias.lower()
        for key, aliases in DANCE_ALIASES.items()
        if key != "happy"
        for alias in aliases
    ]
    if any(alias and alias in normalized for alias in specific_aliases):
        return False
    return any(word in normalized for word in ("跳舞", "跳个舞", "来段舞", "来一段舞", "dance", "dancing"))


def normalize_sing_mode(value: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in {"tts", "audio"} else DEFAULT_SING_MODE


def parse_bool(value: str, default: bool = False) -> bool:
    value = str(value or "").strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


class TtsSpeaker:
    """Small adapter for the robot-local AimDK PlayTts service."""

    def __init__(
        self,
        node: Node,
        *,
        service_name: str = DEFAULT_TTS_SERVICE,
        domain: str = DEFAULT_TTS_DOMAIN,
        wait_timeout_sec: float = 1.0,
    ) -> None:
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
        force: bool = False,
        trace_id: str | None = None,
        domain: str | None = None,
        done_callback: Callable[[object, str, str], None] | None = None,
    ):
        text = str(text or "").strip()
        if not text:
            return None
        if not self.client.wait_for_service(timeout_sec=self.wait_timeout_sec):
            self.node.get_logger().warning(f"TTS service is not available: {self.service_name}")
            return None

        request = PlayTts.Request()
        now = self.node.get_clock().now().to_msg()
        request.header.header.stamp = now
        request.tts_req.text = text
        request.tts_req.trace_id = trace_id or self._make_trace_id()
        request.tts_req.domain = domain or self.domain
        request.tts_req.is_interrupted = bool(interrupt)
        request.tts_req.priority_weight = 0
        request.tts_req.priority_level.value = (
            TtsPriorityLevel.SYSTEM_L7 if force else TtsPriorityLevel.INTERACTION_L6
        )

        future = self.client.call_async(request)
        if done_callback is None:
            future.add_done_callback(
                lambda fut: self._log_result(fut, request.tts_req.trace_id, text)
            )
        else:
            future.add_done_callback(
                lambda fut: done_callback(fut, request.tts_req.trace_id, text)
            )
        return future

    def service_ready(self) -> bool:
        return self.client.service_is_ready() or self.client.wait_for_service(timeout_sec=0.05)

    def _make_trace_id(self) -> str:
        return f"{self.domain}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

    def _log_result(self, future, trace_id: str, text: str) -> None:
        try:
            response = future.result()
        except Exception as exc:
            self.node.get_logger().error(f"TTS request failed: trace_id={trace_id}, error={exc}")
            return
        result = parse_tts_response(response)
        if result["accepted"]:
            self.node.get_logger().info(
                f"TTS request accepted: trace_id={trace_id}, estimated_duration={result['estimated_duration']}, text={text}"
            )
        else:
            self.node.get_logger().warning(
                f"TTS request rejected: trace_id={trace_id}, error={result['error']}, text={text}"
            )


class LingxiRobotBridge(Node):
    """ROS2 node plus HTTP server for Java Agent bridge calls."""

    def __init__(self, *, host: str, port: int) -> None:
        super().__init__("lingxi_x2_robot_bridge")
        self.host = host
        self.port = int(port)
        self.nav_proxy_url = os.environ.get("LINGXI_NAV_PROXY_URL", DEFAULT_NAV_PROXY_URL).rstrip("/")
        self.tts = TtsSpeaker(
            self,
            service_name=os.environ.get("LINGXI_TTS_SERVICE", DEFAULT_TTS_SERVICE),
            domain=os.environ.get("LINGXI_TTS_DOMAIN", DEFAULT_TTS_DOMAIN),
        )
        self.audio_client = self.create_client(
            PlayAudioFile,
            os.environ.get("LINGXI_AUDIO_SERVICE", DEFAULT_AUDIO_SERVICE),
        )
        self.media_client = self.create_client(
            PlayMediaFile,
            os.environ.get("LINGXI_MEDIA_SERVICE", DEFAULT_MEDIA_SERVICE),
        )
        self.motion_client = self.create_client(
            SetMcPresetMotion,
            os.environ.get("LINGXI_PRESET_MOTION_SERVICE", DEFAULT_PRESET_MOTION_SERVICE),
        )
        self.mc_action_client = self.create_client(
            SetMcAction,
            os.environ.get("LINGXI_MC_ACTION_SERVICE", DEFAULT_MC_ACTION_SERVICE),
        )
        self.action_resource_client = self.create_client(
            ExecuteActionResource,
            os.environ.get("LINGXI_ACTION_RESOURCE_SERVICE", DEFAULT_ACTION_RESOURCE_SERVICE),
        )
        self.sing_media_file = os.environ.get("LINGXI_SING_MEDIA_FILE", "").strip()
        self.sing_sample_rate = int(os.environ.get("LINGXI_SING_SAMPLE_RATE", "0"))
        self.sing_enabled = parse_bool(os.environ.get("LINGXI_SING_ENABLED", ""), DEFAULT_SING_ENABLED)
        self.sing_mode = normalize_sing_mode(os.environ.get("LINGXI_SING_MODE", DEFAULT_SING_MODE))
        self.lyrics_dir = os.environ.get("LINGXI_LYRICS_DIR", DEFAULT_LYRICS_DIR).strip()
        self.song_catalog = parse_song_catalog(os.environ.get("LINGXI_SONG_CATALOG", DEFAULT_SONG_CATALOG))
        self.default_song = os.environ.get("LINGXI_DEFAULT_SONG", "twinkle").strip() or "twinkle"
        self.dance_resource_key = os.environ.get("LINGXI_DANCE_RESOURCE_KEY", "").strip()
        self.dance_resource_version = os.environ.get("LINGXI_DANCE_RESOURCE_VERSION", "").strip()
        self.dance_catalog = parse_dance_catalog(os.environ.get("LINGXI_DANCE_CATALOG", DEFAULT_DANCE_CATALOG))
        self.disabled_dances = parse_csv_set(os.environ.get("LINGXI_DISABLED_DANCES", DEFAULT_DISABLED_DANCES))
        self.random_dance_keys = [
            key for key in os.environ.get("LINGXI_RANDOM_DANCE_KEYS", DEFAULT_RANDOM_DANCE_KEYS).split(",")
            if key.strip()
        ]
        self.dance_prepare_text = os.environ.get("LINGXI_DANCE_PREPARE_TEXT", DEFAULT_DANCE_PREPARE_TEXT).strip()
        self.dance_prepare_delay_sec = float(os.environ.get(
            "LINGXI_DANCE_PREPARE_DELAY_SEC",
            str(DEFAULT_DANCE_PREPARE_DELAY_SEC),
        ))
        self.default_dance = os.environ.get("LINGXI_DEFAULT_DANCE", "happy").strip() or "happy"
        self.dance_motion = int(os.environ.get(
            "LINGXI_DANCE_MOTION",
            str(McPresetMotion.INTERACTION_BASSDANCE1),
        ))
        self._tts_lock = threading.Lock()
        self._last_tts_at = 0.0
        self._tts_min_interval_sec = float(os.environ.get("LINGXI_TTS_MIN_INTERVAL_SEC", "3.0"))
        self._last_tts_result: dict[str, Any] | None = None
        self._last_entertainment_result: dict[str, Any] | None = None
        self._last_interaction: dict[str, Any] | None = None

        self.server = ThreadingHTTPServer((host, self.port), BridgeHttpHandler)
        self.server.bridge = self
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.get_logger().info(
            f"Lingxi X2 robot bridge listening on http://{host}:{port}, "
            f"tts_service={self.tts.service_name}, tts_domain={self.tts.domain}"
        )

    def destroy_node(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        super().destroy_node()

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "bridge": "lingxi_x2_robot_bridge",
            "tts_service": self.tts.service_name,
            "tts_ready": self.tts.service_ready(),
            "entertainment": {
                "audio_service": self.audio_client.srv_name,
                "audio_ready": self.audio_client.service_is_ready()
                or self.audio_client.wait_for_service(timeout_sec=0.05),
                "media_service": self.media_client.srv_name,
                "media_ready": self.media_client.service_is_ready()
                or self.media_client.wait_for_service(timeout_sec=0.05),
                "motion_service": self.motion_client.srv_name,
                "motion_ready": self.motion_client.service_is_ready()
                or self.motion_client.wait_for_service(timeout_sec=0.05),
                "mc_action_service": self.mc_action_client.srv_name,
                "mc_action_ready": self.mc_action_client.service_is_ready()
                or self.mc_action_client.wait_for_service(timeout_sec=0.05),
                "action_resource_service": self.action_resource_client.srv_name,
                "action_resource_ready": self.action_resource_client.service_is_ready()
                or self.action_resource_client.wait_for_service(timeout_sec=0.05),
                "sing_enabled": self.sing_enabled,
                "sing_configured": self.sing_enabled and bool(self.song_catalog or self.sing_media_file),
                "sing_mode": self.sing_mode,
                "songs": self.song_resources(),
                "default_song": self.default_song,
                "dance_resource_configured": bool(self.dance_catalog or self.dance_resource_key),
                "dances": self.dance_resources(),
                "disabled_dances": sorted(self.disabled_dances),
                "random_dance_keys": self.available_random_dance_keys(),
                "dance_prepare_text": self.dance_prepare_text,
                "dance_prepare_delay_sec": self.dance_prepare_delay_sec,
                "default_dance": self.default_dance,
                "dance_motion": self.dance_motion,
                "last_result": self._last_entertainment_result,
            },
            "last_tts": self._last_tts_result,
            "last_interaction": self._last_interaction,
        }

    def proxy_navigation(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(
            self.nav_proxy_url + path,
            data=body,
            method="GET" if payload is None else "POST",
            headers={"Content-Type": "application/json; charset=utf-8"} if body is not None else {},
        )
        try:
            with urllib_request.urlopen(request, timeout=6.0) as response:
                raw = response.read().decode("utf-8")
                return response.status, json.loads(raw) if raw else {}
        except urllib_error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                result = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                result = {"ok": False, "message": raw or str(exc)}
            return exc.code, result
        except Exception as exc:
            return 502, {
                "ok": False,
                "status": "navigation_bridge_offline",
                "message": f"导航服务未连接：{exc}",
            }

    def resources(self) -> dict[str, Any]:
        return {
            "ok": True,
            "sing_enabled": self.sing_enabled,
            "sing_mode": self.sing_mode,
            "songs": self.song_resources(),
            "dances": self.dance_resources(),
            "disabled_dances": sorted(self.disabled_dances),
            "random_dance_keys": self.available_random_dance_keys(),
            "dance_prepare_text": self.dance_prepare_text,
            "dance_prepare_delay_sec": self.dance_prepare_delay_sec,
            "notes": {
                "tts": "robot voice via AimDK PlayTts",
                "audio": "uploaded WAV files via AimDK PlayAudioFile",
                "lyrics_dir": self.lyrics_dir,
            },
        }

    def song_resources(self) -> list[dict[str, Any]]:
        result = []
        for key in sorted(self.song_catalog.keys()):
            result.append({
                "key": key,
                "name": SONG_DISPLAY_NAMES.get(key, key),
                "mode": self.sing_mode,
                "audio_file": self.song_catalog.get(key, ""),
                "lyrics_file": os.path.join(self.lyrics_dir, f"{key}.txt") if self.lyrics_dir else "",
                "lyrics_file_exists": bool(self.lyrics_dir) and os.path.isfile(
                    os.path.join(self.lyrics_dir, f"{key}.txt")
                ),
                "triggers": self.song_triggers(key),
            })
        return result

    def dance_resources(self) -> list[dict[str, Any]]:
        result = []
        for key in sorted(self.dance_catalog.keys()):
            resource_key, version = self.dance_catalog[key]
            result.append({
                "key": key,
                "name": DANCE_DISPLAY_NAMES.get(key, key),
                "resource_key": resource_key,
                "resource_version": version,
                "available": key not in self.disabled_dances,
            })
        return result

    def available_random_dance_keys(self) -> list[str]:
        keys = [key.strip() for key in self.random_dance_keys if key.strip()]
        return [key for key in keys if key in self.dance_catalog and key not in self.disabled_dances]

    def song_triggers(self, key: str) -> list[str]:
        return {
            "twinkle": ["唱小星星", "小星星"],
            "happy_birthday": ["唱生日快乐", "生日快乐"],
            "dongfanghong": ["唱东方红", "东方红"],
        }.get(key, [key])

    def speak(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text", "")).strip()
        if not text:
            return {"ok": False, "accepted": False, "status": "empty_text", "message": "text is required"}

        event_id = str(payload.get("event_id", "")).strip()
        force = bool(payload.get("force", False))
        requested_interrupt = bool(payload.get("interrupt", True))
        interrupt = True
        now = time.monotonic()
        with self._tts_lock:
            elapsed = now - self._last_tts_at
            if not force and elapsed < self._tts_min_interval_sec:
                retry_after = self._tts_min_interval_sec - elapsed
                self.get_logger().info(
                    f"TTS throttled: retry_after={retry_after:.1f}s, event_id={event_id or '<none>'}, text={text}"
                )
                return {
                    "ok": False,
                    "accepted": False,
                    "status": "throttled",
                    "retry_after_sec": round(retry_after, 3),
                    "message": f"TTS throttled; retry after {retry_after:.1f}s",
                }
            self._last_tts_at = now

        self.get_logger().info(
            f"TTS speak: event_id={event_id or '<none>'}, requested_interrupt={requested_interrupt}, "
            f"interrupt={interrupt}, force={force}, text={text}"
        )
        future = self.tts.speak(
            text,
            interrupt=interrupt,
            force=force,
            trace_id=event_id or None,
            done_callback=self._record_tts_result,
        )
        if future is None:
            with self._tts_lock:
                self._last_tts_at = 0.0
            return {
                "ok": False,
                "accepted": False,
                "status": "tts_unavailable",
                "message": "TTS service is not available",
            }
        return {
            "ok": True,
            "accepted": True,
            "status": "submitted",
            "trace_id": event_id or "",
            "mode": "async_playtts",
        }

    def mark_interaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._last_interaction = {
            "state": "marked",
            "event_id": str(payload.get("event_id", "")),
            "source": str(payload.get("source", "")),
            "intent": str(payload.get("intent", "")),
            "at": time.time(),
        }
        return {"ok": True, "accepted": True, "state": "marked"}

    def end_interaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._last_interaction = {
            "state": "idle",
            "event_id": str(payload.get("event_id", "")),
            "source": str(payload.get("source", "")),
            "intent": str(payload.get("intent", "")),
            "at": time.time(),
        }
        return {"ok": True, "accepted": True, "state": "idle"}

    def sing(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id", "")).strip()
        text = str(payload.get("text", "")).strip()
        if not self.sing_enabled:
            return {
                "ok": True,
                "accepted": False,
                "status": "disabled",
                "trace_id": event_id,
                "text": text,
                "message": "唱歌功能暂时关闭了，我现在可以给你跳舞。",
            }
        song_key = select_catalog_key(text, {
            "twinkle": ("twinkle", "star", "xiaoxingxing", "\u5c0f\u661f\u661f"),
            "happy_birthday": ("birthday", "shengri", "\u751f\u65e5", "\u751f\u65e5\u5feb\u4e50"),
            "dongfanghong": ("dongfang", "\u4e1c\u65b9\u7ea2"),
        }, self.default_song)
        if self.sing_mode == "tts":
            return self._sing_tts(event_id, song_key)
        audio_file = self.song_catalog.get(song_key) or self.song_catalog.get(self.default_song, "")
        if audio_file:
            return self._sing_audio_file(event_id, song_key, audio_file)
        if not self.sing_media_file:
            return {
                "ok": False,
                "accepted": False,
                "status": "not_configured",
                "message": "LINGXI_SONG_CATALOG or LINGXI_SING_MEDIA_FILE is not configured",
            }
        return self._sing_media_file(event_id)

    def _sing_tts(self, event_id: str, song_key: str) -> dict[str, Any]:
        trace_id = event_id or f"sing_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        text = self.load_song_text(song_key) or SONG_TTS_PERFORMANCES.get(
            song_key,
            f"我用自己的声音给大家唱{SONG_DISPLAY_NAMES.get(song_key, song_key)}。啦啦啦。",
        )
        future = self.tts.speak(
            text,
            interrupt=True,
            force=True,
            trace_id=trace_id,
            done_callback=self._record_tts_result,
        )
        if future is None:
            return {
                "ok": False,
                "accepted": False,
                "status": "tts_unavailable",
                "message": "TTS service is not available",
            }
        self.get_logger().info(
            f"Sing TTS submitted: trace_id={trace_id}, song={song_key}, text={text}"
        )
        return {
            "ok": True,
            "accepted": True,
            "status": "submitted",
            "trace_id": trace_id,
            "mode": "play_tts_song",
            "song": song_key,
            "name": SONG_DISPLAY_NAMES.get(song_key, song_key),
        }

    def load_song_text(self, song_key: str) -> str:
        if not self.lyrics_dir:
            return ""
        path = os.path.join(self.lyrics_dir, f"{song_key}.txt")
        try:
            with open(path, "r", encoding="utf-8") as file:
                text = file.read().strip()
        except OSError:
            return ""
        return text[:500]

    def _sing_audio_file(self, event_id: str, song_key: str, audio_file: str) -> dict[str, Any]:
        if not self.audio_client.wait_for_service(timeout_sec=1.0):
            return {
                "ok": False,
                "accepted": False,
                "status": "audio_service_unavailable",
                "message": f"audio service is not available: {self.audio_client.srv_name}",
            }

        trace_id = event_id or f"sing_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        request = PlayAudioFile.Request()
        now = self.get_clock().now().to_msg()
        request.request.header.stamp = now
        request.file.pkg_name = os.environ.get("LINGXI_AUDIO_PKG_NAME", DEFAULT_AUDIO_PKG_NAME)
        request.file.file_name = os.path.basename(audio_file)
        request.file.file_path = os.path.dirname(audio_file).rstrip("/") + "/"
        request.file.priority = 6
        request.file.priority_weight = 0
        future = self.audio_client.call_async(request)
        future.add_done_callback(
            lambda fut: self._record_entertainment_result(fut, trace_id, "sing_audio", audio_file)
        )
        self.get_logger().info(
            f"Sing audio submitted: trace_id={trace_id}, song={song_key}, file={audio_file}"
        )
        return {
            "ok": True,
            "accepted": True,
            "status": "submitted",
            "trace_id": trace_id,
            "mode": "play_audio_file",
            "song": song_key,
            "file_name": audio_file,
        }

    def _sing_media_file(self, event_id: str) -> dict[str, Any]:
        if not self.media_client.wait_for_service(timeout_sec=1.0):
            return {
                "ok": False,
                "accepted": False,
                "status": "media_service_unavailable",
                "message": f"media service is not available: {self.media_client.srv_name}",
            }

        trace_id = event_id or f"sing_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        request = PlayMediaFile.Request()
        now = self.get_clock().now().to_msg()
        request.header.header.stamp = now
        request.media_file_req.file_name = self.sing_media_file
        request.media_file_req.sample_rate = self.sing_sample_rate
        request.media_file_req.priority_level.value = TtsPriorityLevel.INTERACTION_L6
        request.media_file_req.priority_weight = 0
        request.media_file_req.domain = DEFAULT_ENTERTAINMENT_DOMAIN
        request.media_file_req.trace_id = trace_id
        request.media_file_req.is_interrupted = True
        future = self.media_client.call_async(request)
        future.add_done_callback(
            lambda fut: self._record_entertainment_result(fut, trace_id, "sing", self.sing_media_file)
        )
        self.get_logger().info(f"Sing media submitted: trace_id={trace_id}, file={self.sing_media_file}")
        return {
            "ok": True,
            "accepted": True,
            "status": "submitted",
            "trace_id": trace_id,
            "mode": "play_media_file",
            "file_name": self.sing_media_file,
        }

    def dance(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id", "")).strip()
        text = str(payload.get("text", "")).strip()
        if is_generic_dance_request(text):
            available_keys = self.available_random_dance_keys()
            dance_key = random.choice(available_keys) if available_keys else self.default_dance
        else:
            dance_key = select_catalog_key(text, DANCE_ALIASES, self.default_dance)
        if dance_key in self.disabled_dances:
            dance_name = DANCE_DISPLAY_NAMES.get(dance_key, dance_key)
            return {
                "ok": True,
                "accepted": False,
                "status": "resource_unavailable",
                "trace_id": event_id,
                "dance": dance_key,
                "name": dance_name,
                "message": f"{dance_name}当前没有可用的灵创动作资源文件，可以先试试太极、快乐之舞、律动纪元、爱你有声或卡拉OK。",
            }
        if dance_key in self.dance_catalog:
            resource_key, resource_version = self.dance_catalog[dance_key]
            return self._prepare_then_dance_action_resource(event_id, resource_key, resource_version, dance_key)
        if self.dance_resource_key:
            return self._prepare_then_dance_action_resource(
                event_id, self.dance_resource_key, self.dance_resource_version, "configured"
            )
        return self._prepare_then_dance_preset_motion(event_id)

    def _prepare_then_dance_action_resource(
        self,
        event_id: str,
        resource_key: str,
        resource_version: str,
        dance_key: str,
    ) -> dict[str, Any]:
        if not self.action_resource_client.wait_for_service(timeout_sec=1.0):
            return {
                "ok": False,
                "accepted": False,
                "status": "action_resource_service_unavailable",
                "message": f"action resource service is not available: {self.action_resource_client.srv_name}",
            }
        trace_id = event_id or f"dance_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        dance_name = DANCE_DISPLAY_NAMES.get(dance_key, dance_key)
        self._speak_dance_prepare(trace_id)
        threading.Thread(
            target=self._delayed_dance_action_resource,
            args=(trace_id, resource_key, resource_version, dance_key),
            daemon=True,
        ).start()
        self.get_logger().info(
            f"Dance scheduled: trace_id={trace_id}, dance={dance_key}, name={dance_name}, "
            f"delay_sec={self.dance_prepare_delay_sec}"
        )
        return {
            "ok": True,
            "accepted": True,
            "status": "scheduled",
            "trace_id": trace_id,
            "mode": "prepare_then_execute_action_resource",
            "dance": dance_key,
            "name": dance_name,
            "resource_key": resource_key,
            "resource_version": resource_version,
            "prepare_text": self.dance_prepare_text,
            "delay_sec": self.dance_prepare_delay_sec,
        }

    def _delayed_dance_action_resource(
        self,
        trace_id: str,
        resource_key: str,
        resource_version: str,
        dance_key: str,
    ) -> None:
        if self.dance_prepare_delay_sec > 0:
            time.sleep(self.dance_prepare_delay_sec)
        self._request_stable_stand(trace_id)
        time.sleep(float(os.environ.get("LINGXI_DANCE_STAND_SETTLE_SEC", "1.0")))
        self._dance_action_resource(trace_id, resource_key, resource_version, dance_key)

    def _speak_dance_prepare(self, trace_id: str) -> None:
        if not self.dance_prepare_text:
            return
        future = self.tts.speak(
            self.dance_prepare_text,
            interrupt=True,
            force=True,
            trace_id=f"{trace_id}_prepare",
            domain=DEFAULT_ENTERTAINMENT_DOMAIN,
        )
        if future is not None:
            future.add_done_callback(
                lambda fut: self._record_tts_result(fut, f"{trace_id}_prepare", self.dance_prepare_text)
            )

    def _prepare_then_dance_preset_motion(self, event_id: str) -> dict[str, Any]:
        if not self.motion_client.wait_for_service(timeout_sec=1.0):
            return {
                "ok": False,
                "accepted": False,
                "status": "motion_service_unavailable",
                "message": f"preset motion service is not available: {self.motion_client.srv_name}",
            }
        trace_id = event_id or f"dance_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        self._speak_dance_prepare(trace_id)
        threading.Thread(
            target=self._delayed_dance_preset_motion,
            args=(trace_id,),
            daemon=True,
        ).start()
        return {
            "ok": True,
            "accepted": True,
            "status": "scheduled",
            "trace_id": trace_id,
            "mode": "prepare_then_set_mc_preset_motion",
            "motion": self.dance_motion,
            "name": "默认舞蹈",
            "prepare_text": self.dance_prepare_text,
            "delay_sec": self.dance_prepare_delay_sec,
        }

    def _delayed_dance_preset_motion(self, trace_id: str) -> None:
        if self.dance_prepare_delay_sec > 0:
            time.sleep(self.dance_prepare_delay_sec)
        self._request_stable_stand(trace_id)
        time.sleep(float(os.environ.get("LINGXI_DANCE_STAND_SETTLE_SEC", "1.0")))
        self._dance_preset_motion(trace_id)

    def _request_stable_stand(self, trace_id: str) -> None:
        if not self.mc_action_client.wait_for_service(timeout_sec=0.5):
            self.get_logger().warning(f"SetMcAction service unavailable before dance: trace_id={trace_id}")
            return
        request = SetMcAction.Request()
        request.header.stamp = self.get_clock().now().to_msg()
        request.source = DEFAULT_ENTERTAINMENT_DOMAIN
        request.command = McActionCommand()
        request.command.action_desc = os.environ.get("LINGXI_DANCE_PRE_ACTION", "STAND_DEFAULT")
        future = self.mc_action_client.call_async(request)
        future.add_done_callback(lambda fut: self._log_stable_stand_result(fut, trace_id))
        self.get_logger().info(
            f"Stable stand requested before dance: trace_id={trace_id}, action_desc={request.command.action_desc}"
        )

    def _log_stable_stand_result(self, future, trace_id: str) -> None:
        try:
            response = future.result()
            result = parse_entertainment_response(response)
        except Exception as exc:
            self.get_logger().warning(f"Stable stand request failed: trace_id={trace_id}, error={exc}")
            return
        if result.get("accepted"):
            self.get_logger().info(f"Stable stand accepted: trace_id={trace_id}, result={result}")
        else:
            self.get_logger().warning(f"Stable stand rejected: trace_id={trace_id}, result={result}")

    def _dance_action_resource(
        self,
        event_id: str,
        resource_key: str,
        resource_version: str,
        dance_key: str,
    ) -> dict[str, Any]:
        if not self.action_resource_client.wait_for_service(timeout_sec=1.0):
            return {
                "ok": False,
                "accepted": False,
                "status": "action_resource_service_unavailable",
                "message": f"action resource service is not available: {self.action_resource_client.srv_name}",
            }

        trace_id = event_id or f"dance_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        request = ExecuteActionResource.Request()
        now = self.get_clock().now().to_msg()
        request.header.header.stamp = now
        request.resource_key = resource_key
        request.resource_version = resource_version
        request.meta = json.dumps({
            # AimDK's LinkCraft example expects the historical spelling.
            "resource_type": "BODY_MONTION",
            "trace_id": trace_id,
            "source": DEFAULT_ENTERTAINMENT_DOMAIN,
        })
        future = self.action_resource_client.call_async(request)
        future.add_done_callback(
            lambda fut: self._record_entertainment_result(fut, trace_id, "dance_resource", resource_key)
        )
        self.get_logger().info(
            f"Dance resource submitted: trace_id={trace_id}, dance={dance_key}, resource_key={resource_key}, "
            f"resource_version={resource_version or '<default>'}"
        )
        return {
            "ok": True,
            "accepted": True,
            "status": "submitted",
            "trace_id": trace_id,
            "mode": "execute_action_resource",
            "dance": dance_key,
            "name": DANCE_DISPLAY_NAMES.get(dance_key, dance_key),
            "resource_key": resource_key,
            "resource_version": resource_version,
        }

    def debug_preset_motion(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or payload.get("eventId") or "").strip()
        motion = int(payload.get("motion") or self.dance_motion)
        return self._dance_preset_motion(event_id or f"debug_motion_{motion}", motion=motion, name="调试预置动作")

    def debug_action_resource(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or payload.get("eventId") or "").strip()
        resource_key = str(payload.get("resource_key") or payload.get("resourceKey") or "").strip()
        resource_version = str(payload.get("resource_version") or payload.get("resourceVersion") or "0.0.1").strip()
        dance_key = str(payload.get("dance") or payload.get("name") or "debug").strip()
        if not resource_key:
            return {
                "ok": False,
                "accepted": False,
                "status": "missing_resource_key",
                "message": "resource_key is required",
            }
        return self._dance_action_resource(
            event_id or f"debug_resource_{resource_key}_{int(time.time() * 1000)}",
            resource_key,
            resource_version,
            dance_key,
        )

    def _dance_preset_motion(self, event_id: str, *, motion: int | None = None, name: str = "默认舞蹈") -> dict[str, Any]:
        if not self.motion_client.wait_for_service(timeout_sec=1.0):
            return {
                "ok": False,
                "accepted": False,
                "status": "motion_service_unavailable",
                "message": f"preset motion service is not available: {self.motion_client.srv_name}",
            }

        trace_id = event_id or f"dance_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        request = SetMcPresetMotion.Request()
        now = self.get_clock().now().to_msg()
        request.header.stamp = now
        request.area.value = int(os.environ.get("LINGXI_DANCE_MOTION_AREA", "11"))
        selected_motion = int(motion if motion is not None else self.dance_motion)
        request.motion.value = selected_motion
        request.interrupt = True
        request.ani_path = ""
        request.play_timestamp = 0
        future = self.motion_client.call_async(request)
        future.add_done_callback(
            lambda fut: self._record_entertainment_result(fut, trace_id, "dance", str(selected_motion))
        )
        self.get_logger().info(f"Dance motion submitted: trace_id={trace_id}, motion={selected_motion}")
        return {
            "ok": True,
            "accepted": True,
            "status": "submitted",
            "trace_id": trace_id,
            "mode": "set_mc_preset_motion",
            "motion": selected_motion,
            "name": name,
        }

    def unsupported_control(self) -> dict[str, Any]:
        return {
            "ok": False,
            "accepted": False,
            "status": "unsupported",
            "message": "navigation/task control is disabled in this Lingxi X2 live bridge",
        }

    def _record_tts_result(self, future, trace_id: str, text: str) -> None:
        try:
            response = future.result()
            result = parse_tts_response(response)
        except Exception as exc:
            result = {
                "ok": False,
                "accepted": False,
                "status": "exception",
                "trace_id": trace_id,
                "error": str(exc),
                "estimated_duration": 0,
            }
        result.update({"trace_id": trace_id, "text": text, "at": time.time()})
        self._last_tts_result = result
        if result.get("accepted"):
            self.get_logger().info(f"TTS result accepted: {result}")
        else:
            self.get_logger().warning(f"TTS result rejected: {result}")

    def _record_entertainment_result(self, future, trace_id: str, kind: str, detail: str) -> None:
        try:
            response = future.result()
            result = parse_entertainment_response(response)
        except Exception as exc:
            result = {
                "ok": False,
                "accepted": False,
                "status": "exception",
                "error": str(exc),
            }
        result.update({"trace_id": trace_id, "kind": kind, "detail": detail, "at": time.time()})
        self._last_entertainment_result = result
        if result.get("accepted"):
            self.get_logger().info(f"Entertainment result accepted: {result}")
        else:
            self.get_logger().warning(f"Entertainment result rejected: {result}")


class BridgeHttpHandler(BaseHTTPRequestHandler):
    """Convert the small HTTP API to LingxiRobotBridge method calls."""

    def do_GET(self) -> None:
        if self.path == "/health":
            self.write_json(200, self.server.bridge.health())
            return
        if self.path == "/gateway/health":
            voice = self.server.bridge.health()
            nav_status, navigation = self.server.bridge.proxy_navigation("/nav/status")
            voice_ok = bool(voice.get("ok", True))
            navigation_ok = nav_status == 200 and bool(navigation.get("ok", False))
            self.write_json(200, {
                "ok": voice_ok and navigation_ok,
                "services": {
                    "voice": {"ok": voice_ok, "status": voice},
                    "navigation": {"ok": navigation_ok, "status": navigation},
                },
            })
            return
        if self.path in {"/points", "/nav/status", "/localization/current"}:
            status, payload = self.server.bridge.proxy_navigation(self.path)
            self.write_json(status, payload)
            return
        if self.path == "/entertainment/resources":
            self.write_json(200, self.server.bridge.resources())
            return
        self.write_json(404, {"ok": False, "message": "not found"})

    def do_POST(self) -> None:
        try:
            payload = self.read_json()
            if self.path == "/tts/speak":
                self.write_json(200, self.server.bridge.speak(payload))
                return
            if self.path == "/interaction/mark":
                self.write_json(200, self.server.bridge.mark_interaction(payload))
                return
            if self.path == "/interaction/end":
                self.write_json(200, self.server.bridge.end_interaction(payload))
                return
            if self.path == "/entertainment/sing":
                self.write_json(200, self.server.bridge.sing(payload))
                return
            if self.path == "/entertainment/dance":
                self.write_json(200, self.server.bridge.dance(payload))
                return
            if self.path == "/debug/preset-motion":
                self.write_json(200, self.server.bridge.debug_preset_motion(payload))
                return
            if self.path == "/debug/action-resource":
                self.write_json(200, self.server.bridge.debug_action_resource(payload))
                return
            if self.path in {
                "/nav/start",
                "/nav/stop",
                "/nav/map",
                "/nav/mode",
                "/nav/reload",
                "/task/start",
                "/task/pause",
                "/task/resume",
                "/task/stop",
                "/task/end",
                "/auto-return/cancel",
            }:
                status, result = self.server.bridge.proxy_navigation(self.path, payload)
                self.write_json(status, result)
                return
            self.write_json(404, {"ok": False, "message": "not found"})
        except Exception as exc:
            self.write_json(500, {"ok": False, "message": str(exc)})

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        self.server.bridge.get_logger().info(fmt % args)


def parse_tts_response(response) -> dict[str, Any]:
    tts_resp = getattr(response, "tts_resp", None)
    accepted = bool(getattr(tts_resp, "is_success", False))
    return {
        "ok": accepted,
        "accepted": accepted,
        "status": "accepted" if accepted else "rejected",
        "error": getattr(tts_resp, "error_message", ""),
        "estimated_duration": getattr(tts_resp, "estimated_duration", 0),
    }


def parse_entertainment_response(response) -> dict[str, Any]:
    audio_resp = getattr(response, "reponse", None)
    if audio_resp is not None and hasattr(audio_resp, "status"):
        state = getattr(getattr(audio_resp, "status", None), "value", 0)
        code = getattr(audio_resp, "code", 0)
        accepted = state in {1, 200, 300, 400} and code == 0
        return {
            "ok": accepted,
            "accepted": accepted,
            "status": "accepted" if accepted else "rejected",
            "state": state,
            "code": code,
        }
    if hasattr(response, "tts_resp"):
        tts_resp = getattr(response, "tts_resp", None)
        accepted = bool(getattr(tts_resp, "is_success", False))
        return {
            "ok": accepted,
            "accepted": accepted,
            "status": "accepted" if accepted else "rejected",
            "error": getattr(tts_resp, "error_message", ""),
            "estimated_duration": getattr(tts_resp, "estimated_duration", 0),
        }
    common_resp = getattr(response, "header", None)
    if common_resp is not None and hasattr(common_resp, "status"):
        state = getattr(getattr(common_resp, "status", None), "value", 0)
        code = getattr(getattr(common_resp, "header", None), "code", 0)
        message = getattr(common_resp, "message", "")
        accepted = state in {1, 200, 300, 400} and code == 0
        return {
            "ok": accepted,
            "accepted": accepted,
            "status": "accepted" if accepted else "rejected",
            "state": state,
            "code": code,
            "message": message,
        }
    task_resp = getattr(response, "response", None)
    state = getattr(getattr(task_resp, "state", None), "value", 0)
    code = getattr(task_resp, "code", 0)
    accepted = state in {1, 200, 300, 400} and code == 0
    return {
        "ok": accepted,
        "accepted": accepted,
        "status": "accepted" if accepted else "rejected",
        "state": state,
        "code": code,
        "task_id": getattr(task_resp, "task_id", 0),
    }


def main() -> None:
    host = os.environ.get("LINGXI_BRIDGE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("LINGXI_BRIDGE_PORT", str(DEFAULT_PORT)))
    rclpy.init()
    node = LingxiRobotBridge(host=host, port=port)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
