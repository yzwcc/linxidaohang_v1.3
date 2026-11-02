"""Bounded registered acquisition windows for declared-frame offline evaluation."""
from dataclasses import asdict, dataclass
import hashlib
import threading
import time

import numpy as np

from lingxi_slam.localization_session import LocalizationBinding
from lingxi_slam.registered_acquisition import RegisteredAcquisition
from .navfn import finite
from .occupancy import (ClearanceProjection, DeclaredObservationBatch, LidarReturn,
                        ScanObservation, canonical, digest_value)
from .recorded_occupancy import OfflineGeometry, RaySelection
from .terrain import TerrainBudget


@dataclass(frozen=True)
class RegisteredTerrainInput:
    observations: DeclaredObservationBatch
    generation: int
    received_at: float
    latest_received_at: float
    source_returns: int
    selected_returns: int
    projection_work_units: int
    work_evicted_scans: int
    work_evicted_returns: int


class RegisteredRayWindow:
    """One localization generation, original receipts and bounded complete scans.

    No new receipt is assigned to transformed geometry. Selection preserves
    measured pairs and is declared in the digest; it is not obstacle-completeness
    evidence. Terrain TTL starts at the OLDEST retained callback receipt.
    """

    def __init__(self, binding, generation, geometry, *, projection, budget=TerrainBudget(),
                 selection=RaySelection(), clock=time.monotonic):
        if (type(binding) is not LocalizationBinding or type(generation) is not int
                or not 0 <= generation < 2**53 - 1 or type(geometry) is not OfflineGeometry
                or type(projection) is not ClearanceProjection
                or type(budget) is not TerrainBudget or type(selection) is not RaySelection
                or not callable(clock)):
            raise ValueError('explicit_registered_window_binding_and_offline_geometry_required')
        self.binding, self.generation, self.geometry = binding, generation, geometry
        self.projection, self.budget, self.selection, self.clock = projection, budget, selection, clock
        self.owner, self.frames = threading.get_ident(), []
        self.frame_work = []
        self.fixed_work = projection.width * projection.height * round(
            (projection.maximum_z_metres - projection.minimum_z_metres) / projection.resolution_metres)
        if self.fixed_work >= budget.maximum_work_units:
            raise ValueError('registered_projection_exceeds_work_budget')
        self.work_evicted_scans = self.work_evicted_returns = 0
        self.previous_id, self.previous_stamp, self.previous_receipt, self.last_now = 0, 0., 0., None
        self.binding_sha256 = hashlib.sha256(canonical(dict(localization=asdict(binding),
            generation=generation, source='same_frame_registered_acquisition', offlineOnly=True))).hexdigest()

    def clear(self):
        self.frames.clear()
        self.frame_work.clear()

    def _work(self, packet):
        # Rigid transforms preserve length; do not rebuild declared geometry while busy.
        lengths = np.linalg.norm(packet.endpoints - packet.origins, axis=1) * self.geometry.metres_per_raw_unit
        if (not np.isfinite(lengths).all() or np.any(lengths <= 1e-5) or np.any(lengths > 200)):
            raise ValueError('bounded_complete_declared_ray_geometry_required')
        # Mirror the native preflight, with one extra unit per ray for rounding.
        # Charge ALL measured pairs even when an explicit offline selection is used.
        return int(np.sum(np.ceil(np.sqrt(3.) * np.minimum(lengths,
            self.projection.maximum_range_metres) / self.projection.resolution_metres) + 5))

    def offer(self, packet, *, materialize=True):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_registered_window_owner_required')
        if type(materialize) is not bool:
            raise ValueError('explicit_registered_window_materialization_required')
        now = self.clock()
        if (not finite(now) or now < 0 or self.last_now is not None and now < self.last_now):
            self.clear()
            raise ValueError('monotonic_registered_window_clock_required')
        self.last_now = now
        if (type(packet) is not RegisteredAcquisition or packet.binding != self.binding
                or packet.generation != self.generation or packet.coordinate_frame != 'lingxi_saved_map'
                or packet.units != 'native-raw' or packet.navigation_ready is not False
                or type(packet.native_frame_id) is not int or not self.previous_id < packet.native_frame_id < 2**53
                or not finite(packet.algorithm_stamp) or packet.algorithm_stamp <= self.previous_stamp
                or not finite(packet.received_at) or packet.received_at < self.previous_receipt
                or not 0 <= now - packet.received_at < self.budget.maximum_age_seconds
                or not digest_value(packet.source_sha256)):
            self.clear()
            raise ValueError('current_ordered_same_generation_registered_acquisition_required')
        origins, endpoints = packet.origins, packet.endpoints
        if (any(type(value) is not np.ndarray or value.dtype != np.float64 or value.ndim != 2
                or value.shape[1] != 3 or value.flags.writeable or not np.isfinite(value).all()
                for value in (origins, endpoints))
                or not 1 <= len(origins) <= min(65536, self.budget.maximum_rays)
                or origins.shape != endpoints.shape):
            self.clear()
            raise ValueError('bounded_complete_registered_ray_pairs_required')
        try:
            work = self._work(packet)
            if self.fixed_work + work > self.budget.maximum_work_units:
                raise ValueError('complete_registered_scan_exceeds_work_budget')
        except BaseException:
            self.clear()
            raise
        self.previous_id, self.previous_stamp, self.previous_receipt = (
            packet.native_frame_id, packet.algorithm_stamp, packet.received_at)
        self.frames.append(packet)
        self.frame_work.append(work)
        while (len(self.frames) > self.budget.maximum_scans
                or sum(len(frame.origins) for frame in self.frames) > self.budget.maximum_rays
                or packet.algorithm_stamp - self.frames[0].algorithm_stamp > self.budget.maximum_span_seconds
                or now - self.frames[0].received_at >= self.budget.maximum_age_seconds):
            self.frames.pop(0)
            self.frame_work.pop(0)
        while self.fixed_work + sum(self.frame_work) > self.budget.maximum_work_units:
            self.work_evicted_scans += 1
            self.work_evicted_returns += len(self.frames[0].origins)
            self.frames.pop(0)
            self.frame_work.pop(0)
        if not materialize:
            return None
        digest = hashlib.sha256(canonical(asdict(self.selection)))
        scans = []
        for ordinal, frame in enumerate(self.frames):
            indices = self.selection.indices(len(frame.origins), len(self.frames))
            digest.update(canonical(dict(frameId=frame.native_frame_id, source=frame.source_sha256,
                receivedAt=frame.received_at, originalReturns=len(frame.origins))))
            digest.update(indices.astype('<u8').tobytes())
            origins = self.geometry.apply(frame.origins[indices])
            endpoints = self.geometry.apply(frame.endpoints[indices])
            rays = tuple(LidarReturn(tuple(map(float, origin)), tuple(map(float, endpoint)))
                         for origin, endpoint in zip(origins, endpoints))
            scans.append(ScanObservation(ordinal, frame.algorithm_stamp, frame.source_sha256, rays))
        # This is a window revision, not an optimized-map artifact revision.
        batch = DeclaredObservationBatch(self.binding.map_id, self.binding.receipt_sha256,
            packet.native_frame_id, self.binding_sha256, self.geometry.sha256, digest.hexdigest(), tuple(scans))
        return RegisteredTerrainInput(batch, self.generation, self.frames[0].received_at,
            packet.received_at, sum(len(frame.origins) for frame in self.frames),
            sum(len(scan.returns) for scan in scans), self.fixed_work + sum(self.frame_work),
            self.work_evicted_scans, self.work_evicted_returns)
