"""Observe standard ROS 2 sensor inputs without publishing or calling services.

Run in a sourced ROS 2 environment. Only aggregate metadata is printed; point
coordinates, robot poses, raw sensor records and credentials are never saved.
This is an input audit, not SLAM, calibration, or permission to move the robot.
"""
import argparse
import json
import math
import struct
import time


CLOUD_TOPIC = "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud"
IMU_TOPIC = "/aima/hal/sensor/lidar_chest_front/imu"
ODOM_TOPIC = "/aima/mc/leg_odometry"
FIELD_FORMATS = {
    1: "b", 2: "B", 3: "h", 4: "H", 5: "i", 6: "I", 7: "f", 8: "d",
}
TIME_FIELDS = ("timestamp", "time_stamp", "time", "t", "offset_time")
TIME_SCALES = {
    "seconds": 1.0, "milliseconds": 1e-3,
    "microseconds": 1e-6, "nanoseconds": 1e-9,
}


def finite_norm(values):
    if not all(math.isfinite(value) for value in values):
        return None
    return math.hypot(*values)


def time_hypotheses(values, header_seconds, expected_count):
    """Report plausible interpretations, not an automatically selected unit.

    Only small relative offsets are emitted. Wrong epoch/unit combinations are
    omitted instead of exposing raw absolute point timestamps in the report.
    These broad bounds cannot replace driver documentation or dynamic checks.
    """
    if len(values) != expected_count or len(set(values)) < 2:
        return {}
    if not math.isfinite(header_seconds) or header_seconds <= 0:
        return {}
    earliest, latest = min(values), max(values)
    matches = {}
    for unit, scale in TIME_SCALES.items():
        span = (latest - earliest) * scale
        if not math.isfinite(span) or not 0.001 <= span <= 1.0:
            continue
        relative_first, relative_last = earliest * scale, latest * scale
        if -0.25 <= relative_first <= 0.25 and relative_last <= 1.0:
            matches["relative_" + unit] = {
                "spanSeconds": span,
                "firstMinusHeaderSeconds": relative_first,
                "lastMinusHeaderSeconds": relative_last,
            }
        absolute_first = earliest * scale - header_seconds
        absolute_last = latest * scale - header_seconds
        if -0.25 <= absolute_first <= 0.25 and -0.25 <= absolute_last <= 1.0:
            matches["absolute_" + unit] = {
                "spanSeconds": span,
                "firstMinusHeaderSeconds": absolute_first,
                "lastMinusHeaderSeconds": absolute_last,
            }
    return matches


class Range:
    def __init__(self):
        self.count = 0
        self.minimum = None
        self.maximum = None

    def add(self, value):
        if value is None or not math.isfinite(value):
            return
        self.count += 1
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def report(self):
        return {"count": self.count, "min": self.minimum, "max": self.maximum}


class Stream:
    def __init__(self):
        self.messages = 0
        self.invalid_stamps = 0
        self.non_increasing_stamps = 0
        self.first_stamp = None
        self.last_stamp = None
        self.first_arrival = None
        self.last_arrival = None
        self.frames = set()
        self.header_gaps = Range()
        self.absolute_clock_offsets = Range()

    def observe(self, header, arrival=None, wall_time=None):
        arrival = time.monotonic() if arrival is None else arrival
        wall_time = time.time() if wall_time is None else wall_time
        self.messages += 1
        if self.first_arrival is None:
            self.first_arrival = arrival
        self.last_arrival = arrival
        self.frames.add(header.frame_id)
        stamp = header.stamp.sec + header.stamp.nanosec / 1e9
        if not math.isfinite(stamp) or stamp <= 0:
            self.invalid_stamps += 1
            return
        if self.first_stamp is None:
            self.first_stamp = stamp
        if self.last_stamp is not None:
            gap = stamp - self.last_stamp
            if gap <= 0:
                self.non_increasing_stamps += 1
            else:
                self.header_gaps.add(gap)
        self.last_stamp = stamp
        self.absolute_clock_offsets.add(abs(wall_time - stamp))

    def report(self):
        elapsed = (self.last_arrival or 0) - (self.first_arrival or 0)
        return {
            "messages": self.messages,
            "frames": sorted(self.frames),
            "arrivalHz": (self.messages - 1) / elapsed if elapsed > 0 else None,
            "invalidHeaderStamps": self.invalid_stamps,
            "nonIncreasingHeaderStamps": self.non_increasing_stamps,
            "headerGapSeconds": self.header_gaps.report(),
            "absoluteWallClockOffsetSeconds": self.absolute_clock_offsets.report(),
        }


def sample_cloud(message, budget=256):
    """Respect field types, endianness, organized rows and row padding."""
    width, height = message.width, message.height
    step, row_step = message.point_step, message.row_step
    if width <= 0 or height <= 0 or step <= 0 or row_step < width * step:
        raise ValueError("invalid_pointcloud_dimensions")
    if len(message.data) < row_step * height:
        raise ValueError("truncated_pointcloud")
    if not 2 <= budget <= 1024:
        raise ValueError("invalid_sample_budget")
    fields = {}
    endian = ">" if message.is_bigendian else "<"
    layout = []
    for field in message.fields:
        layout.append({
            "name": field.name, "offset": field.offset,
            "datatype": field.datatype, "count": field.count,
        })
        if field.name in fields:
            raise ValueError("duplicate_pointcloud_field")
        if field.datatype not in FIELD_FORMATS or field.count != 1:
            if field.name in ("x", "y", "z") + TIME_FIELDS:
                raise ValueError("unsupported_required_field")
            continue
        decoder = struct.Struct(endian + FIELD_FORMATS[field.datatype])
        if field.offset < 0 or field.offset + decoder.size > step:
            raise ValueError("pointcloud_field_out_of_bounds")
        fields[field.name] = (field.offset, decoder)
    if not all(name in fields for name in ("x", "y", "z")):
        raise ValueError("missing_xyz")
    total = width * height
    count = min(budget, total)
    time_values = {name: [] for name in TIME_FIELDS if name in fields}
    valid_xyz = 0
    for index in range(count):
        point = index * (total - 1) // max(1, count - 1)
        row, column = divmod(point, width)
        offset = row * row_step + column * step

        def read(name):
            field_offset, decoder = fields[name]
            return decoder.unpack_from(message.data, offset + field_offset)[0]

        if finite_norm([read(name) for name in ("x", "y", "z")]) is not None:
            valid_xyz += 1
        for name, values in time_values.items():
            value = read(name)
            if math.isfinite(value):
                values.append(value)
    times = {}
    stamp = message.header.stamp.sec + message.header.stamp.nanosec / 1e9
    for name, values in time_values.items():
        times[name] = {
            "finiteSamples": len(values),
            "distinctSamples": len(set(values)),
            "spanRawUnits": max(values) - min(values) if values else None,
            "units": "unverified",
            "hypotheses": time_hypotheses(values, stamp, count),
        }
    return {
        "points": total, "bytes": len(message.data), "pointStep": step,
        "height": height, "width": width, "rowStep": row_step,
        "bigEndian": bool(message.is_bigendian),
        "fields": layout, "sampled": count, "finiteXyz": valid_xyz,
        "pointTimes": times,
    }


def frame_path(edges, start, target):
    if not start or not target:
        return None
    pending = [(start, [start])]
    visited = {start}
    while pending:
        frame, path = pending.pop(0)
        if frame == target:
            return path
        for parent, child in sorted(edges):
            neighbor = child if parent == frame else parent if child == frame else None
            if neighbor is not None and neighbor not in visited:
                visited.add(neighbor)
                pending.append((neighbor, path + [neighbor]))
    return None


class Audit:
    def __init__(self, sample_budget=256):
        self.sample_budget = sample_budget
        self.streams = {name: Stream() for name in ("cloud", "imu", "odometry")}
        self.cloud_layout = None
        self.cloud_errors = {}
        self.cloud_sizes = Range()
        self.finite_xyz_ratios = Range()
        self.point_time_spans = {}
        self.varying_time_frames = {}
        self.point_time_hypotheses = {}
        self.imu_acceleration_norm = Range()
        self.imu_angular_velocity_norm = Range()
        self.imu_orientation_norm = Range()
        self.imu_orientation_unavailable = 0
        self.odometry_child_frames = set()
        self.odometry_linear_speed = Range()
        self.tf_edges = set()
        self.static_edges = set()
        self.invalid_transforms = 0

    def cloud(self, message, *, arrival=None, wall_time=None):
        self.streams["cloud"].observe(message.header, arrival, wall_time)
        try:
            sample = sample_cloud(message, self.sample_budget)
        except (ValueError, struct.error, OverflowError) as error:
            category = str(error) if isinstance(error, ValueError) else "invalid_cloud_buffer"
            self.cloud_errors[category] = self.cloud_errors.get(category, 0) + 1
            return
        self.cloud_layout = {key: value for key, value in sample.items() if key not in (
            "sampled", "finiteXyz", "pointTimes",
        )}
        self.cloud_sizes.add(sample["points"])
        self.finite_xyz_ratios.add(sample["finiteXyz"] / sample["sampled"])
        for name, value in sample["pointTimes"].items():
            self.point_time_spans.setdefault(name, Range()).add(value["spanRawUnits"])
            if value["distinctSamples"] > 1:
                self.varying_time_frames[name] = self.varying_time_frames.get(name, 0) + 1
            for interpretation, evidence in value["hypotheses"].items():
                aggregate = self.point_time_hypotheses.setdefault(name, {}).setdefault(
                    interpretation, {key: Range() for key in evidence}
                )
                for key, measurement in evidence.items():
                    aggregate[key].add(measurement)

    def imu(self, message, *, arrival=None, wall_time=None):
        self.streams["imu"].observe(message.header, arrival, wall_time)
        vector = message.linear_acceleration
        self.imu_acceleration_norm.add(finite_norm([vector.x, vector.y, vector.z]))
        vector = message.angular_velocity
        self.imu_angular_velocity_norm.add(finite_norm([vector.x, vector.y, vector.z]))
        value = message.orientation
        if message.orientation_covariance[0] == -1:
            self.imu_orientation_unavailable += 1
        else:
            self.imu_orientation_norm.add(finite_norm([value.x, value.y, value.z, value.w]))

    def odometry(self, message, *, arrival=None, wall_time=None):
        self.streams["odometry"].observe(message.header, arrival, wall_time)
        self.odometry_child_frames.add(message.child_frame_id)
        value = message.twist.twist.linear
        self.odometry_linear_speed.add(finite_norm([value.x, value.y, value.z]))

    def transforms(self, message, static=False):
        for transform in message.transforms:
            parent, child = transform.header.frame_id, transform.child_frame_id
            value = transform.transform.translation
            rotation = transform.transform.rotation
            norm = finite_norm([rotation.x, rotation.y, rotation.z, rotation.w])
            if (not parent or not child or parent == child
                    or finite_norm([value.x, value.y, value.z]) is None
                    or norm is None or abs(norm - 1) > 0.01):
                self.invalid_transforms += 1
                continue
            self.tf_edges.add((parent, child))
            if static:
                self.static_edges.add((parent, child))

    def report(self):
        cloud = sorted(self.streams["cloud"].frames)
        imu = sorted(self.streams["imu"].frames)
        paths = [{
            "cloudFrame": start, "imuFrame": target,
            "observedGraphPath": frame_path(self.tf_edges, start, target),
            "staticGraphPath": frame_path(self.static_edges, start, target),
        } for start in cloud for target in imu]
        return {
            "schemaVersion": 1,
            "scope": "read_only_input_observation",
            "validation": {
                "slamExecuted": False, "mappingValidated": False,
                "relocalizationValidated": False, "navigationValidated": False,
                "motionCommandsSent": 0,
                "calibrationAndTimeUnits": "unverified",
                "tfPathsAreNotTimestampedTransformValidation": True,
            },
            "streams": {name: stream.report() for name, stream in self.streams.items()},
            "cloud": {
                "lastLayout": self.cloud_layout,
                "errors": self.cloud_errors, "pointCount": self.cloud_sizes.report(),
                "sampledFiniteXyzRatio": self.finite_xyz_ratios.report(),
                "pointTimeSpansRawUnits": {
                    name: value.report() for name, value in self.point_time_spans.items()
                },
                "framesWithVaryingSampledPointTime": self.varying_time_frames,
                "pointTimeUnits": "unverified",
                "pointTimeInterpretationEvidence": {
                    name: {
                        interpretation: {key: value.report() for key, value in evidence.items()}
                        for interpretation, evidence in interpretations.items()
                    } for name, interpretations in self.point_time_hypotheses.items()
                },
                "timeInterpretationsAreHypothesesNotCalibration": True,
            },
            "imu": {
                "accelerationNorm": self.imu_acceleration_norm.report(),
                "angularVelocityNorm": self.imu_angular_velocity_norm.report(),
                "orientationNorm": self.imu_orientation_norm.report(),
                "orientationUnavailableMessages": self.imu_orientation_unavailable,
            },
            "odometry": {
                "childFrames": sorted(self.odometry_child_frames),
                "linearSpeedNorm": self.odometry_linear_speed.report(),
            },
            "tf": {
                "edges": [list(edge) for edge in sorted(self.tf_edges)],
                "staticEdges": [list(edge) for edge in sorted(self.static_edges)],
                "invalidTransforms": self.invalid_transforms, "sensorFramePaths": paths,
            },
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--cloud-topic", default=CLOUD_TOPIC)
    parser.add_argument("--imu-topic", default=IMU_TOPIC)
    parser.add_argument("--odom-topic", default=ODOM_TOPIC)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 3 <= args.seconds <= 30:
        parser.error("--seconds must be finite and between 3 and 30")

    import rclpy
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import Imu, PointCloud2
    from tf2_msgs.msg import TFMessage
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

    rclpy.init(args=[])
    node = rclpy.create_node(
        "lingxi_independent_slam_preflight", enable_rosout=False,
        start_parameter_services=False,
    )
    audit = Audit()
    qos = QoSProfile(
        depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
        durability=DurabilityPolicy.VOLATILE,
    )
    static_qos = QoSProfile(
        depth=100, reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
    )
    topics = {"cloud": args.cloud_topic, "imu": args.imu_topic, "odometry": args.odom_topic}
    subscriptions = []
    try:
        for message_type, topic, callback in (
            (PointCloud2, args.cloud_topic, audit.cloud),
            (Imu, args.imu_topic, audit.imu),
            (Odometry, args.odom_topic, audit.odometry),
            (TFMessage, "/tf", audit.transforms),
        ):
            subscriptions.append(node.create_subscription(message_type, topic, callback, qos))
        subscriptions.append(node.create_subscription(
            TFMessage, "/tf_static", lambda message: audit.transforms(message, static=True),
            static_qos,
        ))
        started = time.monotonic()
        while time.monotonic() - started < args.seconds:
            rclpy.spin_once(node, timeout_sec=0.05)
        report = audit.report()
        report["durationSeconds"] = time.monotonic() - started
        report["topics"] = topics
        report["publishers"] = {name: node.count_publishers(topic) for name, topic in topics.items()}
        print(json.dumps(report, allow_nan=False))
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
