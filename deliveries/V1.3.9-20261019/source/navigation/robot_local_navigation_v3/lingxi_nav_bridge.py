"""灵犀 Java Agent 与机器人本地 ROS2 服务之间的 HTTP 桥接节点。

Java 进程通过 HTTP 调用本节点；本节点复用 robot_local_navigation_v3
中的点位目录、导航客户端和 TTS 播报模块，最终与机器人本地 ROS2 topic/service 通信。
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import rclpy
from rclpy.node import Node

from robot_local_navigation_v3.config_store import UnifiedConfigStore
from robot_local_navigation_v3.linkcraft_resources import (
    DEFAULT_RESOURCE_CONFIG_PATH,
    MAX_ACTION_DURATION_SEC,
    discover_linkcraft_resources,
    normalize_linkcraft_action,
)
from robot_local_navigation_v3.map_point_store import MapPointStore
from robot_local_navigation_v3.motion_option_policy import ArrivalMotionTracker, is_empty_motion
from robot_local_navigation_v3.navigation_client import NavigationClient, NavigationSessionHandle
from robot_local_navigation_v3.navigation_events import NavigationEvent, NavigationEventType
from robot_local_navigation_v3.point_catalog import PointCatalog, PointCatalogError
from robot_local_navigation_v3.sdk_motion_controller import DEFAULT_EMOJI_PRIORITY, SdkMotionController
from robot_local_navigation_v3.tour_step_actions import merge_task_arrival_actions
from robot_local_navigation_v3.tour_commands import (
    build_task_command_catalog,
    match_task_command,
)
from robot_local_navigation_v3.tour_step_actions import (
    DEFAULT_TASK_WAIT_SEC,
    normalize_task_loop_count,
    normalize_task_wait_sec,
)
from robot_local_navigation_v3.tour_interaction_policy import (
    WAKE_ACTION_BLOCK,
    WAKE_ACTION_PAUSE,
    VOICE_INTERRUPTION_PAUSE_ORIGIN,
    VOICE_INTERRUPTION_RESUME_SEC,
    TOUR_INTERACTION_FALLBACK_SEC,
    should_block_voice_output,
    should_auto_resume_voice_interruption,
    should_protect_tour_interaction,
    wake_interaction_action,
)
from robot_local_navigation_v3.tts_speaker import TtsSpeaker
from robot_local_navigation_v3.voice_preset_motion import (
    resolve_voice_preset_motion,
    voice_preset_motion_block_reason,
)


# 自动返航状态机。状态值会出现在 /nav/status 返回里，外部调用方可能依赖这些字符串。
AUTO_RETURN_IDLE = "idle"
AUTO_RETURN_NAVIGATING_USER_TARGET = "navigating_user_target"
AUTO_RETURN_NAVIGATING_TASK_STEP = "navigating_task_step"
AUTO_RETURN_TASK_PAUSED = "task_paused"
AUTO_RETURN_ARRIVAL_ACTION = "arrival_action"
AUTO_RETURN_TASK_ARRIVAL_ACTION = "task_arrival_action"
AUTO_RETURN_TASK_LINKCRAFT_ACTION = "task_linkcraft_action"
AUTO_RETURN_TASK_WAITING = "task_waiting"
AUTO_RETURN_WAITING = "waiting_auto_return"
AUTO_RETURN_INTERACTION_ACTIVE = "interaction_active"
AUTO_RETURN_RETURNING_HOME = "returning_home"

ARRIVAL_SETTLE_SEC = 2.5
ARRIVAL_COMPLETION_GUARD_SEC = 1.5
ARRIVAL_INTERRUPT_RELEASE_GUARD_SEC = 1.5
ARRIVAL_TTS_MAX_WAIT_SEC = 180.0
LINKCRAFT_COMPLETION_GUARD_SEC = 2.0
LINKCRAFT_SUBMIT_TIMEOUT_SEC = 6.0
LINKCRAFT_BUSY_RETRY_DELAY_SEC = 1.5


class LingxiNavBridge(Node):
    """对外暴露导航和 TTS HTTP API 的 ROS2 节点。"""

    def __init__(
        self,
        *,
        config_json: str,
        active_map_name: str | None,
        nav_mode: str | None,
        host: str,
        port: int,
    ) -> None:
        super().__init__("lingxi_nav_bridge")

        # 统一配置文件保存可用地图和默认导航参数；每张地图的点位数据放在同级 maps/ 目录下。
        self.config_store = UnifiedConfigStore(config_json)
        self.point_store = MapPointStore(self.config_store.file_path.parent / "maps")
        self.active_map_state_path = self.config_store.file_path.parent / "active_map_name.txt"
        self.config = self.config_store.load()
        # 兼容旧版配置：如果点位仍写在统一配置里，启动时迁移到独立地图点位文件。
        if self.point_store.migrate_from_unified(self.config):
            self.config_store.save(self.config)

        self.active_map_name = self._resolve_active_map_name(active_map_name)
        points = self._load_active_points()
        if not points:
            self.get_logger().warning(
                f"No navigation points found for map: {self.active_map_name or '<all>'}; "
                "bridge will stay online for map and point configuration"
            )

        self.point_catalog = PointCatalog(points)
        self.odom_topic = os.environ.get("NAV_ODOM_TOPIC", "/slam/lidar_odom").strip() or "/slam/lidar_odom"
        self.navigation_client = NavigationClient(
            self,
            defaults=self.config.navigation,
            mode=nav_mode or self.config.app.nav_mode,
            odom_topic=self.odom_topic,
        )
        # 语音播报和动作控制都通过本地 AimDK/ROS2 服务完成。
        self.tts_speaker = TtsSpeaker(self)
        self.motion_controller = SdkMotionController(self)
        self.linkcraft_resource_config_path = Path(os.environ.get(
            "LINKCRAFT_RESOURCE_CONFIG_PATH",
            str(DEFAULT_RESOURCE_CONFIG_PATH),
        )).expanduser()
        # TTS 做最小间隔限流，避免灵犀连续回复时抢占机器人播放通道。
        self._tts_lock = threading.Lock()
        self._last_tts_at = 0.0
        self._tts_min_interval_sec = 5.0
        # 自动返航流程：到达目标点后等待业务动作完成，再在空闲超时后返回起点。
        self.auto_return_enabled = True
        self.auto_return_idle_sec = 30.0
        self._auto_return_lock = threading.RLock()
        self._auto_return_state = AUTO_RETURN_IDLE
        self._last_valid_interaction_at = time.monotonic()
        self._arrival_action_timer = None
        self._task_failure_timer = None
        self._auto_return_timer = None
        self._auto_return_generation = 0
        self._auto_return_source_goal_id = ""
        self._auto_return_target_id = ""
        self._auto_return_reason = ""
        self._task_id = ""
        self._task_name = ""
        self._task_steps: list[dict[str, Any]] = []
        self._task_index = -1
        self._task_loop_count = 1
        self._task_loop_index = 0
        self._task_navigation_task_id: int | None = None
        self._task_current_goal_id = ""
        self._task_last_goal_id = ""
        self._task_cancel_requested = False
        self._task_failure_recovery_pending = False
        self._task_failure_retry_count = 0
        self._task_skipped_steps: list[dict[str, Any]] = []
        self._task_last_failure: dict[str, Any] | None = None
        self._task_resume_index = -1
        self._task_pause_origin = ""
        self._task_interruption_wait_started_at = 0.0
        self._task_paused_phase = ""
        self._task_arrival_event: NavigationEvent | None = None
        self._task_arrival_effects_until = 0.0
        self._task_linkcraft_completed = False
        self._tour_interaction_event_id = ""
        self._tour_interaction_ended = True
        self._tour_quiet_since = time.monotonic()
        self._tour_reply_generation = 0
        self._tour_reply_until = 0.0
        self._task_wait_started_at = 0.0
        self._task_wait_sec = 0.0
        self._task_paused_wait_sec = 0.0
        self._task_wait_interaction_active = False
        self._looping_arrival_emoji_active = False
        self._arrival_motion = ArrivalMotionTracker()
        self._linkcraft_action: dict[str, Any] | None = None
        self._linkcraft_wait_started_at = 0.0
        self._linkcraft_wait_sec = 0.0

        # HTTP server 是阻塞式的，因此放到守护线程里；主线程继续执行 rclpy.spin()，
        # 以便处理 ROS 定时器、订阅和服务回调。
        self.server = ThreadingHTTPServer((host, int(port)), NavHttpHandler)
        self.server.bridge = self
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.get_logger().info(
            f"Lingxi nav bridge listening on http://{host}:{port}, "
            f"active_map={self.active_map_name or '<all>'}, mode={nav_mode or self.config.app.nav_mode}"
        )

    def destroy_node(self) -> None:
        """关闭 HTTP 服务并清理 ROS 节点资源。"""

        self._cancel_auto_return_timers()
        with self._auto_return_lock:
            self._cancel_task_failure_timer_locked()
        self._restore_looping_arrival_emoji()
        self.server.shutdown()
        self.server.server_close()
        super().destroy_node()

    def list_point_names(self) -> dict[str, Any]:
        """返回当前地图中可被语音识别使用的点位名称。"""

        return {
            "ok": True,
            "active_map_name": self.active_map_name,
            "point_names": self.point_catalog.list_voice_names(map_name=self.active_map_name),
        }

    def list_task_commands(self) -> dict[str, Any]:
        """Return enabled tour command phrases for the local voice agent."""

        if not self.active_map_name:
            return {
                "ok": True,
                "active_map_name": "",
                "active_task_id": "",
                "tasks": [],
                "conflicts": [],
            }
        payload = self.point_store.load_tour_tasks(self.active_map_name)
        active_task_id = str(payload.get("active_tour_task_id", ""))
        catalog = build_task_command_catalog(
            payload.get("tasks", []),
            active_task_id=active_task_id,
        )
        return {
            "ok": True,
            "active_map_name": self.active_map_name,
            "active_task_id": active_task_id,
            **catalog,
        }

    def match_task_command_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Resolve one ASR sentence to a configured task without a cloud model."""

        text = str(payload.get("text", "")).strip()
        catalog = self.list_task_commands()
        result = match_task_command(text, catalog.get("tasks", []))
        return {
            "ok": True,
            "active_map_name": self.active_map_name,
            **result,
        }

    def list_linkcraft_resources(self) -> dict[str, Any]:
        """Return downloaded LinkCraft resources without executing any of them."""

        try:
            resources = discover_linkcraft_resources(self.linkcraft_resource_config_path)
        except Exception as exc:
            self.get_logger().error(f"读取灵创资源失败: {exc}")
            return {
                "ok": False,
                "message": f"读取灵创资源失败：{exc}",
                "resources": [],
            }
        return {
            "ok": True,
            "resource_count": len(resources),
            "resources": resources,
        }

    def start_navigation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """解析请求中的目标点名称，并启动一次导航任务。"""

        target_name = str(payload.get("target_name", "")).strip()
        if not target_name:
            return {"ok": False, "accepted": False, "status": "no_target", "message": "target_name is required"}
        event_id = str(payload.get("event_id", "")).strip()
        self.get_logger().info(f"/nav/start request: event_id={event_id or '<none>'}, target_name={target_name}")
        with self._auto_return_lock:
            task_state = self._auto_return_state
        if event_id and should_protect_tour_interaction(task_state, "navigate"):
            self.get_logger().info(
                f"Voice navigation ignored while tour is protected: event_id={event_id}, "
                f"target_name={target_name}, task_state={task_state}"
            )
            return {
                "ok": False,
                "accepted": False,
                "status": "tour_protected",
                "message": "讲解任务执行中，语音导航不会打断当前讲解",
                "task": self._task_status(),
            }
        # 启动导航来自真实用户意图，应刷新交互时间并取消挂起的自动返航。
        self._mark_valid_interaction(event_id=event_id, source="nav_start", intent="navigate")
        self._cancel_task_for_new_intent(
            reason=f"preempted_by_nav_start:{event_id or target_name}",
        )

        try:
            # 支持按主名称或语音别名解析点位，解析失败时直接返回给 Java Agent。
            match = self.point_catalog.resolve(target_name, map_name=self.active_map_name)
        except PointCatalogError as exc:
            return {
                "ok": False,
                "accepted": False,
                "status": "point_not_found",
                "message": str(exc),
            }

        self.get_logger().info(
            f"/nav/start resolved: event_id={event_id or '<none>'}, "
            f"target_name={target_name}, point={match.point.name}, point_id={match.point.id}, matched_by={match.matched_by}"
        )
        if self.navigation_client.get_status().get("status") == "idle":
            # 只有客户端空闲时才进入“用户导航中”状态；若已有任务，交给 navigation_client 决定是否拒绝。
            self._begin_user_navigation(match.point.id)
        handle = self.navigation_client.start(match.point, listener=self.handle_navigation_event)
        if not handle.accepted:
            self._finish_rejected_user_navigation(match.point.id)
        result = handle_to_dict(handle)
        result.update({
            "ok": bool(handle.accepted),
            "matched_by": match.matched_by,
            "target_name": match.point.name,
        })
        return result

    def stop_navigation(self, payload: dict[str, Any]) -> dict[str, Any]:
        """停止当前导航任务，并把这次停止记为有效用户交互。"""

        event_id = str(payload.get("event_id", "")).strip()
        self.get_logger().info(f"/nav/stop request: event_id={event_id or '<none>'}")
        self._mark_valid_interaction(
            event_id=event_id,
            source="nav_stop",
            intent="stop_navigation",
        )
        auto_return_cancelled, previous_auto_return_state, _ = self._cancel_auto_return_flow("cancelled_by_nav_stop")
        cancelled = self.navigation_client.cancel(
            reason=f"lingxi stop request event_id={payload.get('event_id', '')}"
        )
        self.get_logger().info(
            f"/nav/stop result: event_id={event_id or '<none>'}, cancelled={cancelled}, "
            f"auto_return_cancelled={auto_return_cancelled}, previous_auto_return_state={previous_auto_return_state}"
        )
        return {
            "ok": True,
            "cancelled": cancelled or auto_return_cancelled,
            "navigation_cancelled": cancelled,
            "auto_return_cancelled": auto_return_cancelled,
            "previous_auto_return_state": previous_auto_return_state,
            "status": "stopping" if cancelled else "idle",
        }

    def start_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """启动当前地图配置的单个讲解任务。"""

        event_id = str(payload.get("event_id", "")).strip()
        requested_task_id = str(payload.get("task_id", "")).strip()
        self.get_logger().info(
            f"/task/start request: event_id={event_id or '<none>'}, "
            f"task_id={requested_task_id or '<active>'}"
        )
        self._mark_valid_interaction(event_id=event_id, source="task_start", intent="start_task")
        with self._auto_return_lock:
            if self._auto_return_state in {
                AUTO_RETURN_NAVIGATING_TASK_STEP,
                AUTO_RETURN_TASK_ARRIVAL_ACTION,
                AUTO_RETURN_TASK_LINKCRAFT_ACTION,
                AUTO_RETURN_TASK_WAITING,
                AUTO_RETURN_TASK_PAUSED,
            }:
                return {
                    "ok": False,
                    "accepted": False,
                    "status": "busy",
                    "message": "tour task is already running",
                    "task": self._task_status_locked(),
                }

        if self.navigation_client.get_status().get("status") != "idle":
            return {
                "ok": False,
                "accepted": False,
                "status": "busy",
                "message": "navigation is busy",
            }

        try:
            task_id, task_name, steps, loop_count = self._load_tour_task_points(requested_task_id)
        except PointCatalogError as exc:
            return {
                "ok": False,
                "accepted": False,
                "status": "invalid_task",
                "message": str(exc),
            }
        if not steps:
            return {
                "ok": False,
                "accepted": False,
                "status": "no_task_steps",
                "message": "current map has no enabled tour task steps",
            }

        with self._auto_return_lock:
            self._task_id = task_id
            self._task_name = task_name
            self._task_steps = steps
            self._task_index = -1
            self._task_loop_count = loop_count
            self._task_loop_index = 1
            self._task_navigation_task_id = None
            self._task_current_goal_id = ""
            self._task_last_goal_id = ""
            self._task_cancel_requested = False
            self._cancel_task_failure_timer_locked()
            self._task_failure_recovery_pending = False
            self._task_failure_retry_count = 0
            self._task_skipped_steps = []
            self._task_last_failure = None
            self._task_resume_index = -1
            self._task_pause_origin = ""
            self._clear_task_wait_runtime_locked()
            self._auto_return_source_goal_id = str(steps[0].get("point_id", ""))
            self._auto_return_target_id = ""
            self._auto_return_reason = "task_started"

        handle = self._start_task_step(0)
        result = handle_to_dict(handle)
        result.update({
            "ok": bool(handle.accepted),
            "task_id": task_id,
            "task_name": task_name,
            "step_count": len(steps),
            "loop_count": loop_count,
        })
        return result

    def pause_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """暂停讲解并保留当前任务、步骤和讲解内容。"""

        event_id = str(payload.get("event_id", "")).strip()
        self.get_logger().info(f"/task/pause request: event_id={event_id or '<none>'}")
        with self._auto_return_lock:
            if self._auto_return_state == AUTO_RETURN_TASK_LINKCRAFT_ACTION:
                return {
                    "ok": True,
                    "accepted": False,
                    "paused": False,
                    "status": "linkcraft_protected",
                    "message": "灵创动作执行期间不能暂停讲解，请等待动作结束",
                    "task": self._task_status_locked(),
                }
            previous_state = self._auto_return_state
            self._cancel_task_failure_timer_locked()
            self._task_failure_recovery_pending = False
            if previous_state == AUTO_RETURN_TASK_PAUSED:
                if not bool(payload.get("auto_resume_after_silence", False)):
                    self._next_auto_return_generation_locked()
                    self._cancel_arrival_action_timer_locked()
                    self._task_pause_origin = self._task_paused_phase
                    self._task_interruption_wait_started_at = 0.0
                    self._auto_return_reason = "task_manually_paused"
                return {
                    "ok": True,
                    "accepted": False,
                    "paused": True,
                    "status": "already_paused",
                    "task": self._task_status_locked(),
                }
            if previous_state not in {
                AUTO_RETURN_NAVIGATING_TASK_STEP,
                AUTO_RETURN_TASK_ARRIVAL_ACTION,
                AUTO_RETURN_TASK_WAITING,
            }:
                return {
                    "ok": True,
                    "accepted": False,
                    "paused": False,
                    "status": "idle",
                    "message": "no tour task is running",
                    "task": self._task_status_locked(),
                }
            should_cancel_navigation = previous_state == AUTO_RETURN_NAVIGATING_TASK_STEP
            if previous_state == AUTO_RETURN_TASK_WAITING:
                self._task_paused_wait_sec = normalize_task_wait_sec(
                    self._task_steps[self._task_index].get("wait_sec", DEFAULT_TASK_WAIT_SEC)
                )
                self._task_resume_index = self._task_index + 1
            else:
                self._task_resume_index = self._task_index
            self._task_paused_phase = previous_state
            self._task_pause_origin = (
                VOICE_INTERRUPTION_PAUSE_ORIGIN
                if previous_state == AUTO_RETURN_TASK_ARRIVAL_ACTION
                and bool(payload.get("auto_resume_after_silence", False))
                else previous_state
            )
            self._next_auto_return_generation_locked()
            self._cancel_arrival_action_timer_locked()
            self._auto_return_state = AUTO_RETURN_TASK_PAUSED
            self._auto_return_reason = "task_paused"
            if previous_state == AUTO_RETURN_TASK_ARRIVAL_ACTION:
                self._tour_reply_generation += 1
                self._tour_reply_until = 0.0
                self._task_arrival_effects_until = 0.0
            self._mark_valid_interaction(event_id=event_id, source="task_pause", intent="pause_task")

            navigation_cancelled = False
            if should_cancel_navigation:
                navigation_cancelled = self.navigation_client.cancel(
                    reason=f"tour task paused event_id={event_id or '<none>'}"
                )
            if previous_state == AUTO_RETURN_TASK_ARRIVAL_ACTION:
                self._stop_arrival_motion("task_paused")
            self._restore_looping_arrival_emoji()
        return {
            "ok": True,
            "accepted": True,
            "paused": True,
            "navigation_cancelled": navigation_cancelled,
            "previous_state": previous_state,
            "status": "paused",
            "task": self._task_status(),
        }

    def resume_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """从暂停位置继续讲解；导航中暂停会重新前往当前步骤。"""

        event_id = str(payload.get("event_id", "")).strip()
        self.get_logger().info(f"/task/resume request: event_id={event_id or '<none>'}")
        self._mark_valid_interaction(event_id=event_id, source="task_resume", intent="resume_task")
        with self._auto_return_lock:
            if self._auto_return_state != AUTO_RETURN_TASK_PAUSED:
                return {
                    "ok": True,
                    "accepted": False,
                    "resumed": False,
                    "status": "not_paused",
                    "message": "tour task is not paused",
                    "task": self._task_status_locked(),
                }
            if not self._task_steps or self._task_resume_index < 0:
                self._clear_task_locked("invalid_paused_task")
                self._auto_return_state = AUTO_RETURN_IDLE
                return {
                    "ok": False,
                    "accepted": False,
                    "resumed": False,
                    "status": "invalid_task",
                    "message": "paused tour task has no resumable step",
                    "task": self._task_status_locked(),
                }
            resume_index = self._task_resume_index
            pause_origin = self._task_pause_origin
            if self._task_paused_phase == AUTO_RETURN_TASK_ARRIVAL_ACTION:
                self._resume_task_arrival_locked()
                return {
                    "ok": True, "accepted": True, "resumed": True,
                    "status": "arrival_resumed", "task": self._task_status_locked(),
                }

            if pause_origin == AUTO_RETURN_TASK_WAITING:
                return self._resume_task_wait(event_id)

            if self.navigation_client.get_status().get("status") != "idle":
                return {
                    "ok": False,
                    "accepted": False,
                    "resumed": False,
                    "status": "busy",
                    "message": "navigation is still stopping; retry resume shortly",
                    "task": self._task_status_locked(),
                }

            if resume_index >= len(self._task_steps):
                self._clear_task_locked("task_completed_after_pause")
                self._auto_return_state = AUTO_RETURN_IDLE
                self._auto_return_reason = "task_completed"
                return {
                    "ok": True,
                    "accepted": True,
                    "resumed": True,
                    "status": "completed",
                    "task": self._task_status_locked(),
                }

            handle = self._start_task_step(resume_index, resumed=True)
        result = handle_to_dict(handle)
        result.update({
            "ok": bool(handle.accepted),
            "resumed": bool(handle.accepted),
            "task": self._task_status(),
        })
        return result

    def stop_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """结束当前讲解任务；如果正在去步骤点，立刻停止并进入返航等待。"""

        event_id = str(payload.get("event_id", "")).strip()
        self.get_logger().info(f"/task/stop request: event_id={event_id or '<none>'}")
        self._mark_valid_interaction(event_id=event_id, source="task_stop", intent="end_task")
        stopped, previous_state, goal_id, point_name, should_cancel_navigation = self._cancel_task_flow(
            "task_stopped"
        )
        navigation_cancelled = False
        if should_cancel_navigation:
            navigation_cancelled = self.navigation_client.cancel(
                reason=f"tour task stopped event_id={event_id or '<none>'}"
            )
        if previous_state == AUTO_RETURN_TASK_LINKCRAFT_ACTION:
            self._interrupt_linkcraft_action("task_stopped")
        if previous_state == AUTO_RETURN_TASK_ARRIVAL_ACTION:
            self._stop_arrival_motion("task_stopped")
        if stopped:
            with self._auto_return_lock:
                self._begin_waiting_auto_return_for_goal_locked(
                    goal_id,
                    point_name,
                    "task_stopped",
                )
        self._restore_looping_arrival_emoji()
        return {
            "ok": True,
            "accepted": stopped,
            "stopped": stopped,
            "navigation_cancelled": navigation_cancelled,
            "previous_state": previous_state,
            "status": "stopping" if navigation_cancelled else ("stopped" if stopped else "idle"),
        }

    def mark_interaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """记录一次确认过的用户交互，避免把机器人自己的 TTS 当成用户输入。"""

        source = str(payload.get("source", "unknown")).strip() or "unknown"
        intent = str(payload.get("intent", "")).strip()
        event_id = str(payload.get("event_id", "")).strip()
        with self._auto_return_lock:
            task_state = self._auto_return_state
        if should_protect_tour_interaction(task_state, intent):
            self.get_logger().info(
                f"User interaction ignored while tour is protected: event_id={event_id or '<none>'}, "
                f"source={source}, intent={intent or '<none>'}, task_state={task_state}"
            )
            return {
                "ok": True,
                "marked": False,
                "protected": True,
                "state": task_state,
                "source": source,
                "intent": intent,
                "navigation_cancelled": False,
            }
        return self._mark_valid_interaction(event_id=event_id, source=source, intent=intent)

    def wake_interaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """在音频首包到达时按讲解阶段处理真实唤醒。"""

        event_id = str(payload.get("event_id", "")).strip()
        source = str(payload.get("source", "voice_wake")).strip() or "voice_wake"
        with self._auto_return_lock:
            task_state = self._auto_return_state
            task_active = task_state in {
                AUTO_RETURN_NAVIGATING_TASK_STEP,
                AUTO_RETURN_TASK_ARRIVAL_ACTION,
                AUTO_RETURN_TASK_LINKCRAFT_ACTION,
                AUTO_RETURN_TASK_WAITING,
                AUTO_RETURN_TASK_PAUSED,
            }

        if not task_active:
            return {
                "ok": True,
                "accepted": True,
                "handled": False,
                "paused": False,
                "status": "no_active_tour",
                "state": task_state,
            }

        action = wake_interaction_action(task_state)
        if action == WAKE_ACTION_BLOCK:
            self.get_logger().info(
                f"Voice wake ignored during LinkCraft action: event_id={event_id or '<none>'}, "
                f"source={source}, task_state={task_state}"
            )
            return {
                "ok": True,
                "accepted": False,
                "handled": True,
                "paused": False,
                "status": "linkcraft_protected",
                "message": "灵创动作执行期间暂不响应唤醒，请等待动作结束",
                "state": task_state,
                "task": self._task_status(),
            }

        if action == WAKE_ACTION_PAUSE:
            result = self.pause_task(
                {
                    "event_id": event_id,
                    "resume_current_step": True,
                    "auto_resume_after_silence": True,
                }
            )
            result.update(
                {
                    "handled": True,
                    "wake_action": WAKE_ACTION_PAUSE,
                    "state": self._task_status().get("state", AUTO_RETURN_TASK_PAUSED),
                }
            )
            return result

        marked = self._mark_valid_interaction(
            event_id=event_id,
            source=source,
            intent="wake",
        )
        marked.update(
            {
                "accepted": True,
                "handled": True,
                "paused": task_state == AUTO_RETURN_TASK_PAUSED,
                "status": "already_paused" if task_state == AUTO_RETURN_TASK_PAUSED else "dialog_allowed",
                "wake_action": "allow",
            }
        )
        return marked

    def end_interaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """记录一轮 ASR/LLM/TTS 处理结束，并从此刻重新启动返航等待。"""

        source = str(payload.get("source", "unknown")).strip() or "unknown"
        intent = str(payload.get("intent", "")).strip()
        event_id = str(payload.get("event_id", "")).strip()
        with self._auto_return_lock:
            task_state = self._auto_return_state
        if should_protect_tour_interaction(task_state, intent):
            self.get_logger().info(
                f"User interaction end ignored while tour is protected: event_id={event_id or '<none>'}, "
                f"source={source}, intent={intent or '<none>'}, task_state={task_state}"
            )
            return {
                "ok": True,
                "ended": False,
                "protected": True,
                "state": task_state,
                "source": source,
                "intent": intent,
            }
        return self._end_valid_interaction(event_id=event_id, source=source, intent=intent)

    def cancel_auto_return(self, payload: dict[str, Any]) -> dict[str, Any]:
        """显式取消自动返航流程；如果返航已经发车，同时取消导航任务。"""

        event_id = str(payload.get("event_id", "")).strip()
        source = str(payload.get("source", "manual")).strip() or "manual"
        reason = str(payload.get("reason", "")).strip() or f"cancelled_by:{source}"
        cancelled, previous_state, should_cancel_navigation = self._cancel_auto_return_flow(reason)
        navigation_cancelled = False
        if should_cancel_navigation:
            navigation_cancelled = self.navigation_client.cancel(
                reason=f"auto return cancel event_id={event_id or '<none>'} reason={reason}"
            )
        state = AUTO_RETURN_IDLE if cancelled else previous_state
        return {
            "ok": True,
            "cancelled": cancelled or navigation_cancelled,
            "auto_return_cancelled": cancelled,
            "navigation_cancelled": navigation_cancelled,
            "previous_state": previous_state,
            "state": state,
            "reason": reason,
        }

    def speak(self, payload: dict[str, Any]) -> dict[str, Any]:
        """向机器人本地 AimDK TTS 服务提交文本播报请求。"""

        text = str(payload.get("text", "")).strip()
        if not text:
            return {"ok": False, "accepted": False, "status": "empty_text", "message": "text is required"}

        event_id = str(payload.get("event_id", "")).strip()
        with self._auto_return_lock:
            task_state = self._auto_return_state
        if should_block_voice_output(task_state, event_id):
            self.get_logger().info(
                f"Robot reply TTS ignored while tour is protected: event_id={event_id}, "
                f"task_state={task_state}, text={text}"
            )
            return {
                "ok": True,
                "accepted": False,
                "status": "tour_protected",
                "message": "讲解任务执行中，普通语音回复不会打断当前讲解",
            }
        # AimDK 官方 PlayTts demo 将 interrupt 标为必需。实测 interrupt=false 可能返回成功，
        # 但没有真正接管机器人播放通道，因此桥接层统一用 interrupt=true 提交机器人回复。
        requested_interrupt = bool(payload.get("interrupt", True))
        interrupt = True
        force = bool(payload.get("force", False))
        now = time.monotonic()
        with self._tts_lock:
            elapsed = now - self._last_tts_at
            if not force and elapsed < self._tts_min_interval_sec:
                retry_after_sec = max(0.0, self._tts_min_interval_sec - elapsed)
                self.get_logger().info(
                    f"TTS throttled: retry_after={retry_after_sec:.1f}s, text={text}"
                )
                return {
                    "ok": False,
                    "accepted": False,
                    "status": "throttled",
                    "retry_after_sec": retry_after_sec,
                    "message": f"TTS throttled; retry after {retry_after_sec:.1f}s",
                }
            self._last_tts_at = now
        self.get_logger().info(
            f"TTS speak: event_id={event_id or '<none>'}, requested_interrupt={requested_interrupt}, "
            f"interrupt={interrupt}, force={force}, text={text}"
        )
        with self._auto_return_lock:
            # Serialize the final protection check and submission with stage
            # changes; an HTTP request can otherwise race LinkCraft startup.
            if should_block_voice_output(self._auto_return_state, event_id):
                return {"ok": True, "accepted": False, "status": "tour_protected"}
            reply_generation = None
            if self._task_steps:
                self._tour_reply_generation += 1
                reply_generation = self._tour_reply_generation
                if event_id == self._tour_interaction_event_id:
                    self._tour_quiet_since = time.monotonic()
                self._tour_reply_until = time.monotonic() + ARRIVAL_TTS_MAX_WAIT_SEC
                self._refresh_tour_quiet_timer_locked("reply_submitted")
            try:
                future = self.tts_speaker.speak(
                    text,
                    interrupt=interrupt,
                    # Cloud event IDs must not end the local async playback.
                    trace_id=None,
                )
            except Exception:
                if reply_generation is not None:
                    self._on_tour_reply_result(reply_generation, text, None)
                raise
            if reply_generation is not None:
                if future is None:
                    self._on_tour_reply_result(reply_generation, text, None)
                else:
                    future.add_done_callback(
                        lambda result: self._on_tour_reply_result(reply_generation, text, result)
                    )
        if future is None:
            return {
                "ok": False,
                "accepted": False,
                "status": "tts_unavailable",
                "message": "TTS service is not available",
            }
        return {"ok": True, "accepted": True, "status": "submitted"}

    def _on_tour_reply_result(self, generation: int, text: str, future) -> None:
        with self._auto_return_lock:
            if generation != self._tour_reply_generation:
                return
            accepted = False
            if future is not None:
                try:
                    response = future.result()
                    accepted = bool(response.tts_resp.is_success)
                except Exception as exc:
                    self.get_logger().warning(f"Tour reply submission failed: {exc}")
            now = time.monotonic()
            # PlayTts success is an acknowledgement, NOT playback completion.
            duration = self._arrival_action_wait_sec("tts", {"text": text}) + ARRIVAL_COMPLETION_GUARD_SEC
            self._tour_reply_until = now + duration if accepted else 0.0
            self._tour_quiet_since = max(self._tour_quiet_since, now)
            self._refresh_tour_quiet_timer_locked("reply_accepted" if accepted else "reply_failed")

    def _refresh_tour_quiet_timer_locked(self, source: str) -> None:
        if self._auto_return_state == AUTO_RETURN_TASK_WAITING:
            self._restart_task_wait_after_interaction_locked(source)
        elif should_auto_resume_voice_interruption(self._auto_return_state, self._task_pause_origin):
            self._schedule_voice_interruption_resume_locked(source)
        elif self._auto_return_state == AUTO_RETURN_TASK_ARRIVAL_ACTION and self._task_arrival_effects_until:
            self._schedule_next_task_step_after_arrival(
                self._task_arrival_event,
                max(0.0, self._task_arrival_effects_until - time.monotonic()),
            )

    def status(self) -> dict[str, Any]:
        """返回桥接节点、导航客户端、自动返航和 SDK 服务的当前状态。"""

        return {
            "ok": True,
            "active_map_name": self.active_map_name,
            "nav_mode": self.navigation_client.mode,
            "config_path": str(self.config_store.file_path),
            "dense_crowd_profile": self._dense_crowd_profile_status(),
            "navigation": self.navigation_client.get_status(),
            "localization": self.current_localization(),
            "auto_return": self._auto_return_status(),
            "task": self._task_status(),
            "sdk_services": self.motion_controller.services_ready(),
        }

    def current_localization(self) -> dict[str, Any]:
        """返回最近一次定位样本。"""

        pose = self.navigation_client.get_current_pose()
        if pose is None:
            return {
                "ok": False,
                "status": "no_data",
                "message": f"暂未收到 {self.odom_topic} 定位数据",
                "topic": self.odom_topic,
            }
        return {
            "ok": True,
            "status": "ok",
            "topic": self.odom_topic,
            "pose": pose,
        }

    def set_nav_mode(self, payload: dict[str, Any]) -> dict[str, Any]:
        """切换导航模式：execute 实际执行，dry-run 只模拟流程。"""

        mode = str(payload.get("nav_mode", "")).strip()
        if mode not in {"execute", "dry-run"}:
            return {"ok": False, "message": "nav_mode must be execute or dry-run"}
        self.config.app.nav_mode = mode
        self.config_store.save(self.config)
        self.navigation_client.mode = mode
        return {"ok": True, "nav_mode": mode}

    def set_active_map(self, payload: dict[str, Any]) -> dict[str, Any]:
        """切换当前生效地图，并重新加载点位目录。"""

        map_name = str(payload.get("active_map_name", payload.get("map_name", ""))).strip()
        if not map_name:
            return {"ok": False, "message": "active_map_name is required"}
        self._cancel_pending_auto_return("map_change")
        self.active_map_name = map_name
        self._save_active_map_name(map_name)
        return self.reload_catalog()

    def reload_catalog(self) -> dict[str, Any]:
        """重新读取配置和当前地图点位，供 Web 标注端修改后热加载。"""

        self._cancel_pending_auto_return("catalog_reload")
        self.config = self.config_store.load()
        points = self._load_active_points()
        self.point_catalog = PointCatalog(points)
        self.navigation_client.defaults = self.config.navigation
        self.navigation_client.mode = self.config.app.nav_mode
        return {
            "ok": True,
            "active_map_name": self.active_map_name,
            "nav_mode": self.navigation_client.mode,
            "point_count": len(points),
            "message": "Navigation points loaded" if points else "No navigation points configured",
        }

    def handle_navigation_event(self, event: NavigationEvent) -> None:
        """处理导航客户端回调，并驱动到达动作与自动返航状态机。"""

        self.get_logger().info(
            f"Navigation event: type={event.type}, point={event.point_name}, "
            f"task_id={event.task_id}, status={event.status}, "
            f"distance={event.distance_to_goal}, yaw_error_rad={event.yaw_error}, "
            f"reason={event.reason}"
        )
        if event.type == NavigationEventType.COMPLETED:
            # 自动返航完成后只收尾；用户导航完成后先执行到达动作，再等待自动返航。
            if self._is_auto_return_event(event):
                self._finish_auto_return("home_completed")
                return
            if self._is_task_navigation_event(event):
                self._begin_task_arrival_action(event)
                self._schedule_arrival_actions(event, task_arrival=True)
                return
            if self._is_user_navigation_event(event):
                self._begin_arrival_action(event)
                self._schedule_arrival_actions(event, task_arrival=False)
                return
        if event.type == NavigationEventType.CANCELLED:
            # 用户导航被取消时仍进入等待返航，确保机器人最终回到起点。
            if self._is_auto_return_event(event):
                self._finish_auto_return("home_cancelled")
                return
            if self._is_task_navigation_event(event):
                self._finish_cancelled_task_step(event)
                return
            if self._is_user_navigation_event(event):
                self._begin_waiting_auto_return(event, "user_navigation_cancelled")
                return
        if event.type == NavigationEventType.BLOCKED:
            if (
                self._is_auto_return_event(event)
                or self._is_task_navigation_event(event)
                or self._is_user_navigation_event(event)
            ):
                self._speak_navigation_blocked(event)
            return
        if event.type in {NavigationEventType.FAILED, NavigationEventType.TIMEOUT, NavigationEventType.REJECTED}:
            # 失败和超时需要播报问题；拒绝一般表示任务未启动，不额外播报。
            if self._is_auto_return_event(event):
                if event.type in {NavigationEventType.FAILED, NavigationEventType.TIMEOUT}:
                    self._speak_navigation_problem(event, auto_return=True)
                self._finish_auto_return(event.status)
                return
            if self._is_task_navigation_event(event):
                self._handle_task_navigation_failure(event)
                return
            if self._is_user_navigation_event(event):
                if event.type in {NavigationEventType.FAILED, NavigationEventType.TIMEOUT}:
                    self._speak_navigation_problem(event, auto_return=False)
                self._finish_user_navigation_without_return(event.status)
                return

    def _run_on_arrival_actions(self, event: NavigationEvent) -> float:
        """执行点位配置的到达动作，并返回自动返航前需要等待的最长时间。"""

        task_step = self._task_step_for_arrival(event)
        try:
            point = self.point_catalog.get_by_id(event.goal_id)
        except PointCatalogError:
            try:
                point = self.point_catalog.resolve(event.point_name, map_name=self.active_map_name).point
            except PointCatalogError as exc:
                self.get_logger().warning(f"Arrival actions skipped; point not found: {exc}")
                return 0.0

        point_actions = [
            {"type": str(action.type).strip(), **dict(action.payload)}
            for action in point.business.on_arrival
        ]
        actions = merge_task_arrival_actions(task_step, point_actions)
        self.get_logger().info(
            f"Arrival actions starting: point={point.name}, point_id={point.id}, "
            f"action_count={len(actions)}, task_step={bool(task_step)}, "
            f"actions={self._arrival_actions_summary(actions)}"
        )
        wait_sec = 0.0
        for action in actions:
            # 每个动作可单独声明等待时间；未声明时根据动作类型估算。
            action_type = str(action.get("type", "")).strip()
            payload = {key: value for key, value in action.items() if key != "type"}
            action_wait_sec = self._arrival_action_wait_sec(action_type, payload)
            wait_sec = max(wait_sec, action_wait_sec)
            self.get_logger().info(
                f"Arrival action: point={point.name}, type={action_type}, "
                f"auto_return_wait_sec={action_wait_sec:.1f}, details={self._arrival_action_summary(action_type, payload)}"
            )
            if action_type == "motion":
                self._run_arrival_motion(payload)
                continue
            if action_type == "emoji":
                self._run_arrival_emoji(payload)
                continue
            if action_type == "tts":
                self._run_arrival_tts(payload, event)
                continue
            self.get_logger().warning(f"Unsupported arrival action type: {action_type}")
        return wait_sec + ARRIVAL_COMPLETION_GUARD_SEC if actions else 0.0

    def _schedule_arrival_actions(self, event: NavigationEvent, *, task_arrival: bool) -> None:
        """等待导航控制权释放后再执行动作，避免 AimDK 仍处于忙状态。"""

        expected_state = AUTO_RETURN_TASK_ARRIVAL_ACTION if task_arrival else AUTO_RETURN_ARRIVAL_ACTION
        with self._auto_return_lock:
            current_goal_id = self._task_current_goal_id if task_arrival else self._auto_return_source_goal_id
            if self._auto_return_state != expected_state or current_goal_id != event.goal_id:
                return
            generation = self._next_auto_return_generation_locked()
            self._cancel_arrival_action_timer_locked()
            self._arrival_action_timer = self.create_timer(
                ARRIVAL_SETTLE_SEC,
                lambda: self._on_arrival_settle_done(generation, event, task_arrival),
            )
        self.get_logger().info(
            f"Arrival settle wait started: point={event.point_name}, "
            f"task_arrival={task_arrival}, wait_sec={ARRIVAL_SETTLE_SEC:.1f}"
        )

    def _on_arrival_settle_done(
        self,
        generation: int,
        event: NavigationEvent,
        task_arrival: bool,
    ) -> None:
        """底盘稳定等待结束后执行到点业务，单项失败不会终止任务。"""

        expected_state = AUTO_RETURN_TASK_ARRIVAL_ACTION if task_arrival else AUTO_RETURN_ARRIVAL_ACTION
        with self._auto_return_lock:
            if generation != self._auto_return_generation:
                return
            self._cancel_arrival_action_timer_locked()
            current_goal_id = self._task_current_goal_id if task_arrival else self._auto_return_source_goal_id
            if self._auto_return_state != expected_state or current_goal_id != event.goal_id:
                return
            try:
                wait_sec = self._run_on_arrival_actions(event)
            except Exception as exc:
                self.get_logger().error(
                    f"Arrival actions failed but task will continue: point={event.point_name}, error={exc}"
                )
                wait_sec = ARRIVAL_COMPLETION_GUARD_SEC
            if task_arrival:
                self._schedule_next_task_step_after_arrival(event, wait_sec)
            else:
                self._schedule_auto_return_after_arrival(event, wait_sec)

    def _task_step_for_arrival(self, event: NavigationEvent) -> dict[str, Any] | None:
        """返回与当前到达事件匹配的讲解任务步骤。"""

        with self._auto_return_lock:
            if (
                self._auto_return_state != AUTO_RETURN_TASK_ARRIVAL_ACTION
                or self._task_current_goal_id != event.goal_id
                or self._task_index < 0
                or self._task_index >= len(self._task_steps)
            ):
                return None
            step = self._task_steps[self._task_index]
            if str(step.get("point_id", "")).strip() != event.goal_id:
                return None
            return dict(step)

    def _run_arrival_motion(self, payload: dict[str, Any]) -> None:
        """执行到达点位后的预设动作。"""

        motion = str(payload.get("motion", "")).strip()
        ani_path = str(payload.get("ani_path", "") or "").strip()
        if not motion:
            return
        try:
            motion_id = self.motion_controller.resolve_motion_id(motion)
        except ValueError:
            return
        if is_empty_motion(motion_id, ani_path):
            self.get_logger().warning(
                "Arrival empty motion ignored: 0/0 is reserved for explicit interruption "
                "and must not be used as a normal arrival action"
            )
            return
        future = self.motion_controller.play_preset_motion(
            motion,
            payload.get("area", "right_hand"),
            interrupt=bool(payload.get("interrupt", False)),
            force=bool(payload.get("force", True)),
            ani_path=ani_path,
            play_timestamp=max(0, int(payload.get("play_timestamp", 0) or 0)),
        )
        if future is None:
            return
        with self._auto_return_lock:
            self._arrival_motion.mark_started(motion_id, ani_path)

    def _finish_arrival_motion_after_wait(self, reason: str) -> dict[str, Any]:
        """Finish finite motions naturally and explicitly stop known continuous motions."""

        with self._auto_return_lock:
            completion_action = self._arrival_motion.completion_action_after_wait()
        if completion_action == "interrupt":
            self.get_logger().info(
                f"Continuous arrival motion requires explicit stop: reason={reason}"
            )
            result = self._stop_arrival_motion(reason)
            result["completion"] = "interrupted"
            result["release_guard_sec"] = (
                ARRIVAL_INTERRUPT_RELEASE_GUARD_SEC if result.get("accepted") else 0.0
            )
            return result
        if completion_action == "natural":
            self.get_logger().info(
                f"Arrival motion wait completed naturally: reason={reason}; "
                "no empty action submitted"
            )
        return {
            "ok": True,
            "accepted": completion_action == "natural",
            "status": "naturally_completed" if completion_action == "natural" else "idle",
            "completion": completion_action,
            "release_guard_sec": 0.0,
            "reason": reason,
        }

    def stop_arrival_motion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Stop a lingering action through the explicit internal empty action."""

        reason = str(payload.get("reason", "manual_request")).strip() or "manual_request"
        return self._stop_arrival_motion(reason, force=bool(payload.get("force", False)))

    def play_voice_preset_motion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute one LLM-selected ordinary motion after strict local validation."""

        requested_motion = payload.get("motion")
        requested_area = payload.get("area")
        selection = resolve_voice_preset_motion(requested_motion, requested_area)
        if selection is None:
            return {
                "ok": False,
                "accepted": False,
                "status": "unsupported_motion",
                "message": "这个普通动作或控制区域暂不支持",
            }

        navigation = self.navigation_client.get_status()
        task = self._task_status()
        block_reason = voice_preset_motion_block_reason(
            navigation.get("status", "idle"),
            task.get("active", False),
        )
        if block_reason:
            return {
                "ok": True,
                "accepted": False,
                "status": block_reason,
                "message": "机器人正在导航或执行讲解任务，请停稳后再试这个动作",
            }

        motion, area = selection
        result = self.motion_controller.play_preset_motion_and_wait(
            motion,
            area,
            interrupt=False,
            force=False,
        )
        if not bool(result.get("accepted", False)):
            return {
                "ok": False,
                "accepted": False,
                "status": str(result.get("status", "rejected")),
                "message": str(result.get("message", "机器人动作请求未被接受")),
            }
        self.get_logger().info(
            f"Voice preset motion accepted: event_id={str(payload.get('event_id', '')).strip() or '<none>'}, "
            f"motion={motion}, area={area}"
        )
        return {
            "ok": True,
            "accepted": True,
            "status": "accepted",
            "motion": motion,
            "area": area,
        }

    def _stop_arrival_motion(self, reason: str, *, force: bool = False) -> dict[str, Any]:
        with self._auto_return_lock:
            should_stop = self._arrival_motion.claim_stop(force=force)
        if not should_stop:
            return {"ok": True, "accepted": False, "status": "idle"}
        self.get_logger().info(f"Stopping arrival motion with empty action: reason={reason}, force={force}")
        future = self.motion_controller.play_preset_motion(
            "empty_motion",
            "0",
            interrupt=True,
            force=True,
        )
        if future is None:
            return {
                "ok": False,
                "accepted": False,
                "status": "motion_service_unavailable",
                "message": "停止动作命令未能提交",
            }
        return {"ok": True, "accepted": True, "status": "submitted", "reason": reason}

    def _run_arrival_emoji(self, payload: dict[str, Any]) -> None:
        """执行到达点位后的表情展示。"""

        emotion = str(payload.get("emotion", "")).strip()
        if not emotion:
            return
        priority = payload.get("priority", DEFAULT_EMOJI_PRIORITY)
        mode = payload.get("mode", "once")
        future = self.motion_controller.play_emoji(
            emotion,
            mode=mode,
            priority=int(priority if priority is not None else DEFAULT_EMOJI_PRIORITY),
        )
        if future is not None and str(mode).strip().lower() in {"loop", "2"}:
            with self._auto_return_lock:
                self._looping_arrival_emoji_active = True

    def _restore_looping_arrival_emoji(self) -> None:
        """循环讲解表情结束时恢复默认面部状态。"""

        with self._auto_return_lock:
            if not self._looping_arrival_emoji_active:
                return
            self._looping_arrival_emoji_active = False
        self.motion_controller.play_emoji(
            "calm",
            mode="once",
            priority=DEFAULT_EMOJI_PRIORITY,
        )

    def _run_arrival_tts(self, payload: dict[str, Any], event: NavigationEvent) -> None:
        """执行到达点位后的语音播报。"""

        text = str(payload.get("text", "")).strip()
        if not text:
            return
        self.speak(
            {
                "event_id": f"arrival-{event.task_id or event.goal_id}",
                "text": text,
                "interrupt": True,
                "force": True,
            }
        )

    def _speak_navigation_problem(self, event: NavigationEvent, *, auto_return: bool) -> None:
        """导航失败或超时时，通过 TTS 提醒现场人员处理。"""

        if auto_return:
            text = "自动回起点失败，请检查通道、定位或底盘状态。"
        elif event.type == NavigationEventType.TIMEOUT:
            text = "当前路线暂时无法到达，我先停止导航，请帮我清理通道或重新指定目标。"
        else:
            text = "导航失败，我先停止导航，请检查通道、定位或底盘状态。"
        self.get_logger().warning(
            f"Navigation problem TTS: type={event.type}, point={event.point_name}, "
            f"task_id={event.task_id}, auto_return={auto_return}, reason={event.reason}, text={text}"
        )
        self.speak(
            {
                "event_id": f"nav-problem-{event.task_id or event.goal_id}",
                "text": text,
                "interrupt": True,
                "force": True,
            }
        )

    def _speak_navigation_blocked(self, event: NavigationEvent) -> None:
        """在导航实际停滞时礼貌提醒前方人员让出通道。"""

        text = "请大家让让我"
        self.get_logger().warning(
            f"Navigation blocked reminder: point={event.point_name}, "
            f"task_id={event.task_id}, reason={event.reason}, text={text}"
        )
        self.speak(
            {
                "event_id": f"nav-blocked-{event.task_id}-{int(event.created_at * 1000)}",
                "text": text,
                "interrupt": True,
                "force": False,
            }
        )

    def _resolve_active_map_name(self, active_map_name: str | None) -> str | None:
        """确定启动时使用的 active map。"""

        # 优先使用命令行显式传入的地图；未传入时，如果配置里只有一张地图就直接使用；
        # 否则退回到第一张有点位数据的地图。
        if active_map_name:
            return active_map_name
        saved_map_name = self._load_saved_active_map_name()
        if saved_map_name:
            return saved_map_name
        if len(self.config.maps) == 1:
            return self.config.maps[0].map_name
        all_points = self.point_store.list_all_points(defaults=self.config.navigation)
        if all_points:
            return all_points[0].map_name
        return None

    def _load_saved_active_map_name(self) -> str | None:
        try:
            map_name = self.active_map_state_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            self.get_logger().warning(f"Failed to read active map state: {exc}")
            return None
        if not map_name:
            return None
        if self.point_store.path_for_map(map_name).exists():
            return map_name
        self.get_logger().warning(f"Saved active map no longer exists: {map_name}")
        return None

    def _save_active_map_name(self, map_name: str) -> None:
        try:
            self.active_map_state_path.write_text(f"{map_name}\n", encoding="utf-8")
        except OSError as exc:
            self.get_logger().warning(f"Failed to save active map state: {exc}")

    def _load_active_points(self):
        """加载当前地图点位；未指定地图时加载所有点位。"""

        if self.active_map_name:
            return self.point_store.load_points(
                self.active_map_name,
                defaults=self.config.navigation,
            )
        return self.point_store.list_all_points(defaults=self.config.navigation)

    def _mark_valid_interaction(self, *, event_id: str, source: str, intent: str) -> dict[str, Any]:
        """记录真实用户交互；暂停待发车返航，但不永久取消返航流程。"""

        should_cancel_navigation = False
        with self._auto_return_lock:
            if should_protect_tour_interaction(self._auto_return_state, intent):
                return {"ok": True, "marked": False, "protected": True,
                        "state": self._auto_return_state, "source": source, "intent": intent,
                        "navigation_cancelled": False}
            self._last_valid_interaction_at = time.monotonic()
            previous_state = self._auto_return_state
            self._tour_interaction_event_id = event_id
            self._tour_interaction_ended = False
            self._tour_quiet_since = self._last_valid_interaction_at + TOUR_INTERACTION_FALLBACK_SEC
            if should_auto_resume_voice_interruption(previous_state, self._task_pause_origin):
                self._schedule_voice_interruption_resume_locked(source)
            if previous_state in {AUTO_RETURN_ARRIVAL_ACTION, AUTO_RETURN_WAITING}:
                self._cancel_arrival_action_timer_locked()
                self._cancel_auto_return_timer_locked()
                self._auto_return_state = AUTO_RETURN_INTERACTION_ACTIVE
                self._auto_return_reason = f"interaction_active:{source}"
            elif previous_state == AUTO_RETURN_INTERACTION_ACTIVE:
                self._auto_return_reason = f"interaction_active:{source}"
            elif previous_state == AUTO_RETURN_TASK_WAITING:
                self._restart_task_wait_after_interaction_locked(source)
                self._task_wait_interaction_active = True
                self._auto_return_reason = f"task_wait_interaction_active:{source}"
            elif previous_state == AUTO_RETURN_RETURNING_HOME:
                self._cancel_auto_return_timer_locked()
                self._auto_return_state = AUTO_RETURN_INTERACTION_ACTIVE
                self._auto_return_reason = f"interaction_active:{source}"
                should_cancel_navigation = True
            state = self._auto_return_state

        navigation_cancelled = False
        if should_cancel_navigation:
            navigation_cancelled = self.navigation_client.cancel(
                reason=f"auto return preempted by interaction event_id={event_id or '<none>'}"
            )
        if previous_state in {AUTO_RETURN_ARRIVAL_ACTION, AUTO_RETURN_TASK_ARRIVAL_ACTION}:
            self._restore_looping_arrival_emoji()
        self.get_logger().info(
            f"Valid interaction marked: event_id={event_id or '<none>'}, source={source}, "
            f"intent={intent or '<none>'}, previous_auto_return_state={previous_state}, state={state}, "
            f"navigation_cancelled={navigation_cancelled}"
        )
        return {
            "ok": True,
            "marked": True,
            "state": state,
            "previous_state": previous_state,
            "source": source,
            "intent": intent,
            "navigation_cancelled": navigation_cancelled,
        }

    def _end_valid_interaction(self, *, event_id: str, source: str, intent: str) -> dict[str, Any]:
        """结束真实用户交互；如果此前暂停了返航，从此刻重新等待 idle_sec。"""

        with self._auto_return_lock:
            if should_protect_tour_interaction(self._auto_return_state, intent):
                return {"ok": True, "ended": False, "protected": True,
                        "state": self._auto_return_state, "source": source, "intent": intent}
            if self._task_steps and (
                event_id != self._tour_interaction_event_id or self._tour_interaction_ended
            ):
                return {"ok": True, "ended": False, "status": "stale_interaction",
                        "state": self._auto_return_state, "source": source, "intent": intent}
            self._tour_interaction_ended = True
            self._last_valid_interaction_at = time.monotonic()
            self._tour_quiet_since = self._last_valid_interaction_at
            previous_state = self._auto_return_state
            source_goal_id = self._auto_return_source_goal_id
            if previous_state == AUTO_RETURN_TASK_WAITING:
                wait_sec = self._restart_task_wait_after_interaction_locked(source)
            else:
                wait_sec = 0.0
            if should_auto_resume_voice_interruption(previous_state, self._task_pause_origin):
                self._schedule_voice_interruption_resume_locked(source)
            if previous_state in {AUTO_RETURN_INTERACTION_ACTIVE, AUTO_RETURN_WAITING, AUTO_RETURN_ARRIVAL_ACTION}:
                self._begin_waiting_auto_return_for_goal_locked(
                    source_goal_id,
                    self._point_name_for_id(source_goal_id),
                    f"interaction_end:{source}",
                )
            state = self._auto_return_state

        self.get_logger().info(
            f"Valid interaction ended: event_id={event_id or '<none>'}, source={source}, "
            f"intent={intent or '<none>'}, previous_auto_return_state={previous_state}, state={state}, "
            f"task_idle_wait_sec={wait_sec:.1f}"
        )
        return {
            "ok": True,
            "ended": True,
            "state": state,
            "previous_state": previous_state,
            "source": source,
            "intent": intent,
        }

    def _schedule_voice_interruption_resume_locked(self, source: str) -> None:
        if not should_auto_resume_voice_interruption(self._auto_return_state, self._task_pause_origin):
            return
        if self._task_resume_index < 0 or self._task_resume_index >= len(self._task_steps):
            return
        generation = self._next_auto_return_generation_locked()
        self._cancel_arrival_action_timer_locked()
        # A missing end hook has a bounded inactivity lease. Local TTS reserves
        # its estimated playback window independently of the cloud end hook.
        self._task_interruption_wait_started_at = max(self._tour_quiet_since, self._tour_reply_until)
        self._arrival_action_timer = self.create_timer(
            max(0.05, self._task_interruption_wait_started_at + VOICE_INTERRUPTION_RESUME_SEC - time.monotonic()),
            lambda: self._on_voice_interruption_resume(generation),
        )
        self._auto_return_reason = f"voice_interruption_waiting:{source}"
        self.get_logger().info(
            f"Voice interruption quiet wait started: resume_sec={VOICE_INTERRUPTION_RESUME_SEC:.1f}, "
            f"source={source}"
        )

    def _on_voice_interruption_resume(self, generation: int) -> None:
        with self._auto_return_lock:
            if (
                generation != self._auto_return_generation
                or not should_auto_resume_voice_interruption(self._auto_return_state, self._task_pause_origin)
                or self._task_resume_index < 0
                or self._task_resume_index >= len(self._task_steps)
            ):
                return
            if time.monotonic() < max(self._tour_quiet_since, self._tour_reply_until) + VOICE_INTERRUPTION_RESUME_SEC:
                self._schedule_voice_interruption_resume_locked("still_active")
                return
            self._resume_task_arrival_locked()

    def _resume_task_arrival_locked(self) -> None:
        """Resume effects at the arrived point without issuing another nav goal."""

        self._next_auto_return_generation_locked()
        self._cancel_arrival_action_timer_locked()
        self._task_interruption_wait_started_at = 0.0
        self._task_pause_origin = ""
        self._task_paused_phase = ""
        self._task_resume_index = -1
        self._tour_quiet_since = time.monotonic()
        self._tour_interaction_ended = True
        self._auto_return_state = AUTO_RETURN_TASK_ARRIVAL_ACTION
        self._auto_return_reason = "task_arrival_resumed"
        event = self._task_arrival_event
        if event is not None:
            # Already at the point: resume the explanation immediately after
            # the quiet interval, without another navigation settle interval.
            self._on_arrival_settle_done(self._auto_return_generation, event, True)

    def _begin_user_navigation(self, goal_id: str) -> None:
        """进入用户目标导航状态，并清理之前挂起的返航定时器。"""

        self._restore_looping_arrival_emoji()
        with self._auto_return_lock:
            self._cancel_arrival_action_timer_locked()
            self._cancel_task_failure_timer_locked()
            self._cancel_auto_return_timer_locked()
            self._auto_return_state = AUTO_RETURN_NAVIGATING_USER_TARGET
            self._auto_return_source_goal_id = goal_id
            self._auto_return_target_id = ""
            self._auto_return_reason = "user_navigation_started"
        self.get_logger().info(f"Auto return state: {AUTO_RETURN_NAVIGATING_USER_TARGET}, source_goal_id={goal_id}")

    def _finish_rejected_user_navigation(self, goal_id: str) -> None:
        """用户导航任务被拒绝时，回退自动返航状态机。"""

        with self._auto_return_lock:
            if (
                self._auto_return_state == AUTO_RETURN_NAVIGATING_USER_TARGET
                and self._auto_return_source_goal_id == goal_id
            ):
                self._auto_return_state = AUTO_RETURN_IDLE
                self._auto_return_reason = "user_navigation_rejected"

    def _finish_user_navigation_without_return(self, reason: str) -> None:
        """用户导航失败或超时时清理状态，不触发自动返航。"""

        with self._auto_return_lock:
            if self._auto_return_state != AUTO_RETURN_NAVIGATING_USER_TARGET:
                return
            self._cancel_arrival_action_timer_locked()
            self._cancel_auto_return_timer_locked()
            self._auto_return_state = AUTO_RETURN_IDLE
            self._auto_return_reason = reason
            self._auto_return_source_goal_id = ""
            self._auto_return_target_id = ""
        self.get_logger().info(f"User navigation finished without auto return: reason={reason}")

    def _load_tour_task_points(
        self,
        requested_task_id: str = "",
    ) -> tuple[str, str, list[dict[str, Any]], int]:
        """读取当前地图的讲解任务，并解析成有序步骤。"""

        if not self.active_map_name:
            raise PointCatalogError("active map is required for tour task")
        tasks_payload = self.point_store.load_tour_tasks(self.active_map_name)
        task = tasks_payload.get("active_task", {})
        if requested_task_id:
            task = next(
                (
                    item for item in tasks_payload.get("tasks", [])
                    if isinstance(item, dict) and str(item.get("id", "")) == requested_task_id
                ),
                None,
            )
            if task is None:
                raise PointCatalogError(f"讲解任务不存在：{requested_task_id}")
        if not task.get("enabled", True):
            return str(task.get("id", "")), str(task.get("name", "默认讲解任务")), []
        steps = []
        for step in task.get("steps", []):
            if not isinstance(step, dict) or not step.get("enabled", True):
                continue
            point_id = str(step.get("point_id", "")).strip()
            if not point_id:
                continue
            point = self.point_catalog.get_by_id(point_id)
            steps.append({
                "point_id": point.id,
                "point_name": point.name,
                "speech_text": str(step.get("speech_text", step.get("text", ""))).strip(),
                "wait_sec": normalize_task_wait_sec(step.get("wait_sec", DEFAULT_TASK_WAIT_SEC)),
                **({"arrival_motion": step.get("arrival_motion")} if "arrival_motion" in step else {}),
                **({"arrival_emoji": step.get("arrival_emoji")} if "arrival_emoji" in step else {}),
                **({"linkcraft_action": step.get("linkcraft_action")} if "linkcraft_action" in step else {}),
            })
        task_name = str(task.get("name", "默认讲解任务")).strip() or "默认讲解任务"
        task_id = str(task.get("id", requested_task_id or "default")).strip() or "default"
        loop_count = normalize_task_loop_count(task.get("loop_count", 1))
        self.get_logger().info(
            f"Tour task loaded: task_id={task_id}, task={task_name}, step_count={len(steps)}, "
            f"loop_count={loop_count}, "
            f"steps={json.dumps(steps, ensure_ascii=False, default=str)}"
        )
        return task_id, task_name, steps, loop_count

    def _start_task_step(
        self,
        index: int,
        *,
        resumed: bool = False,
        retrying: bool = False,
    ) -> NavigationSessionHandle:
        """启动讲解任务中的一个步骤导航。"""

        self._finish_arrival_motion_after_wait("before_next_task_step")
        self._restore_looping_arrival_emoji()
        reuse_current_arrival = False
        with self._auto_return_lock:
            if index < 0 or index >= len(self._task_steps):
                return NavigationSessionHandle(
                    task_id=None,
                    goal_id="",
                    point_name="",
                    accepted=False,
                    status="invalid_task_step",
                    reason="task step index out of range",
                )
            step = self._task_steps[index]
            point_id = str(step.get("point_id", "")).strip()
            point = self.point_catalog.get_by_id(point_id)
            reuse_current_arrival = (
                not resumed
                and index > 0
                and self._task_last_goal_id == point.id
            )
            self._cancel_arrival_action_timer_locked()
            self._next_auto_return_generation_locked()
            self._task_index = index
            self._task_navigation_task_id = None
            self._task_arrival_event = None
            self._task_arrival_effects_until = 0.0
            self._tour_reply_generation += 1
            self._tour_reply_until = 0.0
            self._task_linkcraft_completed = False
            self._task_paused_phase = ""
            self._task_pause_origin = ""
            self._task_resume_index = -1
            self._task_current_goal_id = point.id
            # A submitted or skipped goal is not a confirmed arrival. Movement
            # invalidates the previous position until COMPLETED is received.
            self._task_last_goal_id = point.id if reuse_current_arrival else ""
            self._clear_task_wait_runtime_locked()
            self._task_failure_recovery_pending = False
            if not retrying:
                self._task_failure_retry_count = 0
            self._auto_return_state = AUTO_RETURN_NAVIGATING_TASK_STEP
            self._auto_return_source_goal_id = point.id
            self._auto_return_target_id = ""
            self._auto_return_reason = f"task_step:{index + 1}/{len(self._task_steps)}"

        self.get_logger().info(
            f"Tour task step starting: task={self._task_name or '<unnamed>'}, "
            f"step={index + 1}/{len(self._task_steps)}, point={point.name}, point_id={point.id}"
        )
        if reuse_current_arrival:
            self.get_logger().info(
                f"讲解任务连续步骤复用当前点位，无需重复导航: "
                f"step={index + 1}/{len(self._task_steps)}, point={point.name}, point_id={point.id}"
            )
            self.handle_navigation_event(NavigationEvent(
                type=NavigationEventType.COMPLETED,
                task_id=None,
                goal_id=point.id,
                point_name=point.name,
                status="completed",
                reason="consecutive_same_point",
            ))
            return NavigationSessionHandle(
                task_id=None,
                goal_id=point.id,
                point_name=point.name,
                accepted=True,
                status="completed",
                reason="consecutive_same_point",
            )
        handle = self.navigation_client.start(
            point,
            listener=self.handle_navigation_event,
            allow_initial_arrival_hysteresis=resumed,
            allow_timeout_arrival_recovery=True,
        )
        if not handle.accepted:
            self.get_logger().warning(
                f"Tour task step rejected: point={point.name}, status={handle.status}, reason={handle.reason}"
            )
            with self._auto_return_lock:
                failure_recovery_pending = self._task_failure_recovery_pending
            if not failure_recovery_pending:
                self._finish_task_without_return(handle.status)
        else:
            with self._auto_return_lock:
                if (
                    self._auto_return_state == AUTO_RETURN_NAVIGATING_TASK_STEP
                    and self._task_current_goal_id == point.id
                ):
                    self._task_navigation_task_id = handle.task_id
        return handle

    def _begin_task_arrival_action(self, event: NavigationEvent) -> None:
        """讲解任务步骤到达后进入步骤动作阶段。"""

        with self._auto_return_lock:
            if not self._is_task_navigation_event_locked(event):
                return
            self._auto_return_state = AUTO_RETURN_TASK_ARRIVAL_ACTION
            self._task_arrival_event = event
            self._task_current_goal_id = event.goal_id
            self._task_last_goal_id = event.goal_id
            self._auto_return_source_goal_id = event.goal_id
            self._auto_return_reason = "task_arrival_action"
        self.get_logger().info(
            f"Tour task arrival action: task={self._task_name or '<unnamed>'}, "
            f"point={event.point_name}, goal_id={event.goal_id}"
        )

    def _schedule_next_task_step_after_arrival(self, event: NavigationEvent, wait_sec: float) -> None:
        """Wait for normal arrival effects before the optional LinkCraft stage."""

        wait_sec = max(0.0, float(wait_sec))
        begin_linkcraft = False
        with self._auto_return_lock:
            if (
                self._auto_return_state != AUTO_RETURN_TASK_ARRIVAL_ACTION
                or self._task_current_goal_id != event.goal_id
            ):
                return
            self._task_arrival_effects_until = time.monotonic() + wait_sec
            wait_sec = max(wait_sec, self._tour_reply_until - time.monotonic())
            if wait_sec <= 0.0:
                begin_linkcraft = True
            else:
                generation = self._next_auto_return_generation_locked()
                self._cancel_arrival_action_timer_locked()
                self._arrival_action_timer = self.create_timer(
                    wait_sec,
                    lambda: self._on_task_arrival_wait_done(generation, event),
                )
                self.get_logger().info(
                    f"Tour task arrival wait started: point={event.point_name}, wait_sec={wait_sec:.1f}"
                )
                return
        self._restore_looping_arrival_emoji()
        if begin_linkcraft:
            self._finish_task_arrival_and_continue(event)

    def _on_task_arrival_wait_done(self, generation: int, event: NavigationEvent) -> None:
        """讲解任务步骤动作等待结束。"""

        should_continue = False
        with self._auto_return_lock:
            if generation != self._auto_return_generation:
                return
            self._cancel_arrival_action_timer_locked()
            if (
                self._auto_return_state != AUTO_RETURN_TASK_ARRIVAL_ACTION
                or self._task_current_goal_id != event.goal_id
            ):
                return
            if self._tour_reply_until > time.monotonic():
                self._schedule_next_task_step_after_arrival(event, 0.0)
                return
            self._task_arrival_effects_until = 0.0
            should_continue = True
            if should_continue:
                self._finish_task_arrival_and_continue(event)

    def _finish_task_arrival_and_continue(self, event: NavigationEvent) -> None:
        """Release the arrival motion safely before starting LinkCraft or the next point."""

        result = self._finish_arrival_motion_after_wait("task_arrival_done")
        self._restore_looping_arrival_emoji()
        if not result.get("ok", False):
            self.get_logger().error(
                "Tour task stopped because the continuous arrival motion could not be interrupted"
            )
            self._finish_task_without_return("arrival_motion_stop_failed")
            return

        release_guard_sec = max(0.0, float(result.get("release_guard_sec", 0.0) or 0.0))
        if release_guard_sec <= 0.0:
            self._begin_linkcraft_or_advance(event)
            return

        with self._auto_return_lock:
            if (
                self._auto_return_state != AUTO_RETURN_TASK_ARRIVAL_ACTION
                or self._task_current_goal_id != event.goal_id
            ):
                return
            generation = self._next_auto_return_generation_locked()
            self._cancel_arrival_action_timer_locked()
            self._arrival_action_timer = self.create_timer(
                release_guard_sec,
                lambda: self._on_task_motion_release_done(generation, event),
            )
        self.get_logger().info(
            f"Tour task motion release wait started: point={event.point_name}, "
            f"wait_sec={release_guard_sec:.1f}"
        )

    def _on_task_motion_release_done(self, generation: int, event: NavigationEvent) -> None:
        with self._auto_return_lock:
            if generation != self._auto_return_generation:
                return
            self._cancel_arrival_action_timer_locked()
            if (
                self._auto_return_state != AUTO_RETURN_TASK_ARRIVAL_ACTION
                or self._task_current_goal_id != event.goal_id
            ):
                return
            self._begin_linkcraft_or_advance(event)

    def _begin_linkcraft_or_advance(self, event: NavigationEvent) -> None:
        """Start the selected LinkCraft resource, then enter the configured wait."""

        action = None
        generation = -1
        with self._auto_return_lock:
            if (
                self._auto_return_state != AUTO_RETURN_TASK_ARRIVAL_ACTION
                or self._task_current_goal_id != event.goal_id
                or self._task_index < 0
                or self._task_index >= len(self._task_steps)
            ):
                return
            action = normalize_linkcraft_action(
                self._task_steps[self._task_index].get("linkcraft_action")
            ) if not self._task_linkcraft_completed else None
            if action is not None:
                generation = self._next_auto_return_generation_locked()
                self._cancel_arrival_action_timer_locked()
                self._auto_return_state = AUTO_RETURN_TASK_LINKCRAFT_ACTION
                self._auto_return_reason = "task_linkcraft_action"
                self._linkcraft_action = dict(action)
                self._linkcraft_wait_started_at = 0.0
                self._linkcraft_wait_sec = 0.0

        if action is None:
            self._begin_task_wait(event, "no_linkcraft_action")
            return
        self.get_logger().info(
            f"讲解任务进入灵创阶段: point={event.point_name}, "
            f"resource={action['name']}, duration_sec={action['duration_sec']:.1f}"
        )
        self._submit_linkcraft_action(generation, event, action, retries_remaining=1)

    def _submit_linkcraft_action(
        self,
        generation: int,
        event: NavigationEvent,
        action: dict[str, Any],
        *,
        retries_remaining: int,
    ) -> None:
        with self._auto_return_lock:
            if not self._is_current_linkcraft_action_locked(generation, event, action):
                return
            generation = self._next_auto_return_generation_locked()
            self._cancel_arrival_action_timer_locked()
            self._arrival_action_timer = self.create_timer(
                LINKCRAFT_SUBMIT_TIMEOUT_SEC,
                lambda: self._on_linkcraft_submit_timeout(generation, event, action),
            )
            try:
                self.motion_controller.execute_action_resource(
                    action["resource_key"],
                    action["resource_version"],
                    action["resource_type"],
                    trace_id=f"tour-{event.task_id or event.goal_id}",
                    on_result=lambda accepted, message: self._on_linkcraft_submit_result(
                        generation,
                        event,
                        action,
                        retries_remaining,
                        accepted,
                        message,
                    ),
                )
            except Exception as exc:
                self._on_linkcraft_submit_result(
                    generation,
                    event,
                    action,
                    0,
                    False,
                    str(exc),
                )

    def _on_linkcraft_submit_result(
        self,
        generation: int,
        event: NavigationEvent,
        action: dict[str, Any],
        retries_remaining: int,
        accepted: bool,
        message: str,
    ) -> None:
        retry = False
        wait_sec = 0.0
        begin_wait = False
        with self._auto_return_lock:
            if not self._is_current_linkcraft_action_locked(generation, event, action):
                return
            generation = self._next_auto_return_generation_locked()
            self._cancel_arrival_action_timer_locked()
            message_lower = str(message or "").lower()
            retry = (
                not accepted
                and retries_remaining > 0
                and any(token in message_lower for token in ("playing", "busy", "播放中"))
            )
            if retry:
                self._arrival_action_timer = self.create_timer(
                    LINKCRAFT_BUSY_RETRY_DELAY_SEC,
                    lambda: self._retry_linkcraft_action(
                        generation,
                        event,
                        action,
                        retries_remaining - 1,
                    ),
                )
            elif accepted:
                wait_sec = min(
                    MAX_ACTION_DURATION_SEC,
                    max(0.1, float(action["duration_sec"])) + LINKCRAFT_COMPLETION_GUARD_SEC,
                )
                self._linkcraft_wait_started_at = time.monotonic()
                self._linkcraft_wait_sec = wait_sec
                self._arrival_action_timer = self.create_timer(
                    wait_sec,
                    lambda: self._on_linkcraft_wait_done(generation, event, action),
                )
            else:
                self._finish_linkcraft_stage_locked(
                    event,
                    f"resource_rejected:{message or 'unknown'}",
                )
                begin_wait = True

        if retry:
            self.get_logger().warning(
                f"灵创资源正在占用，{LINKCRAFT_BUSY_RETRY_DELAY_SEC:.1f} 秒后重试一次: "
                f"resource={action['name']}, message={message or '<none>'}"
            )
            return
        if accepted:
            self.get_logger().info(
                f"灵创资源执行等待已开始: resource={action['name']}, wait_sec={wait_sec:.1f}"
            )
            return
        self.get_logger().warning(
            f"灵创资源未执行，讲解任务继续: resource={action['name']}, message={message or '<none>'}"
        )
        if begin_wait:
            self._begin_task_wait(event, "resource_rejected")

    def _retry_linkcraft_action(
        self,
        generation: int,
        event: NavigationEvent,
        action: dict[str, Any],
        retries_remaining: int,
    ) -> None:
        with self._auto_return_lock:
            if not self._is_current_linkcraft_action_locked(generation, event, action):
                return
            self._cancel_arrival_action_timer_locked()
        self._submit_linkcraft_action(
            generation,
            event,
            action,
            retries_remaining=retries_remaining,
        )

    def _on_linkcraft_submit_timeout(
        self,
        generation: int,
        event: NavigationEvent,
        action: dict[str, Any],
    ) -> None:
        with self._auto_return_lock:
            if not self._is_current_linkcraft_action_locked(generation, event, action):
                return
            self._cancel_arrival_action_timer_locked()
            self._finish_linkcraft_stage_locked(
                event,
                "resource_submit_timeout",
            )
        self.get_logger().warning(
            f"灵创资源提交超时，讲解任务继续: resource={action['name']}"
        )
        self._begin_task_wait(event, "resource_submit_timeout")

    def _on_linkcraft_wait_done(
        self,
        generation: int,
        event: NavigationEvent,
        action: dict[str, Any],
    ) -> None:
        with self._auto_return_lock:
            if not self._is_current_linkcraft_action_locked(generation, event, action):
                return
            self._cancel_arrival_action_timer_locked()
            self._finish_linkcraft_stage_locked(
                event,
                "resource_completed",
            )
        self.get_logger().info(f"灵创资源阶段完成: resource={action['name']}")
        self._begin_task_wait(event, "resource_completed")

    def _finish_linkcraft_stage_locked(
        self,
        event: NavigationEvent,
        reason: str,
    ) -> None:
        self._next_auto_return_generation_locked()
        self._clear_linkcraft_runtime_locked()
        self._task_linkcraft_completed = True
        self._auto_return_state = AUTO_RETURN_TASK_ARRIVAL_ACTION
        self._auto_return_reason = reason
        self._begin_task_wait(event, reason)

    def _begin_task_wait(self, event: NavigationEvent, reason: str) -> None:
        """Hold position after all step effects, then advance exactly once."""

        with self._auto_return_lock:
            if (
                self._auto_return_state != AUTO_RETURN_TASK_ARRIVAL_ACTION
                or self._task_current_goal_id != event.goal_id
                or self._task_index < 0
                or self._task_index >= len(self._task_steps)
            ):
                return
            wait_sec = normalize_task_wait_sec(
                self._task_steps[self._task_index].get("wait_sec", DEFAULT_TASK_WAIT_SEC)
            )
            self._auto_return_state = AUTO_RETURN_TASK_WAITING
            self._tour_quiet_since = max(self._tour_quiet_since, time.monotonic())
            self._restart_task_wait_after_interaction_locked(reason)
        self.get_logger().info(
            f"讲解任务点位等待已开始: point={event.point_name}, wait_sec={wait_sec:.1f}, reason={reason}"
        )

    def _resume_task_wait(self, event_id: str) -> dict[str, Any]:
        with self._auto_return_lock:
            if (
                self._auto_return_state != AUTO_RETURN_TASK_PAUSED
                or self._task_pause_origin != AUTO_RETURN_TASK_WAITING
                or self._task_index < 0
                or self._task_index >= len(self._task_steps)
            ):
                return {
                    "ok": False,
                    "accepted": False,
                    "resumed": False,
                    "status": "invalid_task",
                    "message": "暂停的讲解任务没有可恢复的等待阶段",
                    "task": self._task_status_locked(),
                }
            self._auto_return_state = AUTO_RETURN_TASK_WAITING
            self._task_pause_origin = ""
            self._task_paused_phase = ""
            self._task_resume_index = -1
            self._tour_quiet_since = time.monotonic()
            wait_sec = self._restart_task_wait_after_interaction_locked("task_wait_resumed")
        self.get_logger().info(
            f"讲解任务点位等待已继续: event_id={event_id or '<none>'}, wait_sec={wait_sec:.1f}"
        )
        return {
            "ok": True,
            "accepted": True,
            "resumed": True,
            "status": "waiting",
            "task": self._task_status(),
        }

    def _restart_task_wait_after_interaction_locked(self, source: str) -> float:
        """已持锁：一轮问答结束后重新开始完整的点位无交互倒计时。"""

        if (
            self._auto_return_state != AUTO_RETURN_TASK_WAITING
            or self._task_index < 0
            or self._task_index >= len(self._task_steps)
            or not self._task_current_goal_id
        ):
            return 0.0
        wait_sec = normalize_task_wait_sec(
            self._task_steps[self._task_index].get("wait_sec", DEFAULT_TASK_WAIT_SEC)
        )
        generation = self._next_auto_return_generation_locked()
        self._cancel_arrival_action_timer_locked()
        self._auto_return_reason = f"task_wait_interaction_ended:{source}"
        self._task_wait_started_at = max(self._tour_quiet_since, self._tour_reply_until)
        self._task_wait_sec = wait_sec
        self._task_paused_wait_sec = 0.0
        self._task_wait_interaction_active = self._task_wait_started_at > time.monotonic()
        goal_id = self._task_current_goal_id
        self._arrival_action_timer = self.create_timer(
            max(0.05, self._task_wait_started_at + wait_sec - time.monotonic()),
            lambda: self._on_task_wait_done(generation, goal_id),
        )
        return wait_sec

    def _on_task_wait_done(self, generation: int, goal_id: str) -> None:
        next_index = -1
        completed = False
        with self._auto_return_lock:
            if (
                generation != self._auto_return_generation
                or self._auto_return_state != AUTO_RETURN_TASK_WAITING
                or self._task_current_goal_id != goal_id
            ):
                return
            if time.monotonic() < self._task_wait_started_at + self._task_wait_sec:
                self._restart_task_wait_after_interaction_locked("still_active")
                return
            self._next_auto_return_generation_locked()
            self._cancel_arrival_action_timer_locked()
            next_index, completed = self._advance_task_after_arrival_locked()
            if not completed:
                self._start_task_step(next_index)
        if completed:
            self.get_logger().info("讲解任务最后一个点位等待结束，任务已完成")
            return
        self.get_logger().info(f"讲解任务点位等待结束，开始第 {next_index + 1} 个点位")

    def _is_current_linkcraft_action_locked(
        self,
        generation: int,
        event: NavigationEvent,
        action: dict[str, Any],
    ) -> bool:
        return (
            generation == self._auto_return_generation
            and self._auto_return_state == AUTO_RETURN_TASK_LINKCRAFT_ACTION
            and self._task_current_goal_id == event.goal_id
            and isinstance(self._linkcraft_action, dict)
            and self._linkcraft_action.get("resource_key") == action.get("resource_key")
        )

    def _advance_task_after_arrival_locked(self) -> tuple[int, bool]:
        """Advance to the next point, wrap to the first point, or finish once."""

        next_index = self._task_index + 1
        if next_index < len(self._task_steps):
            return next_index, False
        current_loop_index = max(1, self._task_loop_index)
        if current_loop_index < self._task_loop_count:
            self._task_loop_index = current_loop_index + 1
            self._auto_return_reason = (
                f"task_loop_completed:{self._task_loop_index - 1}/{self._task_loop_count}"
            )
            self.get_logger().info(
                f"Tour task loop completed: completed={self._task_loop_index - 1}, "
                f"total={self._task_loop_count}; restarting at step 1"
            )
            return 0, False
        self._clear_task_locked("task_completed")
        self._auto_return_state = AUTO_RETURN_IDLE
        self._auto_return_reason = "task_completed"
        return next_index, True

    def _finish_cancelled_task_step(self, event: NavigationEvent) -> None:
        """任务步骤导航被取消时收尾；结束任务会进入返航等待。"""

        with self._auto_return_lock:
            if not self._is_task_navigation_event_locked(event):
                return
            should_return = self._task_cancel_requested
            self._clear_task_locked("task_cancelled")
            if should_return:
                self._begin_waiting_auto_return_for_goal_locked(
                    event.goal_id,
                    event.point_name,
                    "task_stopped",
                )
            else:
                self._auto_return_state = AUTO_RETURN_IDLE
        self._restore_looping_arrival_emoji()

    def _handle_task_navigation_failure(self, event: NavigationEvent) -> None:
        """Retry once, then skip an unreachable point without ending the tour."""

        with self._auto_return_lock:
            if not self._is_task_navigation_event_locked(event):
                return
            if self._task_failure_recovery_pending:
                self.get_logger().warning(
                    f"Duplicate task navigation failure ignored: point={event.point_name}, "
                    f"task_id={event.task_id}, reason={event.reason}"
                )
                return

            policy = self.navigation_client.defaults.task_failure_policy
            retry_limit = policy.max_retries_per_step
            retrying = self._task_failure_retry_count < retry_limit
            should_continue = bool(policy.continue_after_failure)
            if not retrying and not should_continue:
                self.get_logger().error(
                    f"Tour task stopped by failure policy: point={event.point_name}, "
                    f"reason={event.reason}"
                )
                self._finish_task_without_return(event.status)
                return

            action = "retry" if retrying else "skip"
            self._task_failure_recovery_pending = True
            if retrying:
                self._task_failure_retry_count += 1
            self._task_last_failure = {
                "step_number": self._task_index + 1,
                "point_id": event.goal_id,
                "point_name": event.point_name,
                "event_type": event.type.value,
                "status": event.status,
                "reason": event.reason,
                "distance_to_goal": event.distance_to_goal,
                "retry_count": self._task_failure_retry_count,
                "action": action,
                "created_at": event.created_at,
            }
            self._auto_return_reason = f"task_step_{action}_after_{event.status}"
            generation = self._auto_return_generation
            delay_sec = policy.retry_delay_sec
            self._cancel_task_failure_timer_locked()
            self._task_failure_timer = self.create_timer(
                delay_sec,
                lambda: self._on_task_failure_recovery_timer(generation, event, action),
            )
            self.get_logger().warning(
                f"Tour task navigation {action} scheduled: step={self._task_index + 1}/"
                f"{len(self._task_steps)}, point={event.point_name}, retry="
                f"{self._task_failure_retry_count}/{retry_limit}, delay_sec={delay_sec:.1f}, "
                f"reason={event.reason}"
            )

    def _on_task_failure_recovery_timer(
        self,
        generation: int,
        event: NavigationEvent,
        action: str,
    ) -> None:
        """Continue a tour only after the failed navigation session is released."""

        with self._auto_return_lock:
            if (
                generation != self._auto_return_generation
                or not self._task_failure_recovery_pending
                or self._auto_return_state != AUTO_RETURN_NAVIGATING_TASK_STEP
                or self._task_current_goal_id != event.goal_id
            ):
                return
            self._cancel_task_failure_timer_locked()
            self._task_failure_recovery_pending = False
            current_index = self._task_index

            if action == "retry":
                self._auto_return_reason = f"task_step_retry:{current_index + 1}"
                self.get_logger().info(
                    f"Tour task retrying failed point: step={current_index + 1}, "
                    f"point={event.point_name}, retry={self._task_failure_retry_count}"
                )
            else:
                skipped = {
                    "step_number": current_index + 1,
                    "point_id": event.goal_id,
                    "point_name": event.point_name,
                    "event_type": event.type.value,
                    "status": event.status,
                    "reason": event.reason,
                    "distance_to_goal": event.distance_to_goal,
                    "loop_index": self._task_loop_index,
                    "created_at": event.created_at,
                }
                self._task_skipped_steps.append(skipped)
                self._auto_return_reason = f"task_step_skipped:{current_index + 1}"
                next_index, completed = self._advance_task_after_arrival_locked()
                self.get_logger().warning(
                    f"Tour task skipped unreachable point: step={current_index + 1}, "
                    f"point={event.point_name}, next_index={next_index}, completed={completed}"
                )

        if action == "retry":
            self._start_task_step(current_index, resumed=True, retrying=True)
        elif not completed:
            self._start_task_step(next_index)

    def _finish_task_without_return(self, reason: str) -> None:
        """讲解任务异常结束，不主动返航。"""

        with self._auto_return_lock:
            if self._auto_return_state not in {
                AUTO_RETURN_NAVIGATING_TASK_STEP,
                AUTO_RETURN_TASK_ARRIVAL_ACTION,
                AUTO_RETURN_TASK_LINKCRAFT_ACTION,
                AUTO_RETURN_TASK_WAITING,
            }:
                return
            was_arrival_action_active = self._auto_return_state == AUTO_RETURN_TASK_ARRIVAL_ACTION
            was_linkcraft_active = self._auto_return_state == AUTO_RETURN_TASK_LINKCRAFT_ACTION
            self._next_auto_return_generation_locked()
            self._cancel_arrival_action_timer_locked()
            self._clear_task_locked(reason)
            self._auto_return_state = AUTO_RETURN_IDLE
            self._auto_return_reason = reason
        if was_linkcraft_active:
            self._interrupt_linkcraft_action(reason)
        elif was_arrival_action_active:
            self._stop_arrival_motion(reason)
        self._restore_looping_arrival_emoji()
        self.get_logger().info(f"Tour task finished without auto return: reason={reason}")

    def _cancel_task_for_new_intent(self, reason: str) -> bool:
        """新导航意图抢占讲解任务；不触发返航等待。"""

        stopped, previous_state, _, _, should_cancel_navigation = self._cancel_task_flow(reason)
        navigation_cancelled = False
        if should_cancel_navigation:
            navigation_cancelled = self.navigation_client.cancel(reason=reason)
        if previous_state == AUTO_RETURN_TASK_LINKCRAFT_ACTION:
            self._interrupt_linkcraft_action(reason)
        elif previous_state == AUTO_RETURN_TASK_ARRIVAL_ACTION:
            self._stop_arrival_motion(reason)
        if stopped:
            self.get_logger().info(
                f"Tour task preempted: previous_state={previous_state}, reason={reason}, "
                f"navigation_cancelled={navigation_cancelled}"
            )
        return stopped

    def _cancel_task_flow(self, reason: str) -> tuple[bool, str, str, str, bool]:
        """取消当前讲解任务，返回是否需要取消底层导航。"""

        with self._auto_return_lock:
            previous_state = self._auto_return_state
            if previous_state not in {
                AUTO_RETURN_NAVIGATING_TASK_STEP,
                AUTO_RETURN_TASK_ARRIVAL_ACTION,
                AUTO_RETURN_TASK_LINKCRAFT_ACTION,
                AUTO_RETURN_TASK_WAITING,
                AUTO_RETURN_TASK_PAUSED,
            }:
                return False, previous_state, "", "", False
            goal_id = self._task_current_goal_id or self._task_last_goal_id
            point_name = self._point_name_for_id(goal_id)
            should_cancel_navigation = previous_state == AUTO_RETURN_NAVIGATING_TASK_STEP
            self._task_cancel_requested = True
            self._next_auto_return_generation_locked()
            self._cancel_arrival_action_timer_locked()
            self._clear_task_locked(reason)
            self._auto_return_state = AUTO_RETURN_IDLE
            self._auto_return_reason = reason
            return True, previous_state, goal_id, point_name, should_cancel_navigation

    def _clear_task_locked(self, reason: str) -> None:
        """已持锁：清理讲解任务运行态。"""

        self._task_id = ""
        self._task_name = ""
        self._task_steps = []
        self._task_index = -1
        self._task_loop_count = 1
        self._task_loop_index = 0
        self._task_navigation_task_id = None
        self._task_current_goal_id = ""
        self._task_last_goal_id = ""
        self._task_cancel_requested = False
        self._cancel_task_failure_timer_locked()
        self._task_failure_recovery_pending = False
        self._task_failure_retry_count = 0
        self._task_skipped_steps = []
        self._task_resume_index = -1
        self._task_pause_origin = ""
        self._task_interruption_wait_started_at = 0.0
        self._task_paused_phase = ""
        self._task_arrival_event = None
        self._task_arrival_effects_until = 0.0
        self._task_linkcraft_completed = False
        self._tour_reply_generation += 1
        self._tour_reply_until = 0.0
        self._tour_interaction_event_id = ""
        self._tour_interaction_ended = True
        self._tour_quiet_since = time.monotonic()
        self._clear_task_wait_runtime_locked()
        self._clear_linkcraft_runtime_locked()
        self._auto_return_target_id = ""
        self._auto_return_reason = reason

    def _clear_linkcraft_runtime_locked(self) -> None:
        self._linkcraft_action = None
        self._linkcraft_wait_started_at = 0.0
        self._linkcraft_wait_sec = 0.0

    def _clear_task_wait_runtime_locked(self) -> None:
        self._task_wait_started_at = 0.0
        self._task_wait_sec = 0.0
        self._task_paused_wait_sec = 0.0
        self._task_wait_interaction_active = False

    def _interrupt_linkcraft_action(self, reason: str) -> None:
        """Best-effort interruption through the documented empty preset motion."""

        self.get_logger().info(f"尝试中断灵创资源: reason={reason}")
        self._stop_arrival_motion(f"linkcraft:{reason}", force=True)

    def _begin_arrival_action(self, event: NavigationEvent) -> None:
        """用户导航完成后进入到达动作阶段。"""

        with self._auto_return_lock:
            if not self._is_user_navigation_event_locked(event):
                return
            self._auto_return_state = AUTO_RETURN_ARRIVAL_ACTION
            self._auto_return_source_goal_id = event.goal_id
            self._auto_return_reason = "arrival_action"
        self.get_logger().info(
            f"Auto return state: {AUTO_RETURN_ARRIVAL_ACTION}, point={event.point_name}, goal_id={event.goal_id}"
        )

    def _schedule_auto_return_after_arrival(self, event: NavigationEvent, wait_sec: float) -> None:
        """到达动作结束后，安排进入自动返航等待阶段。"""

        wait_sec = max(0.0, float(wait_sec))
        transition_complete = False
        with self._auto_return_lock:
            if (
                self._auto_return_state != AUTO_RETURN_ARRIVAL_ACTION
                or self._auto_return_source_goal_id != event.goal_id
            ):
                return
            if wait_sec <= 0.0:
                self._begin_waiting_auto_return_locked(event, "arrival_action_done")
                transition_complete = True
            else:
                generation = self._next_auto_return_generation_locked()
                self._cancel_arrival_action_timer_locked()
                self._arrival_action_timer = self.create_timer(
                    wait_sec,
                    lambda: self._on_arrival_action_wait_done(generation, event),
                )
                self.get_logger().info(
                    f"Arrival action wait started: point={event.point_name}, wait_sec={wait_sec:.1f}"
                )
        if transition_complete:
            self._finish_arrival_motion_after_wait("arrival_action_done")
            self._restore_looping_arrival_emoji()

    def _on_arrival_action_wait_done(self, generation: int, event: NavigationEvent) -> None:
        """到达动作等待定时器回调；generation 用于丢弃旧定时器。"""

        with self._auto_return_lock:
            if generation != self._auto_return_generation:
                return
            self._cancel_arrival_action_timer_locked()
            if (
                self._auto_return_state != AUTO_RETURN_ARRIVAL_ACTION
                or self._auto_return_source_goal_id != event.goal_id
            ):
                return
            self._begin_waiting_auto_return_locked(event, "arrival_action_done")
        self._finish_arrival_motion_after_wait("arrival_action_done")
        self._restore_looping_arrival_emoji()

    def _begin_waiting_auto_return(self, event: NavigationEvent, reason: str) -> None:
        """线程安全地进入自动返航等待阶段。"""

        with self._auto_return_lock:
            self._begin_waiting_auto_return_locked(event, reason)

    def _begin_waiting_auto_return_locked(self, event: NavigationEvent, reason: str) -> None:
        """在已持锁的前提下设置自动返航等待定时器。"""

        self._begin_waiting_auto_return_for_goal_locked(event.goal_id, event.point_name, reason)

    def _begin_waiting_auto_return_for_goal_locked(self, source_goal_id: str, point_name: str, reason: str) -> None:
        """在已持锁的前提下，为指定源点设置自动返航等待定时器。"""

        self._cancel_arrival_action_timer_locked()
        self._cancel_auto_return_timer_locked()
        if not self.auto_return_enabled:
            self._auto_return_state = AUTO_RETURN_IDLE
            self._auto_return_reason = "disabled"
            return
        if not source_goal_id:
            self._auto_return_state = AUTO_RETURN_IDLE
            self._auto_return_reason = "no_source_goal"
            return
        if self._is_start_point(source_goal_id):
            self._auto_return_state = AUTO_RETURN_IDLE
            self._auto_return_reason = "already_at_start"
            return
        start_point = self.point_catalog.get_start_point(self.active_map_name)
        if start_point is None:
            self._auto_return_state = AUTO_RETURN_IDLE
            self._auto_return_reason = "no_start_point"
            self.get_logger().warning("Auto return skipped: no is_start point configured")
            return

        # 到这里说明可以返航：当前点不是起点，并且当前地图配置了 is_start 起点。
        self._auto_return_state = AUTO_RETURN_WAITING
        self._auto_return_source_goal_id = source_goal_id
        self._auto_return_target_id = start_point.id
        self._auto_return_reason = reason
        generation = self._next_auto_return_generation_locked()
        self._auto_return_timer = self.create_timer(
            self.auto_return_idle_sec,
            lambda: self._on_auto_return_timer(generation),
        )
        self.get_logger().info(
            f"Auto return wait started: from={point_name or source_goal_id}, start_point={start_point.name}, "
            f"idle_sec={self.auto_return_idle_sec:.1f}, reason={reason}"
        )

    def _on_auto_return_timer(self, generation: int) -> None:
        """自动返航空闲定时器回调；确认无人交互后才真正发起返航导航。"""

        with self._auto_return_lock:
            if generation != self._auto_return_generation:
                return
            self._cancel_auto_return_timer_locked()
            if self._auto_return_state != AUTO_RETURN_WAITING:
                return
            idle_elapsed = time.monotonic() - self._last_valid_interaction_at
            if idle_elapsed < self.auto_return_idle_sec:
                # 等待期间出现过交互，按剩余空闲时间重新计时。
                remaining = self.auto_return_idle_sec - idle_elapsed
                self._auto_return_timer = self.create_timer(
                    remaining,
                    lambda: self._on_auto_return_timer(generation),
                )
                return
            self._auto_return_state = AUTO_RETURN_RETURNING_HOME
            self._auto_return_reason = "idle_timeout"

        self._start_auto_return_navigation()

    def _start_auto_return_navigation(self) -> None:
        """向导航客户端提交回起点任务。"""

        start_point = self.point_catalog.get_start_point(self.active_map_name)
        if start_point is None:
            self.get_logger().warning("Auto return skipped: no is_start point configured")
            self._finish_auto_return("no_start_point")
            return
        if self.navigation_client.get_status().get("status") != "idle":
            self.get_logger().info("Auto return skipped: navigation client is no longer idle")
            self._finish_auto_return("navigation_busy")
            return
        with self._auto_return_lock:
            self._auto_return_target_id = start_point.id
            self._auto_return_state = AUTO_RETURN_RETURNING_HOME
        self.get_logger().info(f"Auto return navigation starting: start_point={start_point.name}")
        handle = self.navigation_client.start(start_point, listener=self.handle_navigation_event)
        if not handle.accepted:
            self.get_logger().warning(f"Auto return navigation rejected: status={handle.status}, reason={handle.reason}")
            self._finish_auto_return(handle.status)

    def _finish_auto_return(self, reason: str) -> None:
        """结束自动返航流程并清理状态。"""

        with self._auto_return_lock:
            previous_state = self._auto_return_state
            if previous_state != AUTO_RETURN_RETURNING_HOME:
                self.get_logger().info(
                    f"Auto return finish ignored: current_state={previous_state}, reason={reason}"
                )
                return
            self._cancel_arrival_action_timer_locked()
            self._cancel_auto_return_timer_locked()
            self._auto_return_state = AUTO_RETURN_IDLE
            self._auto_return_reason = reason
            self._auto_return_source_goal_id = ""
            self._auto_return_target_id = ""
        self.get_logger().info(f"Auto return finished: previous_state={previous_state}, reason={reason}")

    def _cancel_pending_auto_return(self, reason: str) -> None:
        """取消尚未真正发车的自动返航流程。"""

        cancelled = False
        with self._auto_return_lock:
            if self._auto_return_state in {
                AUTO_RETURN_ARRIVAL_ACTION,
                AUTO_RETURN_WAITING,
                AUTO_RETURN_INTERACTION_ACTIVE,
            }:
                self._cancel_arrival_action_timer_locked()
                self._cancel_auto_return_timer_locked()
                self._auto_return_state = AUTO_RETURN_IDLE
                self._auto_return_reason = reason
                cancelled = True
        if cancelled:
            self.get_logger().info(f"Auto return pending flow cancelled: reason={reason}")

    def _cancel_auto_return_flow(self, reason: str) -> tuple[bool, str, bool]:
        """显式取消当前返航相关流程，返回是否需要取消底层导航。"""

        with self._auto_return_lock:
            previous_state = self._auto_return_state
            should_cancel_navigation = previous_state in {
                AUTO_RETURN_RETURNING_HOME,
            }
            cancellable = previous_state in {
                AUTO_RETURN_NAVIGATING_USER_TARGET,
                AUTO_RETURN_ARRIVAL_ACTION,
                AUTO_RETURN_WAITING,
                AUTO_RETURN_INTERACTION_ACTIVE,
                AUTO_RETURN_RETURNING_HOME,
            }
            if not cancellable:
                return False, previous_state, False
            self._cancel_arrival_action_timer_locked()
            self._cancel_auto_return_timer_locked()
            self._auto_return_state = AUTO_RETURN_IDLE
            self._auto_return_reason = reason
            self._auto_return_source_goal_id = ""
            self._auto_return_target_id = ""

        self.get_logger().info(
            f"Auto return flow cancelled: previous_state={previous_state}, reason={reason}, "
            f"should_cancel_navigation={should_cancel_navigation}"
        )
        return True, previous_state, should_cancel_navigation

    def _cancel_auto_return_timers(self) -> None:
        """取消自动返航相关的全部定时器。"""

        with self._auto_return_lock:
            self._cancel_arrival_action_timer_locked()
            self._cancel_auto_return_timer_locked()

    def _cancel_arrival_action_timer_locked(self) -> None:
        """取消到达动作等待定时器；调用方必须已经持有自动返航锁。"""

        if self._arrival_action_timer is not None:
            self._arrival_action_timer.cancel()
            self._arrival_action_timer = None

    def _cancel_task_failure_timer_locked(self) -> None:
        timer = getattr(self, "_task_failure_timer", None)
        if timer is not None:
            timer.cancel()
        self._task_failure_timer = None

    def _cancel_auto_return_timer_locked(self) -> None:
        """取消空闲返航定时器；调用方必须已经持有自动返航锁。"""

        if self._auto_return_timer is not None:
            self._auto_return_timer.cancel()
            self._auto_return_timer = None

    def _next_auto_return_generation_locked(self) -> int:
        """递增自动返航 generation，用来让旧定时器回调失效。"""

        self._auto_return_generation += 1
        return self._auto_return_generation

    def _is_user_navigation_event(self, event: NavigationEvent) -> bool:
        """判断事件是否属于当前用户发起的导航任务。"""

        with self._auto_return_lock:
            return self._is_user_navigation_event_locked(event)

    def _is_user_navigation_event_locked(self, event: NavigationEvent) -> bool:
        """已持锁版本的用户导航事件判断。"""

        return (
            self._auto_return_state == AUTO_RETURN_NAVIGATING_USER_TARGET
            and self._auto_return_source_goal_id == event.goal_id
        )

    def _is_task_navigation_event(self, event: NavigationEvent) -> bool:
        """判断事件是否属于当前讲解任务步骤。"""

        with self._auto_return_lock:
            return self._is_task_navigation_event_locked(event)

    def _is_task_navigation_event_locked(self, event: NavigationEvent) -> bool:
        """已持锁版本的讲解任务事件判断。"""

        return (
            self._auto_return_state == AUTO_RETURN_NAVIGATING_TASK_STEP
            and not self._task_failure_recovery_pending
            and self._task_current_goal_id == event.goal_id
            and (
                self._task_navigation_task_id is None
                or event.task_id == self._task_navigation_task_id
            )
        )

    def _is_auto_return_event(self, event: NavigationEvent) -> bool:
        """判断事件是否属于当前自动返航任务。"""

        with self._auto_return_lock:
            return (
                self._auto_return_state == AUTO_RETURN_RETURNING_HOME
                and self._auto_return_target_id == event.goal_id
            )

    def _is_start_point(self, point_id: str) -> bool:
        """判断给定点位是否为当前地图配置的起点。"""

        try:
            return self.point_catalog.get_by_id(point_id).is_start
        except PointCatalogError:
            start_point = self.point_catalog.get_start_point(self.active_map_name)
            return start_point is not None and start_point.id == point_id

    def _point_name_for_id(self, point_id: str) -> str:
        """返回点位名称；仅用于日志和状态恢复，失败时返回空字符串。"""

        if not point_id:
            return ""
        try:
            return self.point_catalog.get_by_id(point_id).name
        except PointCatalogError:
            return ""

    def _arrival_action_wait_sec(self, action_type: str, payload: dict[str, Any]) -> float:
        """计算到达动作结束后进入自动返航等待前的缓冲时间。"""

        for key in ("auto_return_wait_sec", "wait_sec", "duration_sec", "expected_duration_sec"):
            if key in payload:
                try:
                    return max(0.0, float(payload.get(key, 0.0)))
                except (TypeError, ValueError):
                    self.get_logger().warning(f"Invalid arrival action wait value: key={key}, value={payload.get(key)}")
                    return 0.0
        if action_type == "tts":
            text = str(payload.get("text", "")).strip()
            if not text:
                return 0.0
            # 没有显式时长时按中文播报长度估算，避免 TTS 还没播完就开始返航倒计时。
            compact_len = len("".join(text.split()))
            return min(ARRIVAL_TTS_MAX_WAIT_SEC, max(2.0, compact_len / 4.0 + 0.8))
        if action_type == "motion":
            if str(payload.get("motion", "")).strip().lower() in {"empty_motion", "0"}:
                return 0.0
            return 3.0
        if action_type == "emoji":
            return 3.0 if str(payload.get("mode", "once")).strip() == "loop" else 2.0
        return 0.0

    def _preview_action_text(self, payload: dict[str, Any]) -> str:
        """生成日志用的动作文本预览，避免长文本刷屏。"""

        text = str(payload.get("text", "")).strip()
        if not text:
            return "<none>"
        text = " ".join(text.split())
        return text if len(text) <= 40 else text[:40] + "..."

    def _arrival_action_summary(self, action_type: str, payload: dict[str, Any]) -> str:
        if action_type == "motion":
            return (
                f"motion={payload.get('motion')}, area={payload.get('area')}, "
                f"interrupt={bool(payload.get('interrupt', False))}, force={bool(payload.get('force', True))}"
            )
        if action_type == "emoji":
            return f"emotion={payload.get('emotion')}, mode={payload.get('mode', 'once')}"
        if action_type == "tts":
            return f"text={self._preview_action_text(payload)}"
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _arrival_actions_summary(self, actions: list[dict[str, Any]]) -> str:
        return "[" + "; ".join(
            self._arrival_action_summary(
                str(action.get("type", "")).strip(),
                {key: value for key, value in action.items() if key != "type"},
            )
            for action in actions
        ) + "]"

    def _auto_return_status(self) -> dict[str, Any]:
        """组装 /nav/status 中的自动返航状态。"""

        with self._auto_return_lock:
            idle_elapsed = time.monotonic() - self._last_valid_interaction_at
            return {
                "enabled": self.auto_return_enabled,
                "state": self._auto_return_state,
                "interaction_active": self._auto_return_state == AUTO_RETURN_INTERACTION_ACTIVE,
                "idle_sec": self.auto_return_idle_sec,
                "idle_elapsed_sec": round(idle_elapsed, 3),
                "source_goal_id": self._auto_return_source_goal_id,
                "target_goal_id": self._auto_return_target_id,
                "reason": self._auto_return_reason,
            }

    def _task_status(self) -> dict[str, Any]:
        """组装 /nav/status 中的讲解任务状态。"""

        with self._auto_return_lock:
            return self._task_status_locked()

    def _dense_crowd_profile_status(self) -> dict[str, Any]:
        profile = self.navigation_client.defaults.dense_crowd
        return {
            "enabled": profile.enabled,
            "target_pose_radius_m": profile.target_pose_radius,
            "position_tolerance_m": profile.position_tolerance_m,
            "yaw_tolerance_deg": round(profile.yaw_tolerance_rad * 180.0 / 3.141592653589793, 1),
            "max_forward_speed_mps": profile.max_forward_speed,
            "stable_duration_sec": profile.stable_duration_sec,
            "requires_low_speed": profile.require_low_speed,
            "timeout_sec": profile.timeout_sec,
            "obstacle_clearance": "aimdk_internal_not_exposed",
        }

    def _task_status_locked(self) -> dict[str, Any]:
        """已持锁版本的讲解任务状态。"""

        policy = self.navigation_client.defaults.task_failure_policy
        return {
            "active": self._auto_return_state in {
                AUTO_RETURN_NAVIGATING_TASK_STEP,
                AUTO_RETURN_TASK_ARRIVAL_ACTION,
                AUTO_RETURN_TASK_LINKCRAFT_ACTION,
                AUTO_RETURN_TASK_WAITING,
                AUTO_RETURN_TASK_PAUSED,
            },
            "paused": self._auto_return_state == AUTO_RETURN_TASK_PAUSED,
            "id": self._task_id,
            "name": self._task_name,
            "index": self._task_index,
            "step_number": self._task_index + 1 if self._task_index >= 0 else 0,
            "step_count": len(self._task_steps),
            "loop_count": self._task_loop_count,
            "loop_index": self._task_loop_index,
            "loops_completed": max(0, self._task_loop_index - 1) if self._task_loop_index else 0,
            "loops_remaining": (
                max(0, self._task_loop_count - self._task_loop_index)
                if self._task_loop_index
                else 0
            ),
            "current_goal_id": self._task_current_goal_id,
            "last_goal_id": self._task_last_goal_id,
            "state": self._auto_return_state,
            "failure_recovery": {
                "mode": "retry_then_skip" if policy.continue_after_failure else "stop",
                "pending": getattr(self, "_task_failure_recovery_pending", False),
                "retry_count": getattr(self, "_task_failure_retry_count", 0),
                "max_retries_per_step": policy.max_retries_per_step,
                "skipped_steps": list(getattr(self, "_task_skipped_steps", [])),
                "last_failure": getattr(self, "_task_last_failure", None),
            },
            "linkcraft": self._linkcraft_status_locked(),
            "wait": self._task_wait_status_locked(),
            "interruption": self._task_interruption_status_locked(),
        }

    def _task_interruption_status_locked(self) -> dict[str, Any]:
        active = should_auto_resume_voice_interruption(
            self._auto_return_state,
            self._task_pause_origin,
        )
        elapsed = (
            max(0.0, time.monotonic() - self._task_interruption_wait_started_at)
            if active and self._task_interruption_wait_started_at > 0.0
            else 0.0
        )
        return {
            "active": active,
            "resume_after_silence_sec": VOICE_INTERRUPTION_RESUME_SEC,
            "remaining_sec": round(max(0.0, VOICE_INTERRUPTION_RESUME_SEC - elapsed), 3),
        }

    def _task_wait_status_locked(self) -> dict[str, Any]:
        active = self._auto_return_state == AUTO_RETURN_TASK_WAITING
        if active:
            self._task_wait_interaction_active = self._task_wait_started_at > time.monotonic()
        if active and self._task_wait_interaction_active:
            remaining_sec = self._task_wait_sec
        elif active and self._task_wait_started_at > 0.0:
            remaining_sec = max(
                0.0,
                self._task_wait_sec - (time.monotonic() - self._task_wait_started_at),
            )
        elif self._auto_return_state == AUTO_RETURN_TASK_PAUSED and self._task_pause_origin == AUTO_RETURN_TASK_WAITING:
            remaining_sec = self._task_paused_wait_sec
        else:
            remaining_sec = 0.0
        return {
            "active": active,
            "interaction_active": self._task_wait_interaction_active,
            "duration_sec": round(self._task_wait_sec, 3),
            "remaining_sec": round(remaining_sec, 3),
        }

    def _linkcraft_status_locked(self) -> dict[str, Any]:
        active = (
            self._auto_return_state == AUTO_RETURN_TASK_LINKCRAFT_ACTION
            and isinstance(self._linkcraft_action, dict)
        )
        elapsed_sec = (
            max(0.0, time.monotonic() - self._linkcraft_wait_started_at)
            if active and self._linkcraft_wait_started_at > 0.0
            else 0.0
        )
        return {
            "active": active,
            "name": str((self._linkcraft_action or {}).get("name", "")),
            "resource_key": str((self._linkcraft_action or {}).get("resource_key", "")),
            "duration_sec": float((self._linkcraft_action or {}).get("duration_sec", 0.0) or 0.0),
            "remaining_sec": round(max(0.0, self._linkcraft_wait_sec - elapsed_sec), 3),
        }


class NavHttpHandler(BaseHTTPRequestHandler):
    """把 HTTP 路由转换为 LingxiNavBridge 的方法调用。"""

    def do_GET(self) -> None:
        """处理只读查询接口。"""

        # Java Agent 和 shell 巡检使用的轻量只读接口。
        if self.path == "/points":
            self.write_json(200, self.server.bridge.list_point_names())
            return
        if self.path == "/nav/status":
            self.write_json(200, self.server.bridge.status())
            return
        if self.path == "/task/commands":
            self.write_json(200, self.server.bridge.list_task_commands())
            return
        if self.path == "/linkcraft/resources":
            self.write_json(200, self.server.bridge.list_linkcraft_resources())
            return
        if self.path == "/localization/current":
            self.write_json(200, self.server.bridge.current_localization())
            return
        self.write_json(404, {"ok": False, "message": "not found"})

    def do_POST(self) -> None:
        """处理会改变状态的控制接口。"""

        try:
            payload = self.read_json()
            # 会改变状态的接口：启动/停止导航、切换配置、记录交互和 TTS 播报。
            if self.path == "/nav/start":
                self.write_json(200, self.server.bridge.start_navigation(payload))
                return
            if self.path == "/nav/stop":
                self.write_json(200, self.server.bridge.stop_navigation(payload))
                return
            if self.path == "/task/start":
                self.write_json(200, self.server.bridge.start_task(payload))
                return
            if self.path == "/task/match":
                self.write_json(200, self.server.bridge.match_task_command_request(payload))
                return
            if self.path == "/task/pause":
                self.write_json(200, self.server.bridge.pause_task(payload))
                return
            if self.path == "/task/resume":
                self.write_json(200, self.server.bridge.resume_task(payload))
                return
            if self.path in {"/task/stop", "/task/end"}:
                self.write_json(200, self.server.bridge.stop_task(payload))
                return
            if self.path == "/nav/mode":
                self.write_json(200, self.server.bridge.set_nav_mode(payload))
                return
            if self.path == "/nav/map":
                self.write_json(200, self.server.bridge.set_active_map(payload))
                return
            if self.path == "/nav/reload":
                self.write_json(200, self.server.bridge.reload_catalog())
                return
            if self.path == "/interaction/mark":
                self.write_json(200, self.server.bridge.mark_interaction(payload))
                return
            if self.path == "/interaction/wake":
                self.write_json(200, self.server.bridge.wake_interaction(payload))
                return
            if self.path == "/interaction/end":
                self.write_json(200, self.server.bridge.end_interaction(payload))
                return
            if self.path == "/auto-return/cancel":
                self.write_json(200, self.server.bridge.cancel_auto_return(payload))
                return
            if self.path == "/motion/stop":
                self.write_json(200, self.server.bridge.stop_arrival_motion(payload))
                return
            if self.path == "/motion/play":
                self.write_json(200, self.server.bridge.play_voice_preset_motion(payload))
                return
            if self.path == "/tts/speak":
                self.write_json(200, self.server.bridge.speak(payload))
                return
            self.write_json(404, {"ok": False, "message": "not found"})
        except Exception as exc:
            self.write_json(500, {"ok": False, "message": str(exc)})

    def read_json(self) -> dict[str, Any]:
        """读取请求体并校验它必须是 JSON object。"""

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        """按 UTF-8 JSON 格式写回 HTTP 响应。"""

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        """把 BaseHTTPRequestHandler 的访问日志转接到 ROS logger。"""

        self.server.bridge.get_logger().info(format % args)


def handle_to_dict(handle: NavigationSessionHandle) -> dict[str, Any]:
    """将导航客户端返回的 session handle 转成 HTTP JSON 响应。"""

    return {
        "task_id": handle.task_id,
        "goal_id": handle.goal_id,
        "point_name": handle.point_name,
        "accepted": handle.accepted,
        "status": handle.status,
        "reason": handle.reason,
    }


def parse_args() -> argparse.Namespace:
    """解析命令行参数和默认应用目录。"""

    default_home = Path.home() / "robot_local_navigation_v3"
    app_home = Path(
        os.environ.get(
            "ROBOT_LOCAL_NAVIGATION_V3_HOME",
            os.environ.get("ROBOT_LOCAL_NAV_V2_HOME", default_home),
        )
    )
    parser = argparse.ArgumentParser(description="HTTP bridge from Lingxi Java agent to ROS2 navigation")
    parser.add_argument("--config-json", default=str(app_home / "unified_nav_config.json"))
    parser.add_argument("--active-map-name")
    parser.add_argument("--nav-mode", choices=["execute", "dry-run"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    return parser.parse_args()


def main(args=None) -> None:
    """初始化 ROS2、启动桥接节点，并进入 rclpy 事件循环。"""

    cli_args = parse_args()
    rclpy.init(args=args)
    node = LingxiNavBridge(
        config_json=cli_args.config_json,
        active_map_name=cli_args.active_map_name,
        nav_mode=cli_args.nav_mode,
        host=cli_args.host,
        port=cli_args.port,
    )
    try:
        # HTTP 服务运行在后台线程；主线程专注处理 ROS2 回调。
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
