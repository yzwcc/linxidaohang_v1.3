from __future__ import annotations

import threading
import time
from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Any

from aimdk_msgs.msg import MessageHeader, PncTaskRequest
from geometry_msgs.msg import Pose2D, PoseStamped
from nav_msgs.msg import Odometry
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

from robot_local_navigation_v3.arrival_policy import ArrivalPolicy, ArrivalSample
from robot_local_navigation_v3.blocked_navigation import BlockedNavigationDetector
from robot_local_navigation_v3.arrival_hysteresis import (
    is_arrival_stability_candidate,
    is_within_arrival_hysteresis,
    is_yaw_within_tolerance,
)
from robot_local_navigation_v3.models import NavPoint, NavigationDefaults
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
from robot_local_navigation_v3.path_clearance import PathClearanceError, plan_reference_line


TASK_REQUEST_START = 1
TASK_REQUEST_STOP = 2
PNC_RESPONSE_TOPIC = "/aima/pnc/pnc_task_response"
PNC_RESPONSE_RETRY_PERIOD_SEC = 2.0
PNC_RESPONSE_MAX_RETRIES = 15


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
        map_root: str | None = None,
        republish_period_sec: float = 1.0,
        progress_period_sec: float = 1.0,
    ):
        if mode not in {"execute", "dry-run"}:
            raise ValueError("mode must be 'execute' or 'dry-run'")
        self.node = node
        self.defaults = defaults
        self.mode = mode
        self.map_root = map_root or os.environ.get(
            "ROBOT_LOCAL_NAV_MAP_ROOT",
            "/agibot/data/var/MapManagerModule/",
        )
        self.republish_period_sec = max(0.1, float(republish_period_sec))
        self.progress_period_sec = max(0.1, float(progress_period_sec))
        self._lock = threading.RLock()
        self._active_session: NavigationSession | None = None
        self._planning_goal = False
        self._last_pose: dict[str, object] | None = None
        self._last_pose_at: float | None = None
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
    ) -> NavigationSessionHandle:
        with self._lock:
            if self._planning_goal or (
                self._active_session is not None and self._active_session.is_active
            ):
                session = self._active_session
                reason = (
                    "navigation is preparing a safe route"
                    if self._planning_goal
                    else f"navigation is busy with task_id={session.task_id}"
                )
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
            self._planning_goal = True

        try:
            if self.mode == "dry-run" or not self._requires_global_path_safety(point):
                safe_reference_line: list[tuple[float, float]] = []
            else:
                safe_reference_line = self.plan_safe_reference_line(point)
                self._verify_planning_pose_still_valid(safe_reference_line[0])

            with self._lock:
                if self._active_session is not None and self._active_session.is_active:
                    reason = f"navigation is busy with task_id={self._active_session.task_id}"
                    return NavigationSessionHandle(
                        task_id=None,
                        goal_id=point.id,
                        point_name=point.name,
                        accepted=False,
                        status="busy",
                        reason=reason,
                    )
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
                    safe_reference_line=safe_reference_line,
                    on_finished=self._clear_session,
                )
                self._active_session = session
            return session.start()
        except (PathClearanceError, OSError, ValueError) as exc:
            reason = str(exc)
            emit_safely(
                listener,
                NavigationEvent(
                    type=NavigationEventType.REJECTED,
                    task_id=None,
                    goal_id=point.id,
                    point_name=point.name,
                    status="path_safety_rejected",
                    reason=reason,
                ),
                logger=self.node.get_logger(),
            )
            self.node.get_logger().warning(
                f"Navigation rejected by global path safety: point={point.name}, reason={reason}"
            )
            return NavigationSessionHandle(
                task_id=None,
                goal_id=point.id,
                point_name=point.name,
                accepted=False,
                status="path_safety_rejected",
                reason=reason,
            )
        finally:
            with self._lock:
                self._planning_goal = False

    def plan_safe_reference_line(self, point: NavPoint) -> list[tuple[float, float]]:
        with self._lock:
            pose = self._last_pose
            updated_at = self._last_pose_at
        safety = self.defaults.path_safety
        if safety.robot_radius_m is None:
            raise PathClearanceError("尚未配置机器人实测外接半径，已拒绝下发导航")
        if pose is None or updated_at is None:
            raise PathClearanceError("尚未收到 /slam/lidar_odom 定位，已拒绝下发导航")
        age = time.time() - updated_at
        if age > safety.max_odom_age_sec:
            raise PathClearanceError(
                f"/slam/lidar_odom 定位已过期 {age:.2f}s（上限 {safety.max_odom_age_sec:.2f}s），已拒绝下发导航"
            )
        position = pose["position"]
        try:
            start_x = float(position["x"])
            start_y = float(position["y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PathClearanceError("当前定位数据无效，已拒绝下发导航") from exc
        route = plan_reference_line(
            map_dir=Path(self.map_root).expanduser() / str(point.map_id),
            start_x=start_x,
            start_y=start_y,
            goal_x=float(point.pose.position.x),
            goal_y=float(point.pose.position.y),
            config=safety,
        )
        self.node.get_logger().info(
            "Global path safety applied: "
            f"point={point.name}, robot_radius_m={safety.robot_radius_m:.3f}, "
            f"extra_clearance_m={safety.extra_clearance_m:.3f}, "
            f"static_inflation_m={safety.robot_radius_m + safety.extra_clearance_m:.3f}, "
            f"reference_points={len(route)}"
        )
        return route

    @staticmethod
    def _requires_global_path_safety(point: NavPoint) -> bool:
        return point.navigation.task_type == 2 and point.navigation.pnc_mode == 0

    def _verify_planning_pose_still_valid(self, planned_start: tuple[float, float]) -> None:
        with self._lock:
            pose = self._last_pose
            updated_at = self._last_pose_at
        if pose is None or updated_at is None:
            raise PathClearanceError("规划期间定位数据丢失，已拒绝下发导航")
        age = time.time() - updated_at
        limit = self.defaults.path_safety.max_odom_age_sec
        if age > limit:
            raise PathClearanceError(
                f"规划完成时 /slam/lidar_odom 已过期 {age:.2f}s（上限 {limit:.2f}s），已拒绝下发导航"
            )
        position = pose["position"]
        drift = math.hypot(
            float(position["x"]) - planned_start[0],
            float(position["y"]) - planned_start[1],
        )
        if drift > self.defaults.path_safety.max_start_drift_m:
            raise PathClearanceError(
                f"规划期间机器人已移动 {drift:.2f}m（上限 "
                f"{self.defaults.path_safety.max_start_drift_m:.2f}m），请重新下发导航"
            )

    def cancel(self, reason: str = "cancelled") -> bool:
        with self._lock:
            session = self._active_session
        if session is None or not session.is_active:
            return False
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
        safe_reference_line: list[tuple[float, float]],
        on_finished,
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
        self.safe_reference_line = list(safe_reference_line)
        self.on_finished = on_finished
        self.task_id = int(time.time() * 1000)
        self._lock = threading.RLock()
        self._status = "created"
        self._current_msg: PncTaskRequest | None = None
        self._republish_timer = None
        self._timeout_timer = None
        self._arrival_policy = ArrivalPolicy(defaults.arrival_policy, point)
        self._arrived_region_emitted = False
        self._arrived_region_at: float | None = None
        self._stop_sent = False
        self._finish_started = False
        self._last_progress_at = 0.0
        self._last_sample: ArrivalSample | None = None
        self._last_pnc_response: dict[str, Any] | None = None
        self._blocked_detector = BlockedNavigationDetector(
            reminder_interval_sec=5.0,
        )

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
            self._emit(NavigationEventType.FAILED, status="failed", reason=str(exc))
            self._finish_without_stop("failed")
            return self._handle(accepted=False, status="failed", reason=str(exc))

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
        linear = msg.twist.twist.linear
        angular = msg.twist.twist.angular
        blocked_notice = self._blocked_detector.update(
            current_pose=sample.current_pose,
            distance_to_goal=sample.distance_to_goal,
            arrival_tolerance_m=self._arrival_policy.position_tolerance,
            linear_speed_mps=math.hypot(float(linear.x), float(linear.y)),
            angular_speed_radps=float(angular.z),
        )
        if blocked_notice is not None:
            self._emit(
                NavigationEventType.BLOCKED,
                status="blocked",
                reason=blocked_notice.reason,
                sample=sample,
            )
        self._emit_progress_if_due(sample)

        if sample.in_region:
            if not self._arrived_region_emitted:
                self._arrived_region_emitted = True
                self._emit(
                    NavigationEventType.ARRIVED_REGION,
                    status="arrived_region",
                    sample=sample,
                )
                if self.defaults.stop_policy.mode == "on_arrived_region":
                    self._complete_async("arrived_region", sample=sample)
                    return

        hysteresis_enabled = (
            self._arrived_region_emitted or self.allow_initial_arrival_hysteresis
        )
        within_arrival_hysteresis = (
            hysteresis_enabled
            and is_within_arrival_hysteresis(
                sample.distance_to_goal,
                self._arrival_policy.position_tolerance,
            )
        )
        stability_candidate = is_arrival_stability_candidate(
            sample.in_region,
            self._arrived_region_emitted,
            sample.distance_to_goal,
            self._arrival_policy.position_tolerance,
            allow_initial_hysteresis=self.allow_initial_arrival_hysteresis,
        )
        if stability_candidate:
            if self._arrived_region_at is None:
                self._arrived_region_at = time.monotonic()
        else:
            self._arrived_region_at = None

        if sample.completed:
            self._complete_async("completed", sample=sample)
            return

        # Permit small braking drift after entering the strict position region,
        # but never use that fallback to bypass the configured final heading.
        yaw_within_tolerance = is_yaw_within_tolerance(
            sample.yaw_error,
            self.defaults.arrival_policy.yaw_tolerance_rad,
        )
        if (
            (sample.in_region or within_arrival_hysteresis)
            and yaw_within_tolerance
            and self._arrived_region_at is not None
            and (time.monotonic() - self._arrived_region_at)
            >= self.defaults.arrival_policy.stable_duration_sec
        ):
            yaw_error_deg = (
                None if sample.yaw_error is None else math.degrees(sample.yaw_error)
            )
            self.node.get_logger().info(
                f"Navigation arrival fallback accepted: point={self.point.name}, "
                f"distance={sample.distance_to_goal:.3f}, "
                f"strict_tolerance={self._arrival_policy.position_tolerance:.3f}, "
                f"yaw_error_deg={yaw_error_deg}"
            )
            self._complete_async("arrived_region_stable", sample=sample)

    def handle_pnc_response(self, response: dict[str, Any]) -> None:
        """Handle a terminal PNC response only when it matches this task."""

        response_task_id = response.get("task_id")
        if response_task_id is None or int(response_task_id) != self.task_id:
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
                "last_distance": (
                    None if self._last_sample is None else self._last_sample.distance_to_goal
                ),
                "last_yaw_error_rad": (
                    None if self._last_sample is None else self._last_sample.yaw_error
                ),
                "pnc_response": self._last_pnc_response,
                "blocked": self._blocked_detector.status(),
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
        reference_line = []
        if NavigationClient._requires_global_path_safety(self.point):
            if not self.safe_reference_line:
                raise PathClearanceError("安全参考线为空，已拒绝下发导航")
            if not hasattr(msg, "reference_line"):
                raise PathClearanceError("当前 AIMDK 的 PncTaskRequest 不支持 reference_line，已拒绝下发导航")
            for x, y in self.safe_reference_line:
                pose = Pose2D()
                pose.x = float(x)
                pose.y = float(y)
                pose.theta = 0.0
                reference_line.append(pose)
            msg.reference_line = reference_line

        self.node.get_logger().info(
            "Navigation task started: "
            f"name={self.point.name}, task_id={self.task_id}, map_id={self.point.map_id}, "
            f"x={self.point.pose.position.x:.3f}, y={self.point.pose.position.y:.3f}, "
            f"safe_reference_points={len(reference_line)}"
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
