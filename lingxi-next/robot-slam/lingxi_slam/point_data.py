"""Bounded point-data records, separate from health IPC and robot controls."""
from dataclasses import dataclass
import math
import struct
import threading
import time

import numpy as np

from .point_output import PointPacket
from .process_protocol import session_id


HEADER = struct.Struct("<8sHBBIIIQdd16s")
MAGIC = b"LXSPOINT"
MODES = {"live": 1, "recorded": 2, "synthetic": 3}
MAXIMUM_POINTS = 8000
MAXIMUM_BYTES = 192 + MAXIMUM_POINTS * 24
MAXIMUM_FRAME_ID = 2**53 - 1


def mode_id(value):
    if type(value) is not str or value not in MODES:
        raise ValueError("explicit_point_input_mode_required")
    return MODES[value]


def arrays(packet):
    xyz, pose = packet.points, packet.world_lidar_pose
    if (not isinstance(xyz, np.ndarray) or xyz.dtype != np.float64 or xyz.ndim != 2
            or xyz.shape[1] != 3 or not 1 <= len(xyz) <= MAXIMUM_POINTS
            or not isinstance(pose, np.ndarray) or pose.dtype != np.float64 or pose.shape != (4, 4)
            or not np.isfinite(xyz).all() or not np.isfinite(pose).all()
            or not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-7, rtol=0)
            or np.linalg.norm(pose[:3, :3].T @ pose[:3, :3] - np.eye(3), ord="fro") > 1e-5
            or not np.isclose(np.linalg.det(pose[:3, :3]), 1., atol=1e-5, rtol=0)
            or type(packet.native_frame_id) is not int or not 1 <= packet.native_frame_id <= MAXIMUM_FRAME_ID
            or type(packet.native_point_count) is not int
            or not len(xyz) <= packet.native_point_count <= 2_000_000
            or type(packet.algorithm_stamp) not in (float, int) or not math.isfinite(packet.algorithm_stamp)
            or packet.algorithm_stamp <= 0 or not math.isfinite(packet.algorithm_stamp * 1000)
            or packet.coordinate_frame != "lingxi_glim_odom"
            or packet.source_frame != "IMU" or packet.kind != "native_deskewed_corrected_odometry"):
        raise ValueError("bounded_finite_corrected_point_record_required")
    return xyz, pose


@dataclass(frozen=True)
class DecodedPointRecord:
    session: str
    input_mode: str
    emitted_at_seconds: float
    packet: PointPacket


class PointEncoder:
    """One native owner; transport may drop encoded records, never mix sessions."""

    def __init__(self, session, input_mode, *, clock=time.monotonic):
        self.session, self.mode = session_id(session), mode_id(input_mode)
        if not callable(clock):
            raise ValueError("point_encoder_clock_required")
        self.clock, self.owner = clock, threading.get_ident()
        self.previous_id, self.previous_stamp, self.previous_emission = 0, 0., 0.

    def encode(self, packet):
        if threading.get_ident() != self.owner:
            raise RuntimeError("same_native_point_encoder_owner_required")
        xyz, pose = arrays(packet)
        emitted = self.clock()
        if (packet.native_frame_id != self.previous_id + 1 or packet.algorithm_stamp <= self.previous_stamp
                or type(emitted) not in (float, int) or not math.isfinite(emitted)
                or emitted < 0 or emitted < self.previous_emission):
            raise ValueError("ordered_native_point_emission_required")
        header = HEADER.pack(
            MAGIC, 1, self.mode, 0, len(xyz), packet.native_point_count, 0,
            packet.native_frame_id, packet.algorithm_stamp, emitted, bytes.fromhex(self.session))
        payload = header + pose.astype("<f8", copy=False).tobytes() + xyz.astype("<f8", copy=False).tobytes()
        self.previous_id, self.previous_stamp, self.previous_emission = (
            packet.native_frame_id, packet.algorithm_stamp, emitted)
        return payload


def decode(payload, session, input_mode, *, now=None):
    """Decode exactly one complete record using the same host monotonic clock."""
    expected_session, expected_mode = session_id(session), mode_id(input_mode)
    now = time.monotonic() if now is None else now
    if type(payload) is not bytes or not 192 <= len(payload) <= MAXIMUM_BYTES:
        raise ValueError("one_bounded_complete_point_record_required")
    magic, version, mode, flags, count, native_count, reserved, frame_id, stamp, emitted, identity = (
        HEADER.unpack_from(payload))
    if (magic != MAGIC or version != 1 or mode != expected_mode or flags != 0 or reserved != 0
            or identity.hex() != expected_session or not 1 <= count <= MAXIMUM_POINTS
            or len(payload) != 192 + count * 24):
        raise ValueError("matching_version_session_mode_and_point_record_required")
    if (type(now) not in (float, int) or not math.isfinite(now) or now < 0
            or not math.isfinite(emitted) or emitted < 0 or not -.05 <= now - emitted <= 2.):
        raise ValueError("fresh_local_monotonic_point_record_required")
    pose = np.frombuffer(payload, dtype="<f8", count=16, offset=64).reshape(4, 4).copy()
    xyz = np.frombuffer(payload, dtype="<f8", count=count * 3, offset=192).reshape(count, 3).copy()
    packet = PointPacket(frame_id, stamp, native_count, xyz, pose)
    arrays(packet)
    xyz.flags.writeable = pose.flags.writeable = False
    return DecodedPointRecord(expected_session, input_mode, emitted, packet)


class PointPipeOutput:
    """Opt-in native-owner callback for a supervisor-provided dedicated FD."""

    def __init__(self, session, input_mode, fd, *, clock=time.monotonic):
        from .point_pipe import LatestPointPipeWriter
        self.encoder = PointEncoder(session, input_mode, clock=clock)
        self.writer = LatestPointPipeWriter(fd)

    def __call__(self, packet):
        self.writer.offer(self.encoder.encode(packet))

    def close(self):
        if threading.get_ident() != self.encoder.owner:
            raise RuntimeError("same_native_point_pipe_owner_required")
        self.writer.close()
