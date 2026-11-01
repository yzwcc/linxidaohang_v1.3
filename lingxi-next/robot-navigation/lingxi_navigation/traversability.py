"""Fuse observed clearance with independent support; never infer ground from air.

This is an offline integration contract, not a ground estimator or permission to
move. Declared-frame recorded maps cannot enter this metric-only feed.
"""
from dataclasses import dataclass, field, replace
import hashlib
import threading

from .navfn import CostmapSnapshot
from .occupancy import (ClearanceProjection, ObservationBatch, OccupancyResult,
                        canonical, digest_value)


GEOMETRY = ('map_id', 'receipt_sha256', 'width', 'height', 'resolution_metres',
            'origin_x_metres', 'origin_y_metres', 'frame', 'encoding')


@dataclass(frozen=True)
class GroundSupport:
    # 0: supported, 254: explicitly prohibited, 255: unobserved/uncertain.
    grid: CostmapSnapshot
    evidence_sha256: str
    metric_evidence_sha256: str
    source_kind: str

    def __post_init__(self):
        if (type(self.grid) is not CostmapSnapshot
                or not all(map(digest_value, (self.evidence_sha256, self.metric_evidence_sha256)))
                or self.source_kind not in ('registered_ground_support', 'isolated_synthetic_ground')
                or not set(self.grid.costs) <= {0, 254, 255}):
            raise ValueError('explicit_map_bound_ground_support_required')

    @property
    def sha256(self):
        return hashlib.sha256(canonical(dict(grid=self.grid.sha256,
            evidence=self.evidence_sha256, metric=self.metric_evidence_sha256,
            sourceKind=self.source_kind))).hexdigest()


@dataclass(frozen=True)
class TraversabilitySnapshot:
    grid: CostmapSnapshot
    clearance_sha256: str
    support_sha256: str
    metric_evidence_sha256: str
    offline_only: bool = field(default=True, init=False)
    navigation_ready: bool = field(default=False, init=False)

    @property
    def sha256(self):
        return hashlib.sha256(canonical(dict(grid=self.grid.sha256,
            clearance=self.clearance_sha256, support=self.support_sha256,
            metric=self.metric_evidence_sha256, offlineOnly=True))).hexdigest()

    def occupancy_values(self):
        # ROS OccupancyGrid is NOT a display map. Preserve unknown exactly.
        return [{0: 0, 254: 100, 255: -1}[cost] for cost in self.grid.costs]


class SupportPreservingFeed:
    """One fixed map/support epoch, monotonically increasing full revisions.

    Publish the entire fused grid to Nav2 StaticLayer (overwrite, not max), with
    no second marking/clearing layer that can reopen unknowns or latch old hits.
    Inflation and controller collision checks remain Nav2 responsibilities.
    """

    def __init__(self, support):
        if type(support) is not GroundSupport:
            raise ValueError('typed_ground_support_required')
        self.support, self.revision, self.owner = support, -1, threading.get_ident()

    def update(self, observations, clearance):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_traversability_feed_owner_required')
        if (type(observations) is not ObservationBatch or type(clearance) is not OccupancyResult
                or type(clearance.costmap) is not CostmapSnapshot
                or type(clearance.projection) is not ClearanceProjection):
            raise ValueError('metric_observations_and_projected_clearance_required')
        grid, support = clearance.costmap, self.support
        expected_source = ('isolated_synthetic_rays' if support.source_kind == 'isolated_synthetic_ground'
                           else 'registered_per_return_lidar')
        if (any(getattr(grid, key) != getattr(support.grid, key) for key in GEOMETRY)
                or (observations.map_id, observations.receipt_sha256, observations.revision) !=
                   (grid.map_id, grid.receipt_sha256, grid.revision)
                or observations.metric_evidence_sha256 != support.metric_evidence_sha256
                or observations.source_kind != expected_source
                or observations.sha256 != clearance.observations_sha256
                or grid.revision <= self.revision or grid.revision < support.grid.revision):
            raise ValueError('current_same_map_geometry_metric_and_observation_binding_required')
        projection = clearance.projection
        if (any(getattr(grid, key) != getattr(projection, key) for key in GEOMETRY[2:7])
                or not set(grid.costs) <= {0, 254, 255}
                or (clearance.free_cells, clearance.occupied_cells, clearance.unknown_cells) !=
                   tuple(grid.costs.count(value) for value in (0, 254, 255))
                or clearance.rays != sum(len(scan.returns) for scan in observations.scans)
                or clearance.scans != len(observations.scans)):
            raise ValueError('complete_matching_clearance_projection_required')
        costs = bytes(254 if air == 254 or ground == 254 else
                      0 if air == ground == 0 else 255
                      for air, ground in zip(grid.costs, support.grid.costs))
        result = TraversabilitySnapshot(replace(grid, costs=costs), clearance.sha256,
                                         support.sha256, support.metric_evidence_sha256)
        self.revision = grid.revision
        return result
