from __future__ import annotations

import threading
import time
from dataclasses import dataclass
import math

from aimdk_msgs.msg import MessageHeader, PncTaskRequest
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

from robot_local_navigation_v3.arrival_policy import ArrivalPolicy, ArrivalSample
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


TASK_REQUEST_START = 1
TASK_REQUEST_STOP = 2


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
        self._lock = threading.RLock()
        self._active_session: NavigationSession | None = None
        self._last_pose: dict[str, object] | None = None
        self._last_pose_at: float | None = None

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

    def start(
        self,
        point: NavPoint,
        listener: NavigationEventListener | None = None,
        *,
        timeout_sec: float | None = None,
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
                on_finished=self._clear_session,
            )
            self._active_session = session
            return session.start()

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
                return {"status": "idle"}
            return session.get_status()

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
        self._emit(NavigationEventType.CANCELLED, status="cancelled", reason=reason)
        self._finish_without_stop("cancelled")

    def handle_odom(self, msg: Odometry) -> None:
        with self._lock:
            if self._status != "navigating":
                return
        sample = self._arrival_policy.update(msg)
        self._last_sample = sample
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

        within_arrival_hysteresis = (
            self._arrived_region_emitted
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
