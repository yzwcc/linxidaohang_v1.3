#!/usr/bin/env python3
"""Observe the target ROS graph before installing the X2 reference navigation service."""

import argparse
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

from check_reference_bag import CLOUD, IMU, check_cloud


MC_STATE = "/aima/mc/common/state"
TOPIC_TYPES = {CLOUD: "sensor_msgs/msg/PointCloud2", IMU: "sensor_msgs/msg/Imu",
               MC_STATE: "aimdk_msgs/msg/McCommonState"}
SERVICE_TYPES = {f"/aimdk_5Fmsgs/srv/{name}": f"aimdk_msgs/srv/{name}"
                 for name in ("GetMcAction", "GetCurrentInputSource", "SetMcInputSource")}
IMU_FRAME = "lidar_imu_chest_front"
BASE_FRAMES = (IMU_FRAME, "left_ankle_roll_link", "right_ankle_roll_link")
NS_PER_S = 10**9


@dataclass
class Stream:
    count: int = 0
    invalid: int = 0
    clock_skew: int = 0
    non_monotonic: int = 0
    max_gap: int = 0
    last_stamp: int = 0
    recent: list = field(default_factory=list)

    def observe(self, message, now_ns, valid=True):
        self.count += 1
        stamp = message.header.stamp
        if stamp.sec <= 0 or not 0 <= stamp.nanosec < NS_PER_S:
            self.invalid += 1
            return
        ns = stamp.sec * NS_PER_S + stamp.nanosec
        if not valid:
            self.invalid += 1
        if not -50_000_000 <= now_ns - ns <= 350_000_000:
            self.clock_skew += 1
        if self.last_stamp:
            gap = ns - self.last_stamp
            if gap <= 0:
                self.non_monotonic += 1
            else:
                self.max_gap = max(self.max_gap, gap)
        self.last_stamp = ns
        self.recent = (self.recent + [ns])[-12:]

    def report(self, *, now_ns, min_count, max_gap_ns):
        fresh = bool(self.last_stamp and -50_000_000 <= now_ns - self.last_stamp <= 350_000_000)
        return {"samples": self.count, "invalid": self.invalid, "clockSkew": self.clock_skew,
                "outOfOrder": self.non_monotonic, "maxHeaderGapMs": self.max_gap / 1e6, "fresh": fresh,
                "ok": self.count >= min_count and fresh and not (self.invalid or self.clock_skew or self.non_monotonic)
                and self.max_gap <= max_gap_ns}


def imu_valid(message):
    return message.header.frame_id == IMU_FRAME and all(
        math.isfinite(getattr(vector, axis))
        for vector in (message.linear_acceleration, message.angular_velocity) for axis in "xyz")


def mc_state_valid(message):
    return (isinstance(getattr(getattr(message, "input_source", None), "name", None), str)
            and type(getattr(getattr(message, "fsm_state", None), "current_state", None)) is int)


def build_report(*, architecture, distro, streams, clouds, topics, services, calibration, lookup, now_ns):
    streams_report = {name: stream.report(now_ns=now_ns, min_count=8 if name == IMU else 3,
                                          max_gap_ns=50_000_000 if name == IMU else 350_000_000)
                      for name, stream in streams.items()}
    checked = 0
    max_point_offset = 0.
    for message in clouds:
        try:
            max_point_offset = max(max_point_offset, check_cloud(message))
            checked += 1
        except ValueError:
            continue
    cloud_layout = len(clouds) >= 3 and checked == len(clouds)
    # Use identical cloud timestamps for IMU and both feet; latest-only TF can conceal lag.
    window = streams[CLOUD].recent
    synchronized = sum(all(lookup("base_link", source, stamp) is not None for source in BASE_FRAMES)
                       for stamp in window if 0 <= now_ns - stamp <= 750_000_000)
    topic_match = {name: set(topics.get(name, ())) == {kind} for name, kind in TOPIC_TYPES.items()}
    service_match = {name: set(services.get(name, ())) == {kind} for name, kind in SERVICE_TYPES.items()}
    target_platform = architecture in {"aarch64", "arm64"} and distro == "humble"
    ok = (target_platform and cloud_layout and synchronized >= 3 and calibration == "matched"
          and all(item["ok"] for item in streams_report.values()) and all(topic_match.values())
          and all(service_match.values()))
    return {"schemaVersion": 1, "ok": ok, "platform": {"architecture": architecture, "rosDistro": distro,
                                                      "ok": target_platform},
            "streams": streams_report, "cloud": {"checked": checked, "captured": len(clouds),
                                                  "maxPointOffsetMs": max_point_offset * 1000, "ok": cloud_layout},
            "topics": topic_match, "aimdkServiceTypes": service_match, "calibration": calibration,
            "sameStampBodyAndFeetTfSamples": synchronized, "motionValidated": False}


def observe(profile, seconds):
    import numpy as np
    from scipy.spatial.transform import Rotation
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from rclpy.time import Time
    from sensor_msgs.msg import Imu, PointCloud2
    from tf2_ros import Buffer, TransformException, TransformListener
    from aimdk_msgs.msg import McCommonState

    parent = Path(__file__).resolve().parents[1]
    # Sibling module of the reference service, whether run from checkout or release.
    bridge = parent / "bridge"
    sys.path.insert(0, str(bridge if bridge.is_dir() else parent / "robot-runtime" / "python"))
    from reference_calibration import check_target_mounting, valid_rigid

    streams = {topic: Stream() for topic in TOPIC_TYPES}
    cloud_samples = []

    def cloud(message):
        streams[CLOUD].observe(message, time.time_ns())
        if len(cloud_samples) < 3:
            cloud_samples.append(message)

    rclpy.init(args=[])
    node = None
    try:
        node = rclpy.create_node(f"lingxi_target_probe_{os.getpid()}", enable_rosout=False, start_parameter_services=False)
        buffer = Buffer()
        listener = TransformListener(buffer, node, spin_thread=False)
        node.create_subscription(PointCloud2, CLOUD, cloud, qos_profile_sensor_data)
        node.create_subscription(Imu, IMU, lambda msg: streams[IMU].observe(msg, time.time_ns(), imu_valid(msg)),
                                 qos_profile_sensor_data)
        node.create_subscription(McCommonState, MC_STATE,
                                 lambda msg: streams[MC_STATE].observe(msg, time.time_ns(), mc_state_valid(msg)),
                                 qos_profile_sensor_data)

        def lookup(target, source, stamp):
            try:
                transform = buffer.lookup_transform(target, source, Time(nanoseconds=stamp)).transform
            except TransformException:
                return None
            q, t = transform.rotation, transform.translation
            quaternion = np.array([q.x, q.y, q.z, q.w])
            if not np.isfinite(quaternion).all() or abs(float(quaternion @ quaternion) - 1) > .01:
                raise ValueError("invalid_target_tf")
            matrix = np.eye(4)
            matrix[:3, :3] = Rotation.from_quat(quaternion).as_matrix()
            matrix[:3, 3] = (t.x, t.y, t.z)
            if not valid_rigid(matrix):
                raise ValueError("invalid_target_tf")
            return matrix

        def usable_lookup(*arguments):
            try:
                return lookup(*arguments)
            except ValueError:
                return None

        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            rclpy.spin_once(node, timeout_sec=min(.1, remaining))
        now_ns = time.time_ns()
        return build_report(architecture=platform.machine(), distro=os.environ.get("ROS_DISTRO"), streams=streams,
                            clouds=cloud_samples, topics=dict(node.get_topic_names_and_types()),
                            services=dict(node.get_service_names_and_types()), calibration=check_target_mounting(profile, lookup),
                            lookup=usable_lookup, now_ns=now_ns)
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fastlio-config", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=6.)
    args = parser.parse_args()
    if not args.fastlio_config.is_absolute() or not 4 <= args.seconds <= 20:
        parser.error("an absolute --fastlio-config and 4–20 observation seconds are required")
    result = observe(args.fastlio_config, args.seconds)
    print(json.dumps(result, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
