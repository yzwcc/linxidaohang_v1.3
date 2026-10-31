"""Bounded source-backed acquisition geometry; raw map coordinates are not calibration."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

MAX_SCANS, MAX_POINTS, MAX_SCAN_POINTS = 4096, 16777216, 65536
MAX_TRAJECTORY_SAMPLES = 1048576
MAX_BYTES = 768 * 1024**2


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rigid(matrix):
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
            or not np.allclose(matrix[3], [0, 0, 0, 1], rtol=0, atol=1e-8)
            or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), rtol=0, atol=1e-6)
            or abs(np.linalg.det(matrix[:3, :3]) - 1) >= 1e-6):
        raise ValueError("finite_rigid_observation_pose_required")
    return matrix


@dataclass(frozen=True)
class SourceObservation:
    ordinal: int
    frame_id: int
    stamp: float
    submap_id: int
    source_sha256: str
    trajectory_kind: str
    origins_raw: np.ndarray
    endpoints_raw: np.ndarray


class ObservationArchive:
    """Replay all sources in order. Publish derived outputs only after iteration completes.

    No range/height filtering or synthetic ray origins are introduced here. Geometry
    follows per-return IMU interpolation, optimized local frame pose, then the frozen
    global submap pose. Source hashes deliberately exclude optimization transforms.
    """

    def __init__(self, path, expected_sha256):
        self.path = Path(path)
        if (not self.path.is_absolute() or self.path.is_symlink() or not self.path.is_file()
                or not 40 <= self.path.stat().st_size <= MAX_BYTES
                or type(expected_sha256) is not str or re.fullmatch("[a-f0-9]{64}", expected_sha256) is None
                or sha256(self.path) != expected_sha256):
            raise ValueError("bounded_hash_verified_observation_archive_required")
        self.sha256 = expected_sha256
        with self.path.open("rb") as stream:
            if stream.read(8) != b"LXOBS002":
                raise ValueError("source_observation_archive_version_required")
            self.submaps, self.scans, self.points, self.revision = struct.unpack("<4Q", stream.read(32))
        if (not 1 <= self.submaps <= self.scans <= MAX_SCANS or not self.scans <= self.points <= MAX_POINTS
                or not 1 <= self.revision < 2**53):
            raise ValueError("bounded_complete_observation_header_required")

    def __iter__(self):
        digest = hashlib.sha256()
        with self.path.open("rb") as stream:
            def read(size):
                data = stream.read(size)
                if len(data) != size:
                    raise ValueError("complete_observation_archive_bytes_required")
                digest.update(data)
                return data

            def matrix():
                return rigid(np.frombuffer(read(128), dtype="<f8").reshape(4, 4))

            if read(40) != b"LXOBS002" + struct.pack("<4Q", self.submaps, self.scans, self.points, self.revision):
                raise ValueError("unchanged_observation_archive_header_required")
            ordinal, total, last_id, last_stamp, total_samples = 0, 0, -1, -1., 0
            for submap_id in range(self.submaps):
                if struct.unpack("<Q", read(8))[0] != submap_id:
                    raise ValueError("ordered_observation_submaps_required")
                global_pose = matrix()
                count = struct.unpack("<Q", read(8))[0]
                if not 1 <= count <= self.scans - ordinal:
                    raise ValueError("complete_observation_submap_counts_required")
                for _ in range(count):
                    header = read(48)
                    frame_id, stamp, end, points, samples, trajectory_kind = struct.unpack("<QddQQQ", header)
                    if (not last_id < frame_id < 2**53 or not np.isfinite([stamp, end]).all()
                            or not last_stamp < stamp <= end or not 1 <= points <= MAX_SCAN_POINTS
                            or points > self.points - total or not 2 <= samples <= 4096
                            or samples > MAX_TRAJECTORY_SAMPLES - total_samples or trajectory_kind not in (1, 2)):
                        raise ValueError("bounded_ordered_source_scan_required")
                    reference_bytes, extrinsic_bytes = read(128), read(128)
                    reference = rigid(np.frombuffer(reference_bytes, dtype="<f8").reshape(4, 4))
                    extrinsic = rigid(np.frombuffer(extrinsic_bytes, dtype="<f8").reshape(4, 4))
                    local_pose = matrix()
                    trajectory_bytes, points_bytes = read(samples * 64), read(points * 32)
                    trajectory = np.frombuffer(trajectory_bytes, dtype="<f8").reshape(samples, 8)
                    source = np.frombuffer(points_bytes, dtype="<f8").reshape(points, 4)
                    if (not np.isfinite(trajectory).all() or not np.isfinite(source).all()
                            or np.any(np.diff(trajectory[:, 0]) <= 0) or np.any(np.diff(source[:, 0]) < 0)
                            or source[0, 0] < 0 or not np.allclose(np.linalg.norm(trajectory[:, 4:], axis=1), 1,
                                                                                rtol=0, atol=1e-6)):
                        raise ValueError("finite_ordered_acquisition_sources_required")
                    if (trajectory[0, 0] != stamp
                            or not np.allclose(reference[:3, 3], trajectory[0, 1:4], rtol=0, atol=1e-8)
                            or not np.allclose(reference[:3, :3], Rotation.from_quat(trajectory[0, 4:]).as_matrix(),
                                               rtol=0, atol=1e-6)):
                        raise ValueError("source_reference_must_match_acquisition_trajectory_start")
                    times = stamp + source[:, 0]
                    if times[0] < trajectory[0, 0] or times[-1] > trajectory[-1, 0] or times[-1] > end:
                        raise ValueError("every_return_needs_bracketed_acquisition_time")
                    source_digest = hashlib.sha256(header + reference_bytes + extrinsic_bytes +
                                                  trajectory_bytes + points_bytes).hexdigest()
                    rotations = Slerp(trajectory[:, 0], Rotation.from_quat(trajectory[:, 4:]))(times)
                    translations = np.column_stack([np.interp(times, trajectory[:, 0], trajectory[:, i])
                                                    for i in (1, 2, 3)])
                    imu_lidar = np.linalg.inv(extrinsic)
                    origins = rotations.apply(np.broadcast_to(imu_lidar[:3, 3], (points, 3))) + translations
                    ends = rotations.apply(source[:, 1:] @ imu_lidar[:3, :3].T + imu_lidar[:3, 3]) + translations
                    correction = global_pose @ local_pose @ np.linalg.inv(reference)
                    origins = origins @ correction[:3, :3].T + correction[:3, 3]
                    ends = ends @ correction[:3, :3].T + correction[:3, 3]
                    if not np.isfinite(origins).all() or not np.isfinite(ends).all():
                        raise ValueError("finite_registered_source_geometry_required")
                    origins.flags.writeable = ends.flags.writeable = False
                    yield SourceObservation(ordinal, frame_id, stamp, submap_id, source_digest,
                        "smoothed_imu" if trajectory_kind == 1 else "original_lio_imu", origins, ends)
                    last_id, last_stamp = frame_id, stamp
                    ordinal += 1
                    total += points
                    total_samples += samples
            if (ordinal != self.scans or total != self.points or stream.read(1)
                    or digest.hexdigest() != self.sha256 or sha256(self.path) != self.sha256):
                raise ValueError("complete_unchanged_observation_archive_required")


def inspect_observations(path, expected_sha256):
    archive = ObservationArchive(path, expected_sha256)
    sources, geometry = hashlib.sha256(), hashlib.sha256()
    kinds = {"smoothed_imu": 0, "original_lio_imu": 0}
    for scan in archive:
        kinds[scan.trajectory_kind] += 1
        sources.update(bytes.fromhex(scan.source_sha256))
        geometry.update(np.column_stack((scan.origins_raw, scan.endpoints_raw)).astype("<f8").tobytes())
    return {"schemaVersion": 1, "archiveSha256": archive.sha256, "submaps": archive.submaps,
            "scans": archive.scans, "returns": archive.points, "revision": archive.revision,
            "orderedSourceSha256": sources.hexdigest(), "registeredGeometrySha256": geometry.hexdigest(),
            "trajectorySources": kinds,
            "coordinateFrame": "lingxi_glim_map", "coordinateUnits": "native-raw",
            "allPreprocessedReturnsPreserved": True, "perReturnAcquisitionTimeBracketed": True,
            "physicalScaleValidated": False, "navigationReady": False}
