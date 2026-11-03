"""Pinned Patchwork++ candidates in an explicitly declared offline ground frame.

Candidate support is not a metric CostmapSnapshot and cannot authorize motion.
No interpolation fills holes: only repeatedly observed, near-level floor returns
can support a cell. Empty air, sparse patches and out-of-range returns stay unknown.
"""
from array import array
import ctypes
from dataclasses import dataclass, field
import hashlib
from itertools import chain
import math
from pathlib import Path
import threading

from .navfn import finite
from .occupancy import (ClearanceProjection, DeclaredClearanceResult,
                        DeclaredObservationBatch, canonical, digest_value)


UPSTREAM_REVISION = '3e6903a1d5537a4cc2ace897b0bbb98a92d6014c'


class GroundFrameMismatch(ValueError):
    def __init__(self, ordinal, height_error_metres, origin_span_metres):
        self.ordinal = ordinal
        self.height_error_metres = height_error_metres
        self.origin_span_metres = origin_span_metres
        super().__init__('declared_ground_height_and_bounded_deskewed_scan_required: '
            f'scan={ordinal}, height_error_metres={height_error_metres:.6f}, '
            f'origin_span_metres={origin_span_metres:.6f}; '
            'offline_declaration_is_not_physical_calibration')


@dataclass(frozen=True)
class GroundDeclaration:
    sensor_height_metres: float
    ground_z_metres: float
    minimum_range_metres: float = .3
    maximum_range_metres: float = 10.
    height_tolerance_metres: float = .08
    maximum_height_spread_metres: float = .04
    minimum_points_per_cell: int = 3
    minimum_scans_per_cell: int = 2

    def __post_init__(self):
        if (not all(map(finite, (self.sensor_height_metres, self.ground_z_metres,
                self.minimum_range_metres, self.maximum_range_metres,
                self.height_tolerance_metres, self.maximum_height_spread_metres)))
                or not .2 <= self.sensor_height_metres <= 3.
                or abs(self.ground_z_metres) > 10.
                or not .1 <= self.minimum_range_metres <= 5.
                or not 2. <= self.maximum_range_metres <= 30.
                or self.maximum_range_metres - self.minimum_range_metres < 1.
                or not .01 <= self.height_tolerance_metres <= .1
                or not .005 <= self.maximum_height_spread_metres <= .1
                or type(self.minimum_points_per_cell) is not int
                or not 3 <= self.minimum_points_per_cell <= 100
                or type(self.minimum_scans_per_cell) is not int
                or not 2 <= self.minimum_scans_per_cell <= 100):
            raise ValueError('explicit_bounded_offline_ground_declaration_required')

    @property
    def sha256(self):
        return hashlib.sha256(canonical(dict(**self.__dict__,
            frameAssumption='gravity_aligned_z_up', physicalCalibration=False))).hexdigest()


@dataclass(frozen=True)
class GroundLabels:
    labels: bytes
    input_sha256: str
    declaration_sha256: str
    library_sha256: str

    def __post_init__(self):
        if (type(self.labels) is not bytes or not 3 <= len(self.labels) <= 8192
                or not set(self.labels) <= {0, 254, 255}
                or not all(map(digest_value, (self.input_sha256, self.declaration_sha256,
                                             self.library_sha256)))):
            raise ValueError('complete_source_bound_ground_labels_required')


class PatchworkSegmenter:
    """Single-owner native core. Supervise whole offline jobs in a timed child."""

    def __init__(self, library, sha256):
        path = Path(library)
        if (not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file()
                or path.stat().st_size > 16 * 1024**2 or not digest_value(sha256)
                or hashlib.sha256(path.read_bytes()).hexdigest() != sha256):
            raise ValueError('direct_hash_verified_patchwork_library_required')
        native = ctypes.CDLL(str(path))
        native.lingxi_ground_abi.restype = ctypes.c_int
        native.lingxi_ground_revision.restype = ctypes.c_char_p
        native.lingxi_ground_error.restype = ctypes.c_char_p
        if native.lingxi_ground_abi() != 1 or native.lingxi_ground_revision() != UPSTREAM_REVISION.encode():
            raise ValueError('pinned_patchwork_revision_and_abi_required')
        native.lingxi_ground_segment.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
            ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint64)]
        native.lingxi_ground_segment.restype = ctypes.c_int
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            raise ValueError('unchanged_loaded_patchwork_library_required')
        self.native, self.sha256, self.owner = native, sha256, threading.get_ident()

    def segment(self, points, declaration):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_patchwork_segmenter_owner_required')
        if (type(declaration) is not GroundDeclaration or type(points) is not tuple
                or not 3 <= len(points) <= 8192 or any(type(point) is not tuple or len(point) != 3
                    or any(not finite(value) or abs(value) > 100 for value in point) for point in points)):
            raise ValueError('bounded_finite_gravity_aligned_sensor_points_required')
        packed = array('d', chain.from_iterable(points))
        xyz = (ctypes.c_double * len(packed)).from_buffer(packed)
        labels, counts = (ctypes.c_uint8 * len(points))(), (ctypes.c_uint64 * 3)()
        status = self.native.lingxi_ground_segment(xyz, len(points), declaration.sensor_height_metres,
            declaration.minimum_range_metres, declaration.maximum_range_metres, labels, len(points), counts)
        if status != 0:
            raise RuntimeError(self.native.lingxi_ground_error().decode('utf-8', errors='replace'))
        result = bytes(labels)
        if tuple(counts) != tuple(result.count(value) for value in (0, 254, 255)) or sum(counts) != len(points):
            raise RuntimeError('complete_unique_ground_label_accounting_required')
        return GroundLabels(result, hashlib.sha256(canonical(points)).hexdigest(), declaration.sha256, self.sha256)


@dataclass(frozen=True)
class DeclaredGroundCandidate:
    observations_sha256: str
    declaration_sha256: str
    library_sha256: str
    projection: ClearanceProjection
    costs: bytes
    scans: int
    ground_returns: int
    nonground_returns: int
    unclassified_returns: int
    offline_only: bool = field(default=True, init=False)
    ground_support_validated: bool = field(default=False, init=False)
    navigation_ready: bool = field(default=False, init=False)

    def __post_init__(self):
        if (not all(map(digest_value, (self.observations_sha256, self.declaration_sha256,
                                      self.library_sha256)))
                or type(self.projection) is not ClearanceProjection
                or type(self.costs) is not bytes
                or len(self.costs) != self.projection.width * self.projection.height
                or not set(self.costs) <= {0, 254, 255}
                or type(self.scans) is not int or not 1 <= self.scans <= 4096
                or any(type(value) is not int or not 0 <= value <= 262144 for value in
                       (self.ground_returns, self.nonground_returns, self.unclassified_returns))
                or not 1 <= self.ground_returns + self.nonground_returns + self.unclassified_returns <= 262144):
            raise ValueError('complete_bounded_declared_ground_candidate_required')

    @property
    def sha256(self):
        metadata = {key: value for key, value in self.__dict__.items() if key not in ('projection', 'costs')}
        return hashlib.sha256(canonical(dict(**metadata, projection=self.projection.__dict__,
            upstreamRevision=UPSTREAM_REVISION)) + b'\n' + self.costs).hexdigest()


def validate_declared_ground_frame(observations, declaration):
    if type(observations) is not DeclaredObservationBatch or type(declaration) is not GroundDeclaration:
        raise ValueError('declared_observations_and_ground_required')
    for scan in observations.scans:
        # Reference a measured origin, not an averaged or guessed robot position.
        anchor = scan.returns[0].origin_metres
        height_error = abs(anchor[2] - declaration.sensor_height_metres - declaration.ground_z_metres)
        origin_span = max(math.dist(anchor, ray.origin_metres) for ray in scan.returns)
        if height_error > declaration.height_tolerance_metres or origin_span > .35:
            raise GroundFrameMismatch(scan.ordinal, height_error, origin_span)


def declared_ground_candidates(observations, projection, declaration, segmenter):
    if (type(observations) is not DeclaredObservationBatch or type(projection) is not ClearanceProjection
            or type(declaration) is not GroundDeclaration or type(segmenter) is not PatchworkSegmenter):
        raise ValueError('declared_observations_projection_ground_and_native_segmenter_required')
    validate_declared_ground_frame(observations, declaration)
    cells = {}
    totals = [0, 0, 0]
    for scan in observations.scans:
        anchor = scan.returns[0].origin_metres
        ax, ay, az = anchor
        points = tuple((ray.endpoint_metres[0] - ax, ray.endpoint_metres[1] - ay,
                        ray.endpoint_metres[2] - az) for ray in scan.returns)
        # Tiny scans supply no plane evidence; preserve them as unclassified.
        if len(points) >= 3:
            result = segmenter.segment(points, declaration)
            if (type(result) is not GroundLabels or len(result.labels) != len(points)
                    or result.input_sha256 != hashlib.sha256(canonical(points)).hexdigest()
                    or result.declaration_sha256 != declaration.sha256
                    or result.library_sha256 != segmenter.sha256):
                raise ValueError('complete_scan_bound_ground_labels_required')
            labels = result.labels
        else:
            labels = bytes([255] * len(points))
        for index, value in enumerate((0, 254, 255)):
            totals[index] += labels.count(value)
        for ray, label in zip(scan.returns, labels):
            x, y, z = ray.endpoint_metres
            cx = math.floor((x - projection.origin_x_metres) / projection.resolution_metres)
            cy = math.floor((y - projection.origin_y_metres) / projection.resolution_metres)
            if not 0 <= cx < projection.width or not 0 <= cy < projection.height:
                continue
            index = cy * projection.width + cx
            cell = cells.get(index)
            if cell is None:
                cell = cells[index] = [0, set(), math.inf, -math.inf, False, False]
            dz = z - declaration.ground_z_metres
            # Even points Patchwork calls ground must not promote a step/platform.
            if dz > declaration.height_tolerance_metres or dz < -declaration.height_tolerance_metres:
                if z <= projection.maximum_z_metres:
                    cell[4] = True
            elif label == 0 and declaration.minimum_range_metres <= math.hypot(x - anchor[0], y - anchor[1]) <= declaration.maximum_range_metres:
                cell[0] += 1
                cell[1].add(scan.ordinal)
                cell[2], cell[3] = min(cell[2], z), max(cell[3], z)
            else:
                cell[5] = True
    costs = bytearray([255] * (projection.width * projection.height))
    for index, (points, scans, low, high, blocked, uncertain) in cells.items():
        if blocked:
            costs[index] = 254
        elif (not uncertain and points >= declaration.minimum_points_per_cell and len(scans) >= declaration.minimum_scans_per_cell
                and high - low <= declaration.maximum_height_spread_metres):
            costs[index] = 0
    return DeclaredGroundCandidate(observations.sha256, declaration.sha256, segmenter.sha256,
                                  projection, bytes(costs), len(observations.scans), *totals)


@dataclass(frozen=True)
class DeclaredTraversability:
    clearance_sha256: str
    support_sha256: str
    costs: bytes
    offline_only: bool = field(default=True, init=False)
    navigation_ready: bool = field(default=False, init=False)

    def __post_init__(self):
        if (not all(map(digest_value, (self.clearance_sha256, self.support_sha256)))
                or type(self.costs) is not bytes or not 64 <= len(self.costs) <= 1048576
                or not set(self.costs) <= {0, 254, 255}):
            raise ValueError('complete_declared_traversability_required')

    @property
    def sha256(self):
        return hashlib.sha256(canonical(dict(clearance=self.clearance_sha256,
            support=self.support_sha256, offlineOnly=True, navigationReady=False)) + b'\n' + self.costs).hexdigest()


def fuse_declared(clearance, support):
    if (type(clearance) is not DeclaredClearanceResult or type(support) is not DeclaredGroundCandidate
            or clearance.observations_sha256 != support.observations_sha256
            or clearance.projection != support.projection
            or len(clearance.costs) != len(support.costs)
            or len(support.costs) != support.projection.width * support.projection.height
            or not set(clearance.costs + support.costs) <= {0, 254, 255}):
        raise ValueError('same_observations_geometry_and_complete_declared_candidates_required')
    return DeclaredTraversability(clearance.sha256, support.sha256,
        bytes(254 if air == 254 or ground == 254 else 0 if air == ground == 0 else 255
              for air, ground in zip(clearance.costs, support.costs)))
