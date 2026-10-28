"""Immutable, bounded snapshots of explicitly profiled standard sensor messages."""
from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class SensorProfile:
    cloud_frame: str
    imu_frame: str
    point_time_encoding: str

    def __post_init__(self):
        if any(type(value) is not str or not value or len(value) > 128
               or any(character.isspace() for character in value)
               for value in (self.cloud_frame, self.imu_frame)):
            raise ValueError("explicit_sensor_frames_required")
        if self.point_time_encoding != "frame_relative_nanoseconds_hypothesis":
            raise ValueError("explicit_relative_nanosecond_hypothesis_required")


def sensor_stamp(message, frame):
    if message.header.frame_id != frame:
        raise ValueError("sensor_frame_mismatch")
    stamp = message.header.stamp
    if (type(stamp.sec) is not int or type(stamp.nanosec) is not int
            or stamp.sec < 0 or not 0 <= stamp.nanosec < 10**9):
        raise ValueError("invalid_sensor_stamp")
    result = stamp.sec * 10**9 + stamp.nanosec
    if result <= 0:
        raise ValueError("invalid_sensor_stamp")
    return result


@dataclass(frozen=True)
class ImuPacket:
    stamp_ns: int
    acceleration: tuple
    angular: tuple
    payload_bytes: int = 56


@dataclass(frozen=True)
class CloudPacket:
    stamp_ns: int
    width: int
    height: int
    point_step: int
    row_step: int
    big_endian: bool
    offsets: tuple
    data: bytes

    @property
    def payload_bytes(self):
        return len(self.data)


def imu_snapshot(message, profile):
    stamp = sensor_stamp(message, profile.imu_frame)
    values = []
    for vector, covariance in (
        (message.linear_acceleration, message.linear_acceleration_covariance),
        (message.angular_velocity, message.angular_velocity_covariance),
    ):
        if len(covariance) != 9 or covariance[0] == -1:
            raise ValueError("available_acceleration_and_angular_velocity_required")
        triple = tuple(float(value) for value in (vector.x, vector.y, vector.z))
        if not all(math.isfinite(value) for value in triple):
            raise ValueError("finite_imu_vector_required")
        values.append(triple)
    return ImuPacket(stamp, *values)


def cloud_snapshot(message, profile, maximum_points, maximum_bytes):
    stamp = sensor_stamp(message, profile.cloud_frame)
    width, height, step, row = message.width, message.height, message.point_step, message.row_step
    if (any(type(value) is not int or value <= 0 for value in (width, height, step, row))
            or not 10 <= width * height <= maximum_points or row < width * step
            or height * row > maximum_bytes or type(message.is_bigendian) is not bool):
        raise ValueError("bounded_cloud_dimensions_required")
    payload = memoryview(message.data)
    if payload.nbytes != height * row or not payload.c_contiguous:
        raise ValueError("complete_contiguous_cloud_payload_required")
    if not 4 <= len(message.fields) <= 32:
        raise ValueError("bounded_cloud_schema_required")
    fields = {}
    for field in message.fields:
        if (type(field.name) is not str or not field.name or len(field.name) > 64
                or field.name in fields):
            raise ValueError("unique_bounded_cloud_fields_required")
        fields[field.name] = field
    offsets = []
    for name, datatype, size in (("x", 7, 4), ("y", 7, 4), ("z", 7, 4), ("timestamp", 8, 8)):
        field = fields.get(name)
        if (field is None or field.datatype != datatype or field.count != 1
                or type(field.offset) is not int or not 0 <= field.offset <= step - size):
            raise ValueError("explicit_xyz_and_float64_timestamp_required")
        offsets.append(field.offset)
    intervals = sorted((offset, offset + size) for offset, size in zip(offsets, (4, 4, 4, 8)))
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
        raise ValueError("nonoverlapping_required_cloud_fields_required")
    return CloudPacket(stamp, width, height, step, row, message.is_bigendian, tuple(offsets),
                       payload.tobytes())


def decode_cloud(packet):
    """Decode every point, preserving row padding and time pairing; never crop."""
    order = ">" if packet.big_endian else "<"
    dtype = np.dtype({
        "names": ["x", "y", "z", "timestamp"],
        "formats": [order + "f4"] * 3 + [order + "f8"],
        "offsets": packet.offsets, "itemsize": packet.point_step,
    })
    structured = np.ndarray(
        shape=(packet.height, packet.width), dtype=dtype, buffer=packet.data,
        strides=(packet.row_step, packet.point_step),
    )
    points = np.column_stack([structured[name].ravel() for name in ("x", "y", "z")]).astype(np.float64)
    times = structured["timestamp"].ravel().astype(np.float64) * 1e-9
    if (not np.isfinite(points).all() or not np.isfinite(times).all()
            or times.min() < 0 or times.max() > .2 or np.ptp(times) <= 0):
        raise ValueError("complete_finite_cloud_and_relative_time_required")
    return np.ascontiguousarray(points), np.ascontiguousarray(times)
