from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import rclpy
from geometry_msgs.msg import Pose
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy
from std_msgs.msg import String

try:
    from aimdk.protocol.mm import relocalization_pb2
    from ros2_plugin_proto.srv import RosRpcWrapper
except ImportError:  # pragma: no cover - only used on robots without AimDK protocol packages.
    relocalization_pb2 = None
    RosRpcWrapper = None


class RelocalizeOnceNode(Node):
    """Publish one AimDK relocalization command and wait for odometry."""

    def __init__(
        self,
        *,
        map_id: int,
        pose: Pose,
        pose_delay_sec: float,
        timeout_sec: float,
        result_json: str | None,
        expected_world_x: float | None,
        expected_world_y: float | None,
        verify_tolerance_m: float,
        subscriber_wait_sec: float,
    ):
        super().__init__("relocalize_once")
        self.map_id = int(map_id)
        self.pose = pose
        self.pose_delay_sec = max(0.0, float(pose_delay_sec))
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.result_json = Path(result_json).expanduser() if result_json else None
        self.expected_world_x = expected_world_x
        self.expected_world_y = expected_world_y
        self.verify_tolerance_m = max(0.0, float(verify_tolerance_m))
        self.subscriber_wait_sec = max(0.0, float(subscriber_wait_sec))
        self.success_received = False
        self.done = False
        self.started_at = time.monotonic()
        self._pose_timer = None
        self._timeout_timer = None
        self._last_rejected_odom: dict[str, object] | None = None
        self._official_started = False

        command_qos = QoSProfile(
            depth=10,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            reliability=QoSReliabilityPolicy.RELIABLE,
        )
        pose_qos = QoSProfile(
            depth=10,
            durability=QoSDurabilityPolicy.VOLATILE,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
        )
        odom_qos = QoSProfile(depth=10, reliability=QoSReliabilityPolicy.BEST_EFFORT)
        self.integrated_command_pub = self.create_publisher(
            String,
            "/integrated_command",
            command_qos,
        )
        self.relocalization_pose_pub = self.create_publisher(
            Pose,
            "/relocalization_pose",
            pose_qos,
        )
        self._official_start_client = None
        self._official_stop_client = None
        if RosRpcWrapper is not None and relocalization_pb2 is not None:
            try:
                self._official_start_client = self.create_client(
                    RosRpcWrapper,
                    "/aimdk_2Eprotocol_2ERelocalizationService/StartNormalRelocalization",
                )
                self._official_stop_client = self.create_client(
                    RosRpcWrapper,
                    "/aimdk_2Eprotocol_2ERelocalizationService/StopNormalRelocalization",
                )
            except Exception as exc:
                self.get_logger().warning(
                    "Official RelocalizationService client is unavailable; "
                    f"falling back to compatibility topics: {exc}"
                )
                self._official_start_client = None
                self._official_stop_client = None
        self.odom_topic = os.environ.get("NAV_ODOM_TOPIC", "/slam/lidar_odom").strip() or "/slam/lidar_odom"
        self.create_subscription(Odometry, self.odom_topic, self._on_odom, odom_qos)
        self._publish_timer = self.create_timer(0.2, self._wait_and_publish)
        self._write_result("running", "等待重定位通道就绪")

    def _wait_and_publish(self) -> None:
        if self._official_start_client is not None and self._official_stop_client is not None:
            if not self._official_start_client.service_is_ready():
                elapsed = time.monotonic() - self.started_at
                if elapsed < self.subscriber_wait_sec:
                    self.get_logger().info(
                        "Waiting for official RelocalizationService...",
                        throttle_duration_sec=2.0,
                    )
                    return
                if not self._official_start_client.wait_for_service(timeout_sec=0.1):
                    self.get_logger().warning("Official RelocalizationService unavailable; falling back to topics")
                else:
                    self._call_official_start()
                    return
            else:
                self._call_official_start()
                return

        command_subscribers = self.integrated_command_pub.get_subscription_count()
        pose_subscribers = self.relocalization_pose_pub.get_subscription_count()
        elapsed = time.monotonic() - self.started_at
        if (command_subscribers == 0 or pose_subscribers == 0) and elapsed < self.subscriber_wait_sec:
            self.get_logger().info(
                "Waiting briefly for relocalization subscribers... "
                f"(command={command_subscribers}, pose={pose_subscribers})",
                throttle_duration_sec=2.0,
            )
            return
        if command_subscribers == 0 or pose_subscribers == 0:
            self.get_logger().info(
                "Relocalization subscribers are not visible through rclpy; publishing anyway. "
                f"(command={command_subscribers}, pose={pose_subscribers})"
            )

        if self._publish_timer is not None:
            self._publish_timer.cancel()
        command = String()
        command.data = f"start_relocalization:{self.map_id}"
        self.integrated_command_pub.publish(command)
        self.get_logger().info(f"Published integrated_command: {command.data}")
        self._write_result(
            "running",
            f"已发送重定位命令: {command.data}",
            {
                "integrated_command_subscriptions": command_subscribers,
                "relocalization_pose_subscriptions": pose_subscribers,
                "subscriber_wait_sec": self.subscriber_wait_sec,
            },
        )
        self._pose_timer = self.create_timer(self.pose_delay_sec, self._publish_pose)

    def _call_official_start(self) -> None:
        if self._official_started:
            return
        self._official_started = True
        if self._publish_timer is not None:
            self._publish_timer.cancel()
            self._publish_timer = None
        req_pb = relocalization_pb2.RelocalizationStartNormalReq(
            command=relocalization_pb2.RelocalizationCommand_NORMAL_START,
            map_id=self.map_id,
        )
        req = RosRpcWrapper.Request(
            serialization_type="pb",
            context=[],
            data=_bytes_seq(req_pb.SerializeToString()),
        )
        self.get_logger().info(f"Calling official StartNormalRelocalization: map_id={self.map_id}")
        self._write_result("running", "正在调用官方重定位服务")
        future = self._official_start_client.call_async(req)
        future.add_done_callback(self._on_official_start_done)

    def _on_official_start_done(self, future) -> None:
        try:
            response = future.result()
            rsp_pb = relocalization_pb2.RelocalizationStartNormalRsp()
            rsp_pb.ParseFromString(_join_data(response.data))
            code = int(rsp_pb.header.code)
            message = rsp_pb.header.msg
            if code != 0 and "already running" not in message.lower():
                self.get_logger().warning(f"StartNormalRelocalization returned code={code}, msg={message}")
                self._write_result("running", f"官方重定位服务返回: {message or code}")
            else:
                self.get_logger().info(f"StartNormalRelocalization accepted: code={code}, msg={message}")
                self._write_result("running", "官方重定位已启动，准备保存位姿")
        except Exception as exc:
            self.get_logger().error(f"Official StartNormalRelocalization failed: {exc}")
            self._write_result("running", f"官方重定位启动失败，改用兼容话题: {exc}")
            self._publish_legacy_topics()
            return
        self._pose_timer = self.create_timer(self.pose_delay_sec, self._save_official_pose)

    def _save_official_pose(self) -> None:
        if self._pose_timer is not None:
            self._pose_timer.cancel()
            self._pose_timer = None
        req_pb = relocalization_pb2.RelocalizationStopNormalReq()
        req_pb.command = relocalization_pb2.RelocalizationCommand_NORMAL_SAVE
        req_pb.map_id = self.map_id
        req_pb.pose.position.u = int(round(self.pose.position.x))
        req_pb.pose.position.v = int(round(self.pose.position.y))
        req_pb.pose.angle = _yaw_from_quaternion(
            self.pose.orientation.x,
            self.pose.orientation.y,
            self.pose.orientation.z,
            self.pose.orientation.w,
        )
        req = RosRpcWrapper.Request(
            serialization_type="pb",
            context=[],
            data=_bytes_seq(req_pb.SerializeToString()),
        )
        self.get_logger().info(
            "Calling official StopNormalRelocalization SAVE: "
            f"map_id={self.map_id}, u={req_pb.pose.position.u}, v={req_pb.pose.position.v}, "
            f"angle={req_pb.pose.angle}"
        )
        self._write_result("running", "已提交官方重定位位姿，等待定位数据")
        future = self._official_stop_client.call_async(req)
        future.add_done_callback(self._on_official_stop_done)

    def _on_official_stop_done(self, future) -> None:
        try:
            response = future.result()
            rsp_pb = relocalization_pb2.RelocalizationStopNormalRsp()
            rsp_pb.ParseFromString(_join_data(response.data))
            code = int(rsp_pb.header.code)
            message = rsp_pb.header.msg
            if code != 0:
                self.get_logger().warning(f"StopNormalRelocalization returned code={code}, msg={message}")
                self._write_result("running", f"官方保存位姿返回: {message or code}")
            else:
                self.get_logger().info("Official relocalization pose saved")
                self._write_result("running", "官方重定位位姿已保存，等待定位回验")
        except Exception as exc:
            self.get_logger().error(f"Official StopNormalRelocalization failed: {exc}")
            self._write_result("running", f"官方保存位姿失败: {exc}")
        self._timeout_timer = self.create_timer(self.timeout_sec, self._on_timeout)

    def _publish_legacy_topics(self) -> None:
        command = String()
        command.data = f"start_relocalization:{self.map_id}"
        self.integrated_command_pub.publish(command)
        self.get_logger().info(f"Published integrated_command: {command.data}")
        self._write_result("running", f"已发送重定位命令: {command.data}")
        self._pose_timer = self.create_timer(self.pose_delay_sec, self._publish_pose)

    def _publish_pose(self) -> None:
        if self._pose_timer is not None:
            self._pose_timer.cancel()
            self._pose_timer = None
        self.relocalization_pose_pub.publish(self.pose)
        self.get_logger().info(
            "Published relocalization_pose: "
            f"x={self.pose.position.x}, y={self.pose.position.y}, z={self.pose.position.z}, "
            f"qx={self.pose.orientation.x}, qy={self.pose.orientation.y}, "
            f"qz={self.pose.orientation.z}, qw={self.pose.orientation.w}"
        )
        self._write_result("running", "已发送重定位位姿，等待定位数据")
        self._timeout_timer = self.create_timer(self.timeout_sec, self._on_timeout)

    def _on_odom(self, msg: Odometry) -> None:
        if self.done or self._timeout_timer is None:
            return
        odom_x = float(msg.pose.pose.position.x)
        odom_y = float(msg.pose.pose.position.y)
        if self.expected_world_x is not None and self.expected_world_y is not None:
            distance_m = math.hypot(odom_x - self.expected_world_x, odom_y - self.expected_world_y)
            if distance_m > self.verify_tolerance_m:
                self._last_rejected_odom = {
                    "odom_world_x": odom_x,
                    "odom_world_y": odom_y,
                    "expected_world_x": self.expected_world_x,
                    "expected_world_y": self.expected_world_y,
                    "distance_m": distance_m,
                    "verify_tolerance_m": self.verify_tolerance_m,
                }
                self.get_logger().info(
                    "Ignoring odometry outside relocalization tolerance: "
                    f"odom_x={odom_x:.3f}, odom_y={odom_y:.3f}, "
                    f"expected_x={self.expected_world_x:.3f}, expected_y={self.expected_world_y:.3f}, "
                    f"distance={distance_m:.3f}, tolerance={self.verify_tolerance_m:.3f}",
                    throttle_duration_sec=2.0,
                )
                return
            details = {
                "odom_world_x": odom_x,
                "odom_world_y": odom_y,
                "expected_world_x": self.expected_world_x,
                "expected_world_y": self.expected_world_y,
                "distance_m": distance_m,
                "verify_tolerance_m": self.verify_tolerance_m,
            }
            message = f"重定位成功，定位回验通过，误差 {distance_m:.3f}m"
        else:
            details = {"odom_world_x": odom_x, "odom_world_y": odom_y, "verified": False}
            message = "重定位成功，已收到定位数据（未启用位置回验）"
        self.success_received = True
        self.done = True
        self._timeout_timer.cancel()
        self.get_logger().info("Received verified odometry data after relocalization")
        self._write_result("success", message, details)

    def _on_timeout(self) -> None:
        if self.done:
            return
        self.done = True
        self.get_logger().error(f"Relocalization timed out after {self.timeout_sec:.1f}s")
        details = self._last_rejected_odom or {}
        if not details:
            details = {
                "slam_lidar_odom_data": False,
                "odom_topic": self.odom_topic,
                "advice": f"底层定位没有输出可读取的 {self.odom_topic} 数据，请先恢复定位数据流再重定位",
            }
        self._write_result(
            "timeout",
            f"重定位命令已发送，但 {self.timeout_sec:.1f}s 内未收到通过回验的定位数据",
            details,
        )

    def _write_result(self, status: str, message: str, details: dict[str, object] | None = None) -> None:
        if self.result_json is None:
            return
        payload = {
            "status": status,
            "message": message,
            "map_id": self.map_id,
            "pixel_x": self.pose.position.x,
            "pixel_y": self.pose.position.y,
            "updated_at": time.time(),
        }
        if details:
            payload.update(details)
        self.result_json.parent.mkdir(parents=True, exist_ok=True)
        self.result_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _bytes_seq(payload: bytes) -> list[bytes]:
    return [bytes([item]) for item in payload]


def _join_data(items) -> bytes:
    return b"".join(items)


def _yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one relocalization command")
    parser.add_argument("--map-id", type=int, required=True)
    parser.add_argument("--x", type=float, required=True)
    parser.add_argument("--y", type=float, required=True)
    parser.add_argument("--z", type=float, default=0.0)
    parser.add_argument("--qx", type=float, default=0.0)
    parser.add_argument("--qy", type=float, default=0.0)
    parser.add_argument("--qz", type=float, default=0.0)
    parser.add_argument("--qw", type=float, default=1.0)
    parser.add_argument("--pose-delay-sec", type=float, default=1.0)
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--result-json", help="Path to write relocalization result JSON")
    parser.add_argument("--expected-world-x", type=float)
    parser.add_argument("--expected-world-y", type=float)
    parser.add_argument("--verify-tolerance-m", type=float, default=2.0)
    parser.add_argument("--subscriber-wait-sec", type=float, default=3.0)
    return parser.parse_args()


def main(args=None) -> int:
    cli_args = parse_args()
    pose = Pose()
    pose.position.x = cli_args.x
    pose.position.y = cli_args.y
    pose.position.z = cli_args.z
    pose.orientation.x = cli_args.qx
    pose.orientation.y = cli_args.qy
    pose.orientation.z = cli_args.qz
    pose.orientation.w = cli_args.qw

    rclpy.init(args=args)
    node = RelocalizeOnceNode(
        map_id=cli_args.map_id,
        pose=pose,
        pose_delay_sec=cli_args.pose_delay_sec,
        timeout_sec=cli_args.timeout_sec,
        result_json=cli_args.result_json,
        expected_world_x=cli_args.expected_world_x,
        expected_world_y=cli_args.expected_world_y,
        verify_tolerance_m=cli_args.verify_tolerance_m,
        subscriber_wait_sec=cli_args.subscriber_wait_sec,
    )
    exit_code = 0
    try:
        while rclpy.ok() and not node.done:
            rclpy.spin_once(node, timeout_sec=0.1)
        if not node.success_received:
            exit_code = 1
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
