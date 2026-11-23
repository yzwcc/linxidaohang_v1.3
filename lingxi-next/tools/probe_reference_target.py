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

PARENT = Path(__file__).resolve().parents[1]
BRIDGE = PARENT / "bridge"
sys.path.insert(0, str(BRIDGE if BRIDGE.is_dir() else PARENT / "robot-runtime" / "python"))
from reference_calibration import (IMU_FRAME, LIDAR_FRAME, MAX_RESIDUAL, check_target_mounting,
                                   check_target_mounting_document, valid_rigid)


MC_STATE = "/aima/mc/common/state"
TOPIC_TYPES = {CLOUD: "sensor_msgs/msg/PointCloud2", IMU: "sensor_msgs/msg/Imu",
               MC_STATE: "aimdk_msgs/msg/McCommonState"}
SERVICE_TYPES = {f"/aimdk_5Fmsgs/srv/{name}": f"aimdk_msgs/srv/{name}"
                 for name in ("GetMcAction", "GetCurrentInputSource", "SetMcInputSource")}
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


def bounded_yaml(path):
    import yaml

    with path.open("rb") as stream:
        payload = stream.read(128 * 1024 + 1)
    if len(payload) > 128 * 1024:
        raise ValueError("target_yaml_too_large")
    try:
        return yaml.safe_load(payload)
    except yaml.YAMLError:
        raise ValueError("invalid_target_yaml") from None


def read_factory_lidar(path):
    """The factory base-to-lidar entry cannot supply the internal IMU transform."""
    import numpy as np

    section = bounded_yaml(path)["chest_lidar"]
    rotation, translation = section["R_baselink_sensor"], section["t_baselink_sensor"]
    if (not isinstance(rotation, list) or len(rotation) != 9 or not isinstance(translation, list)
            or len(translation) != 3 or any(type(value) not in (int, float) for value in rotation + translation)):
        raise ValueError("invalid_factory_chest_lidar")
    matrix = np.eye(4)
    matrix[:3, :3] = np.array(rotation, dtype=float).reshape(3, 3)
    matrix[:3, 3] = translation
    if not valid_rigid(matrix):
        raise ValueError("invalid_factory_chest_lidar")
    return matrix


def candidate_profile(template, lidar_from_imu):
    import numpy as np

    if not valid_rigid(lidar_from_imu):
        raise ValueError("invalid_static_lidar_imu_edge")
    imu_from_lidar = np.linalg.inv(lidar_from_imu)
    parameters = template["/**"]["ros__parameters"]
    # Pinned FAST-LIO debug logs otherwise try to write under its compiled source path.
    parameters["runtime_pos_log_enable"] = False
    mapping = parameters["mapping"]
    mapping.update(extrinsic_est_en=False, extrinsic_R=imu_from_lidar[:3, :3].reshape(-1).tolist(),
                   extrinsic_T=imu_from_lidar[:3, 3].tolist())
    return template


def observe(profile, seconds, *, factory_path=None, output=None):
    import numpy as np
    import yaml
    from scipy.spatial.transform import Rotation
    import rclpy
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
    from rclpy.time import Time
    from sensor_msgs.msg import Imu, PointCloud2
    from tf2_ros import Buffer, TransformException, TransformListener
    from tf2_msgs.msg import TFMessage
    from aimdk_msgs.msg import McCommonState

    factory = read_factory_lidar(factory_path) if factory_path else None
    streams = {topic: Stream() for topic in TOPIC_TYPES}
    cloud_samples = []
    static_edge = None
    invalid_static_edge = False

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

        def matrix_from_tf(transform):
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

        def observe_static(message):
            nonlocal static_edge, invalid_static_edge
            for sample in message.transforms:
                if (sample.header.frame_id, sample.child_frame_id) != (LIDAR_FRAME, IMU_FRAME):
                    continue
                try:
                    measured = matrix_from_tf(sample.transform)
                    if static_edge is not None and np.max(np.abs(static_edge - measured)) > MAX_RESIDUAL:
                        invalid_static_edge = True
                    static_edge = measured
                except ValueError:
                    invalid_static_edge = True

        if output:
            node.create_subscription(TFMessage, "/tf_static", observe_static, QoSProfile(
                depth=100, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE))

        def lookup(target, source, stamp):
            try:
                transform = buffer.lookup_transform(target, source, Time(nanoseconds=stamp)).transform
            except TransformException:
                return None
            return matrix_from_tf(transform)

        def usable_lookup(*arguments):
            try:
                return lookup(*arguments)
            except ValueError:
                return None

        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            rclpy.spin_once(node, timeout_sec=min(.1, remaining))
        now_ns = time.time_ns()
        candidate = None
        if not output:
            status = check_target_mounting(profile, lookup)
        elif invalid_static_edge or static_edge is None:
            status = "invalid_static_tf" if invalid_static_edge else "missing_static_tf"
        else:
            candidate = candidate_profile(bounded_yaml(profile), static_edge)
            status = check_target_mounting_document(candidate, lookup)
        result = build_report(architecture=platform.machine(), distro=os.environ.get("ROS_DISTRO"), streams=streams,
                              clouds=cloud_samples, topics=dict(node.get_topic_names_and_types()),
                              services=dict(node.get_service_names_and_types()), calibration=status,
                              lookup=usable_lookup, now_ns=now_ns)
        if factory is not None:
            result["factoryChassisLidarReadable"] = True
            result["factoryBaseLidarProvidesLidarImuExtrinsic"] = False
        if output and result["ok"]:
            payload = yaml.safe_dump(candidate, sort_keys=False)
            # Never overwrite factory inputs, active config or a prior candidate.
            with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
                stream.write(payload)
            result["wroteCandidateFastLioConfig"] = True
        return result
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fastlio-config", type=Path, required=True)
    parser.add_argument("--factory-calibration", type=Path)
    parser.add_argument("--write-fastlio-config", type=Path, help="Write an isolated, new candidate from live /tf_static")
    parser.add_argument("--seconds", type=float, default=6.)
    args = parser.parse_args()
    paths = (args.fastlio_config, args.factory_calibration, args.write_fastlio_config)
    if any(path is not None and not path.is_absolute() for path in paths) or not 4 <= args.seconds <= 20:
        parser.error("an absolute --fastlio-config and 4–20 observation seconds are required")
    if args.write_fastlio_config and (not args.factory_calibration or args.write_fastlio_config.exists()):
        parser.error("candidate generation requires --factory-calibration and a new output path")
    try:
        result = observe(args.fastlio_config, args.seconds, factory_path=args.factory_calibration,
                         output=args.write_fastlio_config)
    except (AttributeError, KeyError, OSError, TypeError, ValueError, OverflowError):
        # Do not print private YAML input, field values or tracebacks with excerpts.
        print(json.dumps({"schemaVersion": 1, "ok": False, "error": "invalid_target_calibration_or_config"}))
        return 1
    print(json.dumps(result, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
