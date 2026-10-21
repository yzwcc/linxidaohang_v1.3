from __future__ import annotations

import threading
import time
from dataclasses import dataclass
import math
import os
from typing import Any

from aimdk_msgs.msg import MessageHeader, PncTaskRequest
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

from robot_local_navigation_v3.arrival_policy import ArrivalPolicy, ArrivalSample
from robot_local_navigation_v3.arrival_hysteresis import (
    POST_ENTRY_LOW_SPEED_THRESHOLD_MPS,
    is_arrival_sample_stable,
    is_arrival_stability_candidate,
    is_post_entry_arrival_latch_active,
    is_within_arrival_hysteresis,
)
from robot_local_navigation_v3.models import NavPoint, NavigationDefaults
from robot_local_navigation_v3.near_goal_arrival import NearGoalArrival
from robot_local_navigation_v3.navigation_events import (
    NavigationEvent,
    NavigationEventListener,
    NavigationEventType,
    emit_safely,
)
from robot_local_navigation_v3.pnc_response import (
    claim_bounded_retry,
    classify_pnc_response_support_error,
    parse_pnc_task_response,
)


TASK_REQUEST_START = 1
TASK_REQUEST_STOP = 2
PNC_RESPONSE_TOPIC = "/aima/pnc/pnc_task_response"
PNC_RESPONSE_RETRY_PERIOD_SEC = 2.0
PNC_RESPONSE_MAX_RETRIES = 15
DEFAULT_ODOM_MAX_AGE_SEC = 2.0
DEFAULT_START_PREFLIGHT_RETRIES = 5
DEFAULT_START_PREFLIGHT_DELAY_SEC = 0.2


@dataclass(frozen=True)
class NavigationSessionHandle:
    task_id: int | None
    goal_id: str
    point_name: str
    accepted: bool
    status: str
    reason: str = ""


class NavigationClient:
    """Single-session navigation wrapper that emits high-level navigation events."""

    def __init__(
        self,
        node,
        defaults: NavigationDefaults,
        *,
        mode: str = "execute",
        topic_name: str = "/aima/te/pnc_task_request",
        odom_topic: str = "/slam/lidar_odom",
        republish_period_sec: float = 1.0,
        progress_period_sec: float = 1.0,
    ):
        if mode not in {"execute", "dry-run"}:
            raise ValueError("mode must be 'execute' or 'dry-run'")
        self.node = node
        self.defaults = defaults
        self.mode = mode
        self.republish_period_sec = max(0.1, float(republish_period_sec))
        self.progress_period_sec = max(0.1, float(progress_period_sec))
        self.command_topic = topic_name
        self.odom_topic = odom_topic
        try:
            configured_odom_age = float(
                os.environ.get("NAV_ODOM_MAX_AGE_SEC", str(DEFAULT_ODOM_MAX_AGE_SEC))
            )
        except ValueError:
            configured_odom_age = DEFAULT_ODOM_MAX_AGE_SEC
        self.odom_max_age_sec = max(0.2, min(30.0, configured_odom_age))
        try:
            configured_preflight_retries = int(
                os.environ.get(
                    "NAV_START_PREFLIGHT_RETRIES",
                    str(DEFAULT_START_PREFLIGHT_RETRIES),
                )
            )
        except ValueError:
            configured_preflight_retries = DEFAULT_START_PREFLIGHT_RETRIES
        self.start_preflight_retries = max(0, min(20, configured_preflight_retries))
        try:
            configured_preflight_delay = float(
                os.environ.get(
                    "NAV_START_PREFLIGHT_DELAY_SEC",
                    str(DEFAULT_START_PREFLIGHT_DELAY_SEC),
                )
            )
        except ValueError:
            configured_preflight_delay = DEFAULT_START_PREFLIGHT_DELAY_SEC
        self.start_preflight_delay_sec = max(0.0, min(2.0, configured_preflight_delay))
        self._lock = threading.RLock()
        self._active_session: NavigationSession | None = None
        self._cancel_generation = 0
        self._pending_starts = 0
        self._last_task_id = 0
        self._last_pose: dict[str, object] | None = None
        self._last_pose_at: float | None = None
        self._command_subscriber_count: int | None = None
        self._last_preflight: dict[str, object] = {
            "ok": True,
            "status": "not_checked",
            "reason": "",
            "checked_at": None,
        }
        self.pnc_response_topic = (
            os.environ.get("NAV_PNC_RESPONSE_TOPIC", PNC_RESPONSE_TOPIC).strip()
            or PNC_RESPONSE_TOPIC
        )
        self.pnc_response_type = ""
        self.pnc_response_subscription = None
        self._pnc_response_retry_timer = None
        self._pnc_response_retry_count = 0
        try:
            configured_retries = int(
                os.environ.get(
                    "NAV_PNC_RESPONSE_DISCOVERY_RETRIES",
                    str(PNC_RESPONSE_MAX_RETRIES),
                )
            )
        except ValueError:
            configured_retries = PNC_RESPONSE_MAX_RETRIES
        self._pnc_response_max_retries = max(0, min(120, configured_retries))
        self._pnc_response_status = "initializing"
        self._pnc_response_publisher_count: int | None = None
        self._pnc_response_notice_key = ""
        self._pnc_response_last: dict[str, Any] = {
            "task_id": None,
            "state_value": None,
            "disposition": "unknown",
            "received_at": None,
        }
        self._pnc_response_error = ""

        cmd_qos = QoSProfile(
            depth=10,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.publisher = node.create_publisher(PncTaskRequest, topic_name, cmd_qos)
        odom_qos = QoSProfile(depth=10, reliability=QoSReliabilityPolicy.BEST_EFFORT)
        self.odom_subscription = node.create_subscription(
            Odometry,
            odom_topic,
            self._handle_odom,
            qos_profile=odom_qos,
        )
        self._setup_pnc_response_subscription()

    def start(
        self,
        point: NavPoint,
        listener: NavigationEventListener | None = None,
        *,
        timeout_sec: float | None = None,
        allow_initial_arrival_hysteresis: bool = False,
        task_id: int | None = None,
    ) -> NavigationSessionHandle:
        with self._lock:
            if self._active_session is not None and self._active_session.is_active:
                session = self._active_session
                reason = f"navigation is busy with task_id={session.task_id}"
                event = NavigationEvent(
                    type=NavigationEventType.REJECTED,
                    task_id=None,
                    goal_id=point.id,
                    point_name=point.name,
                    status="busy",
                    reason=reason,
                )
                emit_safely(listener, event, logger=self.node.get_logger())
                return NavigationSessionHandle(
                    task_id=None,
                    goal_id=point.id,
                    point_name=point.name,
                    accepted=False,
                    status="busy",
                    reason=reason,
                )
            generation = self._cancel_generation
            self._pending_starts += 1

        try:
            return self._start_pending(
                point, listener, generation=generation, timeout_sec=timeout_sec,
                allow_initial_arrival_hysteresis=allow_initial_arrival_hysteresis,
                task_id=task_id,
            )
        finally:
            with self._lock:
                self._pending_starts -= 1

    def _start_pending(
        self, point, listener, *, generation: int, timeout_sec: float | None,
        allow_initial_arrival_hysteresis: bool, task_id: int | None,
    ) -> NavigationSessionHandle:

        # The ROS graph and the first odometry frame are often late during
        # service startup. Do this outside the session lock so an odometry
        # callback can refresh the status while the bounded graph probe waits.
        preflight = self._preflight_start() if self.mode != "dry-run" else None
        with self._lock:
            if generation != self._cancel_generation or preflight is not None:
                if generation != self._cancel_generation:
                    status, reason = "cancelled", "navigation start cancelled during preflight"
                else:
                    status, reason = preflight
                event = NavigationEvent(
                    type=NavigationEventType.REJECTED,
                    task_id=None,
                    goal_id=point.id,
                    point_name=point.name,
                    status=status,
                    reason=reason,
                )
                emit_safely(listener, event, logger=self.node.get_logger())
                return NavigationSessionHandle(
                    task_id=None,
                    goal_id=point.id,
                    point_name=point.name,
                    accepted=False,
                    status=status,
                    reason=reason,
                )
            if self._active_session is not None and self._active_session.is_active:
                session = self._active_session
                reason = f"navigation became busy with task_id={session.task_id}"
                event = NavigationEvent(
                    type=NavigationEventType.REJECTED,
                    task_id=None,
                    goal_id=point.id,
                    point_name=point.name,
                    status="busy",
                    reason=reason,
                )
                emit_safely(listener, event, logger=self.node.get_logger())
                return NavigationSessionHandle(
                    task_id=None,
                    goal_id=point.id,
                    point_name=point.name,
                    accepted=False,
                    status="busy",
                    reason=reason,
                )
            session_task_id = self._reserve_task_id(task_id)
            session = NavigationSession(
                node=self.node,
                publisher=self.publisher,
                defaults=self.defaults,
                point=point,
                listener=listener,
                mode=self.mode,
                republish_period_sec=self.republish_period_sec,
                progress_period_sec=self.progress_period_sec,
                timeout_sec=timeout_sec or self.defaults.timeout_sec,
                allow_initial_arrival_hysteresis=allow_initial_arrival_hysteresis,
                on_finished=self._clear_session,
                task_id=session_task_id,
            )
            self._active_session = session
            return session.start()

    def reserve_task_id(self) -> int:
        """Reserve a monotonic ID so an owner can bind callbacks before start()."""

        with self._lock:
            return self._allocate_task_id_locked()

    def _reserve_task_id(self, requested: int | None) -> int:
        with self._lock:
            if requested is None:
                return self._allocate_task_id_locked()
            try:
                task_id = int(requested)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid navigation task_id: {requested!r}") from exc
            if task_id <= 0:
                raise ValueError("navigation task_id must be positive")
            self._last_task_id = max(self._last_task_id, task_id)
            return task_id

    def _allocate_task_id_locked(self) -> int:
        candidate = int(time.time() * 1000)
        if candidate <= self._last_task_id:
            candidate = self._last_task_id + 1
        self._last_task_id = candidate
        return candidate

    def cancel(self, reason: str = "cancelled") -> bool:
        with self._lock:
            self._cancel_generation += 1
            pending = self._pending_starts > 0
            session = self._active_session
        if session is None or not session.is_active:
            return pending
        session.cancel(reason=reason)
        return True

    def get_status(self) -> dict[str, object]:
        with self._lock:
            session = self._active_session
            if session is None:
                status: dict[str, object] = {"status": "idle"}
            else:
                status = session.get_status()
            status["pnc_response"] = {
                "enabled": self.pnc_response_subscription is not None,
                "status": self._pnc_response_status,
                "topic": self.pnc_response_topic,
                "type": self.pnc_response_type,
                "publisher_count": self._pnc_response_publisher_count,
                "retry_count": self._pnc_response_retry_count,
                "retry_limit": self._pnc_response_max_retries,
                "last": dict(self._pnc_response_last),
                "error": self._pnc_response_error,
            }
            status["localization"] = self._localization_status_locked()
            status["command_channel"] = {
                "topic": self.command_topic,
                "subscriber_count": self._command_subscriber_count,
            }
            status["preflight"] = dict(self._last_preflight)
            return status

    def get_current_pose(self) -> dict[str, object] | None:
        with self._lock:
            if self._last_pose is None:
                return None
            return {
                **self._last_pose,
                "updated_at": self._last_pose_at,
                "age_sec": None if self._last_pose_at is None else round(time.time() - self._last_pose_at, 3),
            }

    def _localization_status_locked(self) -> dict[str, object]:
        if self._last_pose_at is None:
            return {
                "ok": False,
                "status": "no_data",
                "topic": self.odom_topic,
                "age_sec": None,
                "message": "暂未收到定位数据",
            }
        age_sec = max(0.0, time.time() - self._last_pose_at)
        fresh = age_sec <= self.odom_max_age_sec
        return {
            "ok": fresh,
            "status": "ok" if fresh else "stale",
            "topic": self.odom_topic,
            "age_sec": round(age_sec, 3),
            "max_age_sec": self.odom_max_age_sec,
            "message": "定位数据正常" if fresh else "定位数据已过期",
        }

    def _preflight_start(self) -> tuple[str, str] | None:
        localization = self._localization_status_locked()
        localization_warning = ""
        if not localization["ok"]:
            # A missing/stale odometry frame is a diagnosis, not proof that the
            # PNC command cannot be accepted. The session will keep publishing
            # and will complete once localization starts producing fresh data.
            localization_warning = (
                f"定位数据尚未就绪: topic={localization['topic']}, "
                f"status={localization['status']}, age_sec={localization['age_sec']}"
            )

        subscriber_count: int | None = None
        for attempt in range(self.start_preflight_retries + 1):
            subscriber_count = self._query_command_subscribers()
            if subscriber_count is None or subscriber_count > 0:
                status = "ready" if not localization_warning else "ready_localization_pending"
                reason = localization_warning
                with self._lock:
                    self._last_preflight = {
                        "ok": True,
                        "status": status,
                        "reason": reason,
                        "checked_at": time.time(),
                    }
                if localization_warning:
                    self.node.get_logger().warning(
                        f"{localization_warning}；已先下发导航，等待定位数据恢复"
                    )
                return None
            if attempt < self.start_preflight_retries and self.start_preflight_delay_sec > 0:
                time.sleep(self.start_preflight_delay_sec)
                with self._lock:
                    localization = self._localization_status_locked()
                if localization["ok"]:
                    localization_warning = ""

        reason = (
            f"PNC 指令通道没有订阅者: topic={self.command_topic}, "
            f"probe_attempts={self.start_preflight_retries + 1}; "
            "请确认底盘导航服务已启动"
        )
        with self._lock:
            self._last_preflight = {
                "ok": False,
                "status": "pnc_unavailable",
                "reason": reason,
                "checked_at": time.time(),
            }
        self.node.get_logger().warning(reason)
        return "pnc_unavailable", reason

    def _query_command_subscribers(self) -> int | None:
        query = getattr(self.node, "get_subscriptions_info_by_topic", None)
        if not callable(query):
            # Test doubles and older rclpy builds may not expose graph queries.
            # In that case the command channel remains an observable diagnostic,
            # but it cannot be used as a hard preflight gate.
            return None
        try:
            count = len(query(self.command_topic))
        except Exception as exc:
            self._command_subscriber_count = None
            self.node.get_logger().warning(
                f"PNC 指令通道订阅者查询失败，将继续使用可用定位数据: {exc}"
            )
            return None
        if count == 0:
            get_subscription_count = getattr(self.publisher, "get_subscription_count", None)
            if callable(get_subscription_count):
                try:
                    count = max(count, int(get_subscription_count()))
                except Exception:
                    pass
        self._command_subscriber_count = count
        return count

    def _handle_odom(self, msg: Odometry) -> None:
        pose = msg.pose.pose
        orientation = pose.orientation
        current_pose = {
            "position": {
                "x": float(pose.position.x),
                "y": float(pose.position.y),
                "z": float(pose.position.z),
            },
            "orientation": {
                "x": float(orientation.x),
                "y": float(orientation.y),
                "z": float(orientation.z),
                "w": float(orientation.w),
            },
            "yaw_rad": _yaw_from_quaternion(
                x=float(orientation.x),
                y=float(orientation.y),
                z=float(orientation.z),
                w=float(orientation.w),
            ),
        }
        current_pose["yaw_deg"] = float(current_pose["yaw_rad"]) * 180.0 / math.pi
        with self._lock:
            self._last_pose = current_pose
            self._last_pose_at = time.time()
            session = self._active_session
        if session is not None:
            session.handle_odom(msg)

    def _clear_session(self, session: "NavigationSession") -> None:
        with self._lock:
            if self._active_session is session:
                self._active_session = None

    def _setup_pnc_response_subscription(self) -> None:
        """Discover an optional PNC response publisher without blocking startup."""

        if self.pnc_response_subscription is not None or self._pnc_response_status == "unsupported":
            return
        try:
            graph_types = {
                name: types
                for name, types in self.node.get_topic_names_and_types()
            }
            candidates = graph_types.get(self.pnc_response_topic, [])
            configured_type = os.environ.get("NAV_PNC_RESPONSE_TYPE", "").strip()
            type_name = configured_type or (candidates[0] if candidates else "")
            if not type_name:
                self._defer_pnc_response_discovery(
                    "waiting_for_topic",
                    "尚未发现 PNC 回验话题，当前继续使用里程计到达判定",
                )
                return

            self.pnc_response_type = type_name
            publisher_query = getattr(self.node, "get_publishers_info_by_topic", None)
            if callable(publisher_query):
                publishers = publisher_query(self.pnc_response_topic)
                self._pnc_response_publisher_count = len(publishers)
                if not publishers:
                    self._defer_pnc_response_discovery(
                        "no_publisher",
                        "PNC 回验话题没有发布者，当前继续使用里程计到达判定",
                    )
                    return

            from rosidl_runtime_py.utilities import get_message

            message_type = get_message(type_name)
            response_qos = QoSProfile(
                depth=10,
                reliability=QoSReliabilityPolicy.BEST_EFFORT,
                durability=QoSDurabilityPolicy.VOLATILE,
            )
            self.pnc_response_subscription = self.node.create_subscription(
                message_type,
                self.pnc_response_topic,
                self._handle_pnc_response,
                qos_profile=response_qos,
            )
            self._pnc_response_status = "enabled"
            self._pnc_response_error = ""
            self._cancel_pnc_response_retry()
            self.node.get_logger().info(
                f"PNC 到达回验已启用：topic={self.pnc_response_topic}, type={type_name}"
            )
        except Exception as exc:
            if classify_pnc_response_support_error(exc) == "unsupported":
                self._pnc_response_status = "unsupported"
                self._pnc_response_error = (
                    f"当前 AIMDK 不支持 PNC 回验类型 {self.pnc_response_type or '<unknown>'}：{exc}；"
                    "已停止重试并继续使用里程计到达判定"
                )
                self._cancel_pnc_response_retry()
                self.node.get_logger().warning(self._pnc_response_error)
                return
            self._defer_pnc_response_discovery(
                "transient_error",
                f"PNC 回验暂时不可用：{exc}；当前继续使用里程计到达判定",
            )

    def _defer_pnc_response_discovery(self, status: str, message: str) -> None:
        self._pnc_response_status = status
        self._pnc_response_error = message
        notice_key = f"{status}:{message}"
        if notice_key != self._pnc_response_notice_key:
            self._pnc_response_notice_key = notice_key
            self.node.get_logger().info(message)
        self._schedule_pnc_response_retry()

    def _schedule_pnc_response_retry(self) -> None:
        if self._pnc_response_retry_timer is not None:
            return
        if self._pnc_response_max_retries <= 0:
            self._pnc_response_status = "unavailable"
            return
        self._pnc_response_retry_timer = self.node.create_timer(
            PNC_RESPONSE_RETRY_PERIOD_SEC,
            self._retry_pnc_response_subscription,
        )

    def _retry_pnc_response_subscription(self) -> None:
        if self.pnc_response_subscription is not None or self._pnc_response_status == "unsupported":
            self._cancel_pnc_response_retry()
            return
        retry_allowed, retry_count = claim_bounded_retry(
            self._pnc_response_retry_count,
            self._pnc_response_max_retries,
        )
        self._pnc_response_retry_count = retry_count
        if not retry_allowed:
            self._pnc_response_status = "unavailable"
            self._pnc_response_error = (
                f"PNC 回验在 {self._pnc_response_retry_count} 次有限探测后仍不可用；"
                "已停止重试并继续使用里程计到达判定，服务恢复后请重启导航 bridge"
            )
            self._cancel_pnc_response_retry()
            self.node.get_logger().warning(self._pnc_response_error)
            return
        self._setup_pnc_response_subscription()

    def _cancel_pnc_response_retry(self) -> None:
        if self._pnc_response_retry_timer is not None:
            self._pnc_response_retry_timer.cancel()
            self._pnc_response_retry_timer = None

    def _handle_pnc_response(self, message: Any) -> None:
        parsed = parse_pnc_task_response(message)
        parsed["received_at"] = time.time()
        with self._lock:
            self._pnc_response_last = parsed
            session = self._active_session
        if session is not None:
            session.handle_pnc_response(parsed)


def _yaw_from_quaternion(*, x: float, y: float, z: float, w: float) -> float:
    siny = 2.0 * (w * z + x * y)
    cosy = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny, cosy)


class NavigationSession:
    def __init__(
        self,
        *,
        node,
        publisher,
        defaults: NavigationDefaults,
        point: NavPoint,
        listener: NavigationEventListener | None,
        mode: str,
        republish_period_sec: float,
        progress_period_sec: float,
        timeout_sec: float,
        allow_initial_arrival_hysteresis: bool,
        on_finished,
        task_id: int | None = None,
    ):
        self.node = node
        self.publisher = publisher
        self.defaults = defaults
        self.point = point
        self.listener = listener
        self.mode = mode
        self.republish_period_sec = republish_period_sec
        self.progress_period_sec = progress_period_sec
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.allow_initial_arrival_hysteresis = bool(allow_initial_arrival_hysteresis)
        self.on_finished = on_finished
        self.task_id = int(task_id) if task_id is not None else int(time.time() * 1000)
        self._lock = threading.RLock()
        self._status = "created"
        self._current_msg: PncTaskRequest | None = None
        self._republish_timer = None
        self._timeout_timer = None
        self._arrival_policy = ArrivalPolicy(defaults.arrival_policy, point)
        self._near_goal_arrival = NearGoalArrival()
        self._arrived_region_emitted = False
        self._strict_region_entered_at: float | None = None
        self._arrival_stable_since: float | None = None
        self._stop_sent = False
        self._finish_started = False
        self._last_progress_at = 0.0
        self._last_sample: ArrivalSample | None = None
        self._last_pnc_response: dict[str, Any] | None = None

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._status not in {
                "completed",
                "failed",
                "timeout",
                "cancelled",
                "rejected",
            }

    def start(self) -> NavigationSessionHandle:
        with self._lock:
            self._status = "accepted"
        self._emit(NavigationEventType.ACCEPTED, status="accepted")

        if self.mode == "dry-run":
            self.node.get_logger().info(f"[DRY-RUN] navigation accepted: {self.point.name}")
            self._emit(NavigationEventType.STARTED, status="dry-run")
            self._emit(NavigationEventType.COMPLETED, status="dry-run")
            self._finish_without_stop("dry-run")
            return self._handle(accepted=True, status="dry-run")

        try:
            self._send_start()
        except Exception as exc:
            with self._lock:
                self._status = "failed"
            self._emit(NavigationEventType.FAILED, status="transport_error", reason=str(exc))
            self._finish_without_stop("failed")
            return self._handle(accepted=False, status="transport_error", reason=str(exc))

        with self._lock:
            self._status = "navigating"
        self._emit(NavigationEventType.STARTED, status="navigating")
        self._timeout_timer = self.node.create_timer(self.timeout_sec, self._on_timeout)
        return self._handle(accepted=True, status="started")

    def cancel(self, reason: str = "cancelled") -> None:
        if not self._begin_finish("cancelled"):
            return
        self._cancel_timers()
        if self.defaults.stop_policy.mode != "never":
            self._send_stop()
            # The PNC layer can still be consuming the previous START when a
            # new user goal arrives. Keep STOP alive briefly so the next START
            # cannot race the old session's shutdown.
            stop_hold_sec = max(
                0.0,
                min(3.0, float(self.defaults.stop_policy.hold_sec)),
            )
            if stop_hold_sec > 0.0:
                self.node.get_logger().info(
                    f"Navigation cancel stop hold: {stop_hold_sec:.1f}s, reason={reason}"
                )
                time.sleep(stop_hold_sec)
        self._emit(NavigationEventType.CANCELLED, status="cancelled", reason=reason)
        self._finish_without_stop("cancelled")

    def handle_odom(self, msg: Odometry) -> None:
        with self._lock:
            if self._status != "navigating":
                return
        sample = self._arrival_policy.update(msg)
        self._last_sample = sample
        self._emit_progress_if_due(sample)
        now = time.monotonic()

        if sample.in_region:
            if not self._arrived_region_emitted:
                self._arrived_region_emitted = True
                self._strict_region_entered_at = now
                self._emit(
                    NavigationEventType.ARRIVED_REGION,
                    status="arrived_region",
                    sample=sample,
                )
                if self.defaults.stop_policy.mode == "on_arrived_region":
                    self._complete_async("arrived_region", sample=sample)
                    return

        strict_region_age_sec = (
            None
            if self._strict_region_entered_at is None
            else now - self._strict_region_entered_at
        )
        post_entry_latch_active = is_post_entry_arrival_latch_active(
            self._arrived_region_emitted,
            strict_region_age_sec,
        )
        position_candidate = is_arrival_stability_candidate(
            sample.in_region,
            post_entry_latch_active,
            sample.distance_to_goal,
            self._arrival_policy.position_tolerance,
            allow_initial_hysteresis=self.allow_initial_arrival_hysteresis,
        )
        widened_post_entry_fallback = (
            not sample.in_region
            and post_entry_latch_active
            and is_within_arrival_hysteresis(
                sample.distance_to_goal,
                self._arrival_policy.position_tolerance,
                strict_region_entered=True,
            )
        )
        require_low_speed = (
            self.defaults.arrival_policy.require_low_speed
            or widened_post_entry_fallback
        )
        low_speed_threshold_mps = (
            self.defaults.arrival_policy.low_speed_threshold_mps
            if self.defaults.arrival_policy.require_low_speed
            else POST_ENTRY_LOW_SPEED_THRESHOLD_MPS
        )
        stability_candidate = is_arrival_sample_stable(
            position_candidate,
            sample.yaw_error,
            self.defaults.arrival_policy.yaw_tolerance_rad,
            sample.linear_speed_mps,
            require_low_speed=require_low_speed,
            low_speed_threshold_mps=low_speed_threshold_mps,
        )
        if stability_candidate:
            if self._arrival_stable_since is None:
                self._arrival_stable_since = now
        else:
            self._arrival_stable_since = None

        if sample.completed:
            self._complete_async("completed", sample=sample)
            return

        if (
            stability_candidate
            and self._arrival_stable_since is not None
            and (now - self._arrival_stable_since)
            >= self.defaults.arrival_policy.stable_duration_sec
        ):
            yaw_error_deg = (
                None if sample.yaw_error is None else math.degrees(sample.yaw_error)
            )
            self.node.get_logger().info(
                f"Navigation arrival fallback accepted: point={self.point.name}, "
                f"distance={sample.distance_to_goal:.3f}, "
                f"strict_tolerance={self._arrival_policy.position_tolerance:.3f}, "
                f"yaw_error_deg={yaw_error_deg}, "
                f"linear_speed_mps={sample.linear_speed_mps:.3f}, "
                f"strict_region_age_sec={strict_region_age_sec}"
            )
            self._complete_async("arrived_region_stable", sample=sample)
            return

        # Receipt time alone cannot distinguish live odometry from a replay.
        try:
            ros_now_sec = self.node.get_clock().now().nanoseconds / 1e9
        except (AttributeError, TypeError):
            ros_now_sec = float("nan")
        if self._near_goal_arrival.update(
            msg, sample, now=now, ros_now_sec=ros_now_sec,
            yaw_tolerance=self.defaults.arrival_policy.yaw_tolerance_rad,
            stop_enabled=self.defaults.stop_policy.mode != "never",
        ):
            self.node.get_logger().info(
                f"Navigation stable near-goal accepted: point={self.point.name}, "
                f"distance={sample.distance_to_goal:.3f}, radius=0.450, dwell_sec=5.0, "
                f"linear_speed_mps={sample.linear_speed_mps:.3f}"
            )
            self._complete_async("near_goal_stationary", sample=sample)

    def handle_pnc_response(self, response: dict[str, Any]) -> None:
        """Handle a terminal PNC response only when it matches this task."""

        response_task_id = response.get("task_id")
        if response_task_id is None:
            return
        try:
            response_task_id = int(response_task_id)
        except (TypeError, ValueError):
            self.node.get_logger().warning(
                f"Ignoring malformed PNC response task_id={response_task_id!r}"
            )
            return
        if response_task_id != self.task_id:
            return
        with self._lock:
            if self._status != "navigating":
                return
            self._last_pnc_response = dict(response)
        disposition = str(response.get("disposition", "unknown"))
        state_value = response.get("state_value")
        self.node.get_logger().info(
            f"PNC 回验：task_id={self.task_id}, state={state_value}, disposition={disposition}"
        )
        if disposition == "completed":
            self._complete_async("pnc_completed", sample=self._last_sample)
        elif disposition == "failed":
            self._fail_async(
                f"PNC 返回失败状态：{state_value or response.get('state_text') or 'unknown'}"
            )

    def get_status(self) -> dict[str, object]:
        with self._lock:
            return {
                "status": self._status,
                "task_id": self.task_id,
                "goal_id": self.point.id,
                "point_name": self.point.name,
                "stop_sent": self._stop_sent,
                "arrived_region": self._arrived_region_emitted,
                "strict_region_age_sec": (
                    None
                    if self._strict_region_entered_at is None
                    else max(0.0, time.monotonic() - self._strict_region_entered_at)
                ),
                "arrival_stable_elapsed_sec": (
                    None
                    if self._arrival_stable_since is None
                    else max(0.0, time.monotonic() - self._arrival_stable_since)
                ),
                "last_distance": (
                    None if self._last_sample is None else self._last_sample.distance_to_goal
                ),
                "last_linear_speed_mps": (
                    None if self._last_sample is None else self._last_sample.linear_speed_mps
                ),
                "last_yaw_error_rad": (
                    None if self._last_sample is None else self._last_sample.yaw_error
                ),
                "pnc_response": self._last_pnc_response,
                "near_goal_arrival": {
                    "enabled": self._near_goal_arrival.enabled,
                    "radius_m": self._near_goal_arrival.RADIUS_M,
                    "dwell_sec": self._near_goal_arrival.DWELL_SEC,
                    "stable_samples": self._near_goal_arrival.samples,
                },
            }

    def _complete_async(self, reason: str, sample: ArrivalSample | None = None) -> None:
        if not self._begin_finish("completing"):
            return
        thread = threading.Thread(
            target=self._complete,
            args=(reason, sample),
            daemon=True,
        )
        thread.start()

    def _complete(self, reason: str, sample: ArrivalSample | None) -> None:
        self._cancel_timers()
        if self.defaults.stop_policy.mode != "never":
            self._send_stop(sample=sample)
            time.sleep(self.defaults.stop_policy.hold_sec)
        self._emit(
            NavigationEventType.COMPLETED,
            status="completed",
            reason=reason,
            sample=sample,
        )
        self._finish_without_stop("completed")

    def _on_timeout(self) -> None:
        if not self._begin_finish("timeout"):
            return
        self._cancel_timers()
        if self.defaults.stop_policy.mode != "never":
            self._send_stop(sample=self._last_sample)
        self._emit(
            NavigationEventType.TIMEOUT,
            status="timeout",
            reason=f"navigation timeout after {self.timeout_sec:.1f}s",
            sample=self._last_sample,
        )
        self._finish_without_stop("timeout")

    def _fail_async(self, reason: str) -> None:
        if not self._begin_finish("failed"):
            return
        thread = threading.Thread(
            target=self._fail,
            args=(reason,),
            daemon=True,
        )
        thread.start()

    def _fail(self, reason: str) -> None:
        self._cancel_timers()
        if self.defaults.stop_policy.mode != "never":
            self._send_stop(sample=self._last_sample)
        self._emit(
            NavigationEventType.FAILED,
            status="failed",
            reason=reason,
            sample=self._last_sample,
        )
        self._finish_without_stop("failed")

    def _send_start(self) -> None:
        msg = PncTaskRequest()
        msg.header = self._make_header()
        msg.task_type = self.point.navigation.task_type
        msg.task_request = TASK_REQUEST_START
        msg.pnc_mode = self.point.navigation.pnc_mode
        msg.max_forward_speed = self.point.navigation.max_forward_speed
        msg.task_id = self.task_id
        msg.map_id = self.point.map_id
        msg.target_pose_radius = self.point.navigation.target_pose_radius
        msg.reserve_info = [0] * 64
        msg.target_pose = self._make_pose()

        self.node.get_logger().info(
            "Navigation task started: "
            f"name={self.point.name}, task_id={self.task_id}, map_id={self.point.map_id}, "
            f"x={self.point.pose.position.x:.3f}, y={self.point.pose.position.y:.3f}"
        )
        self._start_publishing(msg)

    def _send_stop(self, sample: ArrivalSample | None = None) -> None:
        with self._lock:
            if self._stop_sent:
                return
            self._stop_sent = True

        msg = PncTaskRequest()
        msg.header = self._make_header()
        msg.task_type = self.point.navigation.task_type
        msg.task_request = TASK_REQUEST_STOP
        msg.pnc_mode = self.point.navigation.pnc_mode
        msg.max_forward_speed = self.point.navigation.max_forward_speed
        msg.task_id = self.task_id
        msg.map_id = self.point.map_id
        msg.target_pose_radius = 0.0
        msg.reserve_info = [0] * 64
        target = PoseStamped()
        target.header.stamp = self.node.get_clock().now().to_msg()
        target.header.frame_id = "map"
        target.pose.orientation.w = 1.0
        msg.target_pose = target

        self.node.get_logger().info(
            f"Navigation stop sent: task_id={self.task_id}, map_id={self.point.map_id}"
        )
        self._start_publishing(msg)
        self._emit(NavigationEventType.STOP_SENT, status="stop_sent", sample=sample)

    def _start_publishing(self, msg: PncTaskRequest) -> None:
        with self._lock:
            self._current_msg = msg
            self._cancel_republish_timer_locked()
            self.publisher.publish(self._current_msg)
            self._republish_timer = self.node.create_timer(
                self.republish_period_sec,
                self._republish_current_message,
            )

    def _republish_current_message(self) -> None:
        with self._lock:
            if self._current_msg is None:
                return
            self._refresh_stamps(self._current_msg)
            msg = self._current_msg
        self.publisher.publish(msg)

    def _cancel_timers(self) -> None:
        with self._lock:
            self._cancel_republish_timer_locked()
            if self._timeout_timer is not None:
                self._timeout_timer.cancel()
                self._timeout_timer = None
            self._current_msg = None

    def _cancel_republish_timer_locked(self) -> None:
        if self._republish_timer is not None:
            self._republish_timer.cancel()
            self._republish_timer = None

    def _make_header(self) -> MessageHeader:
        header = MessageHeader()
        now = self.node.get_clock().now().to_msg()
        header.stamp = now
        header.meas_stamp = now
        header.frame_id = "map"
        header.sequence = 0
        return header

    def _make_pose(self) -> PoseStamped:
        target = PoseStamped()
        target.header.stamp = self.node.get_clock().now().to_msg()
        target.header.frame_id = "map"
        target.pose.position.x = self.point.pose.position.x
        target.pose.position.y = self.point.pose.position.y
        target.pose.position.z = self.point.pose.position.z
        target.pose.orientation.x = self.point.pose.orientation.x
        target.pose.orientation.y = self.point.pose.orientation.y
        target.pose.orientation.z = self.point.pose.orientation.z
        target.pose.orientation.w = self.point.pose.orientation.w
        return target

    def _refresh_stamps(self, msg: PncTaskRequest) -> None:
        now = self.node.get_clock().now().to_msg()
        msg.header.stamp = now
        msg.header.meas_stamp = now
        msg.target_pose.header.stamp = now

    def _emit_progress_if_due(self, sample: ArrivalSample) -> None:
        now = time.time()
        if (now - self._last_progress_at) < self.progress_period_sec:
            return
        self._last_progress_at = now
        self._emit(NavigationEventType.PROGRESS, status="navigating", sample=sample)

    def _emit(
        self,
        event_type: NavigationEventType,
        *,
        status: str,
        reason: str = "",
        sample: ArrivalSample | None = None,
    ) -> None:
        event = NavigationEvent(
            type=event_type,
            task_id=self.task_id,
            goal_id=self.point.id,
            point_name=self.point.name,
            status=status,
            current_pose=None if sample is None else sample.current_pose,
            distance_to_goal=None if sample is None else sample.distance_to_goal,
            yaw_error=None if sample is None else sample.yaw_error,
            reason=reason,
        )
        emit_safely(self.listener, event, logger=self.node.get_logger())

    def _begin_finish(self, status: str) -> bool:
        with self._lock:
            if self._finish_started:
                return False
            self._finish_started = True
            self._status = status
            return True

    def _finish_without_stop(self, status: str) -> None:
        self._cancel_timers()
        with self._lock:
            self._status = status
        self.on_finished(self)

    def _handle(
        self,
        *,
        accepted: bool,
        status: str,
        reason: str = "",
    ) -> NavigationSessionHandle:
        return NavigationSessionHandle(
            task_id=self.task_id,
            goal_id=self.point.id,
            point_name=self.point.name,
            accepted=accepted,
            status=status,
            reason=reason,
        )
