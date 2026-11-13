"""Immediate odometry rays, not finalized map geometry or a freshness claim."""
import ctypes
from dataclasses import dataclass
import math
import struct
import threading

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from .observation_archive import ObservationArchive, rigid

MAX_BYTES = 616 + 4096 * 64 + 65536 * 32


@dataclass(frozen=True)
class AcquisitionPacket:
    native_frame_id: int
    algorithm_stamp: float
    source_sha256: str
    origins: np.ndarray
    endpoints: np.ndarray
    world_lidar_pose: np.ndarray
    coordinate_frame: str = "lingxi_glim_odom"
    kind: str = "native_per_return_original_lio_acquisition"
    last_return_offset_ns: int = None
    world_lidar_last_return_pose: np.ndarray = None


@dataclass(frozen=True)
class EncodedAcquisition:
    """Owned native bytes; interpolation belongs to the registration lane."""
    data: bytes
    metadata: tuple
    algorithm_stamp: float
    expected_pose_bytes: bytes

    def decode(self):
        return decode_acquisition(self.data, self.metadata, self.algorithm_stamp,
            np.frombuffer(self.expected_pose_bytes, dtype='<f8').reshape(4, 4))


def decode_acquisition(data, metadata, stamp, expected_lidar_pose):
    if (type(data) is not bytes or len(metadata) != 4 or not 616 <= len(data) <= MAX_BYTES
            or any(type(value) is not int for value in metadata)
            or not 1 <= metadata[0] < 2**53 or not 1 <= metadata[1] <= 65536
            or not 2 <= metadata[2] <= 4096 or metadata[3] != len(data)
            or len(data) != 616 + metadata[2] * 64 + metadata[1] * 32
            or type(stamp) not in (int, float) or not math.isfinite(stamp)):
        raise ValueError("bounded_complete_acquisition_packet_required")
    # Only our one-frame odometry envelope is allowed, not a historical window.
    frame_id, frame_stamp, end, points, samples, kind = struct.unpack_from("<QddQQQ", data, 184)
    if (data[:40] != b"LXOBS002" + struct.pack("<4Q", 1, 1, metadata[1], 1)
            or struct.unpack_from("<Q", data, 40)[0] != 0
            or not np.array_equal(np.frombuffer(data, "<f8", 16, 48).reshape(4, 4), np.eye(4))
            or struct.unpack_from("<Q", data, 176)[0] != 1
            or (frame_id, frame_stamp, points, samples, kind) != (metadata[0], stamp, metadata[1], metadata[2], 2)):
        raise ValueError("single_current_odometry_acquisition_envelope_required")
    archive = ObservationArchive.from_bytes(data, batched_rotations=True)
    sources = list(archive)
    source = sources[0]
    extrinsic = rigid(np.frombuffer(data, "<f8", 16, 360).reshape(4, 4))
    corrected_imu = rigid(np.frombuffer(data, "<f8", 16, 488).reshape(4, 4))
    pose = rigid(corrected_imu @ np.linalg.inv(extrinsic))
    expected = np.asarray(expected_lidar_pose)
    if (source.frame_id != metadata[0] or source.stamp != stamp or expected.shape != (4, 4)
            or not np.isfinite(expected).all() or not np.allclose(pose, expected, rtol=0, atol=1e-8)):
        raise ValueError("matching_corrected_frame_acquisition_required")
    # This is an observed pose inside this scan's original IMU trajectory, not
    # prediction or a replacement timestamp for the scan-start pose.
    trajectory = np.frombuffer(data, '<f8', samples * 8, 616).reshape(samples, 8)
    returns = np.frombuffer(data, '<f8', points * 4, 616 + samples * 64).reshape(points, 4)
    offset_ns = int(round(float(returns[-1, 0]) * 1e9))
    last_time = stamp + float(returns[-1, 0])
    last_imu = np.eye(4)
    last_imu[:3, :3] = Slerp(trajectory[:, 0], Rotation.from_quat(trajectory[:, 4:]))([last_time]).as_matrix()[0]
    last_imu[:3, 3] = [np.interp(last_time, trajectory[:, 0], trajectory[:, i]) for i in (1, 2, 3)]
    reference = rigid(np.frombuffer(data, '<f8', 16, 232).reshape(4, 4))
    last_pose = rigid(corrected_imu @ np.linalg.inv(reference) @ last_imu @ np.linalg.inv(extrinsic))
    pose.flags.writeable = last_pose.flags.writeable = False
    return AcquisitionPacket(source.frame_id, stamp, source.source_sha256,
                             source.origins_raw, source.endpoints_raw, pose,
                             last_return_offset_ns=offset_ns, world_lidar_last_return_pose=last_pose)


class NativeAcquisitionOutput:
    """Use with PointOutputNative; consume once on the native sensor owner thread."""

    def __init__(self, library):
        self.owner, self.closed = threading.get_ident(), False
        self.previous_id, self.previous_stamp = 0, None
        self.library = ctypes.CDLL(str(library))
        self.library.lingxi_acquisition_error.argtypes = []
        self.library.lingxi_acquisition_error.restype = ctypes.c_char_p
        self.library.lingxi_acquisition_open.argtypes = []
        self.library.lingxi_acquisition_close.argtypes = []
        self.library.lingxi_acquisition_take.argtypes = [ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_uint64)]
        self.library.lingxi_acquisition_counts.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
        for name in ("open", "take", "counts", "close"):
            getattr(self.library, "lingxi_acquisition_" + name).restype = ctypes.c_int
        self.check(self.library.lingxi_acquisition_open())

    def check(self, value):
        if value < 0:
            raise RuntimeError(self.library.lingxi_acquisition_error().decode("utf-8", errors="replace"))
        return value

    def active(self):
        if self.closed or threading.get_ident() != self.owner:
            raise RuntimeError("active_same_thread_acquisition_output_required")

    def take(self, stamp, expected_lidar_pose, *, defer_decode=False):
        self.active()
        metadata = (ctypes.c_uint64 * 4)()
        status = self.check(self.library.lingxi_acquisition_take(0, None, metadata))
        if status == 0:
            return None
        first = tuple(metadata)
        if (status != 1 or first[0] != self.previous_id + 1 or not 1 <= first[1] <= 65536
                or not 2 <= first[2] <= 4096 or first[3] != 616 + first[2] * 64 + first[1] * 32
                or first[3] > MAX_BYTES or type(stamp) not in (int, float) or not math.isfinite(stamp)
                or self.previous_stamp is not None and stamp <= self.previous_stamp):
            raise ValueError("bounded_ordered_acquisition_copy_required")
        buffer = (ctypes.c_ubyte * first[3])()
        status = self.check(self.library.lingxi_acquisition_take(len(buffer), buffer, metadata))
        if status != 1 or tuple(metadata) != first:
            raise ValueError("unchanged_single_acquisition_copy_required")
        if defer_decode:
            expected = rigid(np.asarray(expected_lidar_pose)).astype('<f8').tobytes()
            packet = EncodedAcquisition(bytes(buffer), first, stamp, expected)
        else:
            packet = decode_acquisition(bytes(buffer), first, stamp, expected_lidar_pose)
        self.previous_id, self.previous_stamp = first[0], stamp
        return packet

    def counts(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("same_thread_acquisition_counters_required")
        values = (ctypes.c_uint64 * 5)()
        self.check(self.library.lingxi_acquisition_counts(values))
        return dict(zip(("callbacks", "initializationSkips", "copies", "takes", "discardedOnClose"), values))

    def close(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("same_thread_acquisition_close_required")
        if not self.closed:
            self.check(self.library.lingxi_acquisition_close())
            self.closed = True
