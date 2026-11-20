"""Check recorded X2 PointCloud2/IMU input against the pinned FAST-LIO layout."""
import argparse
import json
import math
from pathlib import Path
import sqlite3
import struct


BASE = "/aima/hal/sensor/lidar_chest_front"
CLOUD = f"{BASE}/lidar_pointcloud"
IMU = f"{BASE}/imu"
FIELDS = {"x": (0, 7), "y": (4, 7), "z": (8, 7), "intensity": (16, 7),
          "ring": (20, 4), "timestamp": (24, 8)}


def check_cloud(message):
    fields = {field.name: (field.offset, field.datatype) for field in message.fields if field.count == 1}
    if (message.header.frame_id != "lidar_chest_front" or message.is_bigendian or message.point_step != 32
            or message.width < 2 or message.height != 1 or message.row_step != message.width * message.point_step
            or len(message.data) != message.row_step or any(fields.get(key) != val for key, val in FIELDS.items())):
        raise ValueError(f"unexpected_x2_cloud_layout: {fields}, step={message.point_step}")
    offsets = [values[5] * 1e-9 for values in struct.iter_unpack("<fff4xfH2xd", message.data)
               if all(map(math.isfinite, values[:3]))]
    if not offsets or min(offsets) < 0 or not all(map(math.isfinite, offsets)) or not 0.03 < max(offsets) < 0.2:
        raise ValueError("x2_cloud_point_time_not_relative_nanoseconds")
    return max(offsets)


def check(database):
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Imu, PointCloud2

    with sqlite3.connect(database.resolve(strict=True).as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        layout = connection.execute("SELECT id, name, type, serialization_format FROM topics WHERE name IN (?, ?)",
                                    (CLOUD, IMU)).fetchall()
        topics = {name: (identity, type_name, format_) for identity, name, type_name, format_ in layout}
        result = {}
        for topic, message_type in ((CLOUD, PointCloud2), (IMU, Imu)):
            identity, type_name, format_ = topics[topic]
            if type_name != f"sensor_msgs/msg/{message_type.__name__}" or format_ != "cdr":
                raise ValueError("unexpected_sensor_type_or_serialization")
            stamps, offsets = [], []
            for payload, in connection.execute("SELECT data FROM messages WHERE topic_id=? ORDER BY timestamp LIMIT ?",
                                               (identity, 10 if topic == CLOUD else 256)):
                message = deserialize_message(payload, message_type)
                header = message.header.stamp
                stamps.append(header.sec + header.nanosec / 1e9)
                if topic == CLOUD:
                    offsets.append(check_cloud(message))
                elif not all(math.isfinite(getattr(value, axis))
                             for value in (message.linear_acceleration, message.angular_velocity) for axis in "xyz"):
                    raise ValueError("non_finite_x2_imu")
            gaps = [right - left for left, right in zip(stamps, stamps[1:])]
            if len(gaps) < 2 or min(gaps) <= 0:
                raise ValueError("insufficient_monotonic_sensor_samples")
            result[message_type.__name__] = {"samples": len(stamps), "maxHeaderGapSeconds": max(gaps),
                                           **({"maxPointOffsetSeconds": max(offsets)} if offsets else {})}
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    print(json.dumps(check(parser.parse_args().database), allow_nan=False))
