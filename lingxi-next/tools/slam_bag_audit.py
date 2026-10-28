"""Read an existing rosbag without starting ROS nodes; emit aggregate input evidence."""
import argparse
import json
import math
from pathlib import Path

from slam_preflight import Audit


CLOUD = "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud"
IMU = "/aima/hal/sensor/lidar_chest_front/imu"
ODOMETRY = "/aima/mc/leg_odometry"
TYPES = {
    CLOUD: "sensor_msgs/msg/PointCloud2", IMU: "sensor_msgs/msg/Imu",
    ODOMETRY: "nav_msgs/msg/Odometry",
    "/tf": "tf2_msgs/msg/TFMessage", "/tf_static": "tf2_msgs/msg/TFMessage",
}


class RecordedInputs:
    def __init__(self):
        self.audit = Audit()
        self.counts = {topic: 0 for topic in TYPES}
        self.first_stamp = None
        self.last_stamp = None
        self.first_position = None
        self.last_position = None
        self.path_length = 0.0
        self.pose_samples = 0
        self.invalid_pose_samples = 0

    def observe(self, topic, message, timestamp_ns):
        if topic not in TYPES:
            return
        if type(timestamp_ns) is not int or timestamp_ns <= 0:
            raise ValueError("invalid_bag_timestamp")
        seconds = timestamp_ns / 1e9
        self.first_stamp = seconds if self.first_stamp is None else min(self.first_stamp, seconds)
        self.last_stamp = seconds if self.last_stamp is None else max(self.last_stamp, seconds)
        self.counts[topic] += 1
        clocks = {"arrival": seconds, "wall_time": seconds}
        if topic == CLOUD:
            self.audit.cloud(message, **clocks)
        elif topic == IMU:
            self.audit.imu(message, **clocks)
        elif topic == ODOMETRY:
            self.audit.odometry(message, **clocks)
            value = message.pose.pose.position
            position = (value.x, value.y, value.z)
            if not all(math.isfinite(item) for item in position):
                self.invalid_pose_samples += 1
                self.last_position = None
                return
            if self.first_position is None:
                self.first_position = position
            if self.last_position is not None:
                self.path_length += math.dist(self.last_position, position)
            self.last_position = position
            self.pose_samples += 1
        else:
            self.audit.transforms(message, static=topic == "/tf_static")

    def report(self):
        result = self.audit.report()
        result["scope"] = "offline_recorded_input_observation_not_slam"
        result["bag"] = {
            "messageCounts": self.counts.copy(),
            "missingTopics": [topic for topic, count in self.counts.items() if count == 0],
            "observedDurationSeconds": (
                self.last_stamp - self.first_stamp if self.first_stamp is not None else None
            ),
            "timestampsAreRecordedReceiveTimes": True,
            "containsRawCoordinates": False,
        }
        for stream in result["streams"].values():
            stream["recordedArrivalHz"] = stream.pop("arrivalHz")
            stream["absoluteBagStampToHeaderSeconds"] = stream.pop("absoluteWallClockOffsetSeconds")
        result["odometry"]["reportedPoseMotion"] = {
            "validPoseSamples": self.pose_samples,
            "invalidPoseSamples": self.invalid_pose_samples,
            "accumulatedPositionStepRawUnits": self.path_length,
            "firstToLastPositionDistanceRawUnits": (
                math.dist(self.first_position, self.last_position)
                if self.first_position is not None and self.last_position is not None else None
            ),
            "isGroundTruth": False, "physicalUnitValidated": False,
            "accumulatedStepsIncludeMeasurementNoise": True,
        }
        return result


def run(directory):
    root = Path(directory).resolve()
    if not root.is_dir() or not (root / "metadata.yaml").is_file():
        raise ValueError("existing_finalized_bag_required")
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message

    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(root), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions("cdr", "cdr"),
    )
    available = {topic.name: topic.type for topic in reader.get_all_topics_and_types()}
    for name, expected in TYPES.items():
        if name in available and available[name] != expected:
            raise ValueError("unexpected_recorded_topic_type")
    classes = {name: get_message(expected) for name, expected in TYPES.items() if name in available}
    evidence = RecordedInputs()
    while reader.has_next():
        topic, payload, timestamp = reader.read_next()
        if topic in classes:
            evidence.observe(topic, deserialize_message(payload, classes[topic]), timestamp)
    return evidence.report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bag", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.bag), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
