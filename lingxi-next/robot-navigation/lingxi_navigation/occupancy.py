"""Per-return visibility into a conservative, map-bound Nav2 clearance costmap."""
import ctypes
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import threading

from .navfn import CostmapSnapshot, finite


VERSION = "1.9.7"
DEFAULT_MINIMUM_Z_METRES = .1
DEFAULT_MAXIMUM_Z_METRES = 1.4
MAXIMUM_RAYS = 262144
DEPENDENCIES = {"liboctomap.so.1.9.7", "liboctomath.so.1.9.7"}


def digest_value(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class LidarReturn:
    origin_metres: tuple
    endpoint_metres: tuple

    def __post_init__(self):
        if (any(type(value) is not tuple or len(value) != 3 for value in
                (self.origin_metres, self.endpoint_metres))
                or any(not finite(value) or abs(value) > 500 for value in
                       self.origin_metres + self.endpoint_metres)
                or not 1e-5 < math.dist(self.origin_metres, self.endpoint_metres) <= 200):
            raise ValueError("finite_per_return_origin_and_endpoint_required")


@dataclass(frozen=True)
class ScanObservation:
    ordinal: int
    stamp: float
    source_sha256: str
    returns: tuple

    def __post_init__(self):
        if (type(self.ordinal) is not int or not 0 <= self.ordinal < 4096
                or not finite(self.stamp) or self.stamp <= 0 or not digest_value(self.source_sha256)
                or type(self.returns) is not tuple or not 1 <= len(self.returns) <= 8192
                or any(type(ray) is not LidarReturn for ray in self.returns)):
            raise ValueError("bounded_source_bound_lidar_scan_required")

    @property
    def sha256(self):
        digest = hashlib.sha256(canonical({"ordinal": self.ordinal, "stamp": self.stamp,
            "sourceSha256": self.source_sha256, "returns": len(self.returns)}))
        for ray in self.returns:
            digest.update(struct.pack("<6d", *(ray.origin_metres + ray.endpoint_metres)))
        return digest.hexdigest()


@dataclass(frozen=True)
class ObservationBatch:
    map_id: str
    receipt_sha256: str
    revision: int
    metric_evidence_sha256: str
    scans: tuple
    source_kind: str
    frame: str = "lingxi_saved_map"
    units: str = "metres"

    def __post_init__(self):
        if (type(self.map_id) is not str or re.fullmatch(r"slam-[0-9a-f]{32}", self.map_id) is None
                or not digest_value(self.receipt_sha256) or not digest_value(self.metric_evidence_sha256)
                or type(self.revision) is not int or not 0 <= self.revision < 2**53
                or self.frame != "lingxi_saved_map" or self.units != "metres"
                or self.source_kind not in ("registered_per_return_lidar", "isolated_synthetic_rays")
                or type(self.scans) is not tuple or not 1 <= len(self.scans) <= 4096
                or any(type(scan) is not ScanObservation for scan in self.scans)
                or sum(len(scan.returns) for scan in self.scans) > MAXIMUM_RAYS
                or any(scan.ordinal != index for index, scan in enumerate(self.scans))
                or any(b.stamp <= a.stamp for a, b in zip(self.scans, self.scans[1:]))):
            raise ValueError("complete_ordered_map_bound_metric_observations_required")

    @property
    def sha256(self):
        fields = {key: getattr(self, key) for key in self.__dataclass_fields__ if key != "scans"}
        return hashlib.sha256(canonical({**fields, "scans": [scan.sha256 for scan in self.scans]})).hexdigest()


@dataclass(frozen=True)
class ClearanceProjection:
    width: int
    height: int
    resolution_metres: float
    origin_x_metres: float
    origin_y_metres: float
    minimum_z_metres: float = DEFAULT_MINIMUM_Z_METRES
    maximum_z_metres: float = DEFAULT_MAXIMUM_Z_METRES
    maximum_range_metres: float = 10.

    def __post_init__(self):
        resolution = self.resolution_metres
        coordinates = (self.origin_x_metres, self.origin_y_metres,
                       self.minimum_z_metres, self.maximum_z_metres)
        if (any(type(value) is not int or not 8 <= value <= 1024 for value in (self.width, self.height))
                or self.width * self.height > 1048576 or not finite(resolution) or not .025 <= resolution <= 1
                or any(not finite(value) or abs(value) > 500
                       or abs(value / resolution - round(value / resolution)) >= 1e-7 for value in coordinates)
                or not 1 <= round((self.maximum_z_metres - self.minimum_z_metres) / resolution) <= 128
                or self.width * self.height * round((self.maximum_z_metres - self.minimum_z_metres) / resolution) > 4000000
                or not finite(self.maximum_range_metres) or not .1 <= self.maximum_range_metres <= 30):
            raise ValueError("bounded_voxel_aligned_clearance_projection_required")


@dataclass(frozen=True)
class OccupancyResult:
    costmap: CostmapSnapshot
    observations_sha256: str
    projection: ClearanceProjection
    rays: int
    scans: int
    nodes: int
    free_cells: int
    occupied_cells: int
    unknown_cells: int
    work_units: int
    navigation_ready: bool = field(default=False, init=False)

    @property
    def sha256(self):
        return hashlib.sha256(canonical({"costmap": self.costmap.sha256,
            "observations": self.observations_sha256,
            "projection": self.projection.__dict__, "octomapVersion": VERSION})).hexdigest()


class OctomapProjector:
    """Fresh volume per revision: no stale free cells survive pose corrections.

    This is not ground-support validation, footprint inflation or local avoidance.
    The native call must run in a supervised process before live product use.
    """

    def __init__(self, library, sha256, dependency_sha256):
        path = Path(library)
        if (not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file()
                or path.stat().st_size > 16 * 1024**2 or not digest_value(sha256)
                or hashlib.sha256(path.read_bytes()).hexdigest() != sha256
                or type(dependency_sha256) is not dict or set(dependency_sha256) != DEPENDENCIES
                or not all(map(digest_value, dependency_sha256.values()))):
            raise ValueError("hash_verified_octomap_adapter_and_dependencies_required")
        native = ctypes.CDLL(str(path))
        native.lingxi_octomap_abi_version.argtypes = []
        native.lingxi_octomap_abi_version.restype = ctypes.c_int
        native.lingxi_octomap_version.argtypes = []
        native.lingxi_octomap_version.restype = ctypes.c_char_p
        native.lingxi_octomap_error.argtypes = []
        native.lingxi_octomap_error.restype = ctypes.c_char_p
        if native.lingxi_octomap_abi_version() != 1 or native.lingxi_octomap_version() != VERSION.encode():
            raise ValueError("pinned_octomap_version_and_abi_required")
        loaded = {}
        for line in Path("/proc/self/maps").read_text(encoding="utf-8").splitlines():
            parts = line.split(maxsplit=5)
            if len(parts) != 6 or not parts[-1].startswith("/"):
                continue
            target = Path(parts[-1])
            if target.name.startswith(("liboctomap.so.", "liboctomath.so.")):
                target = target.resolve(strict=True)
                loaded[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        if loaded != dependency_sha256 or hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            raise ValueError("actual_loaded_octomap_dependency_hashes_required")
        native.lingxi_octomap_project.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t, *([ctypes.c_double] * 6),
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint64)]
        native.lingxi_octomap_project.restype = ctypes.c_int
        self.native, self.owner = native, threading.get_ident()

    def project(self, observations, projection, *, maximum_work_units=4000000):
        if threading.get_ident() != self.owner:
            raise RuntimeError("single_octomap_projector_owner_required")
        if (type(observations) is not ObservationBatch or type(projection) is not ClearanceProjection
                or type(maximum_work_units) is not int or not 1 <= maximum_work_units <= 16000000):
            raise ValueError("typed_observations_projection_and_work_budget_required")
        count = sum(len(scan.returns) for scan in observations.scans)
        rays = (ctypes.c_double * (count * 6))()
        ends = (ctypes.c_uint32 * len(observations.scans))()
        offset = 0
        for index, scan in enumerate(observations.scans):
            for ray in scan.returns:
                rays[offset * 6:offset * 6 + 6] = ray.origin_metres + ray.endpoint_metres
                offset += 1
            ends[index] = offset
        size = projection.width * projection.height
        output, stats = (ctypes.c_uint8 * size)(), (ctypes.c_uint64 * 7)()
        status = self.native.lingxi_octomap_project(rays, count, ends, len(ends),
            projection.resolution_metres, projection.maximum_range_metres,
            projection.origin_x_metres, projection.origin_y_metres,
            projection.minimum_z_metres, projection.maximum_z_metres,
            projection.width, projection.height, maximum_work_units, output, size, stats)
        if status != 0:
            raise RuntimeError(self.native.lingxi_octomap_error().decode("utf-8", errors="replace"))
        costs = bytes(output)
        if (stats[0] != count or stats[1] != len(ends) or not 0 < stats[2] <= 2000000
                or tuple(stats[3:6]) != tuple(costs.count(value) for value in (0, 254, 255))
                or sum(stats[3:6]) != size or stats[6] > maximum_work_units):
            raise RuntimeError("complete_octomap_projection_accounting_required")
        grid = CostmapSnapshot(observations.map_id, observations.receipt_sha256, observations.revision,
            projection.width, projection.height, projection.resolution_metres,
            projection.origin_x_metres, projection.origin_y_metres, costs)
        return OccupancyResult(grid, observations.sha256, projection, *stats)
