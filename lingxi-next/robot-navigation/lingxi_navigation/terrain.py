"""Bounded full-replacement terrain updates over corrected acquisition rays.

Native algorithms run in the caller's supervised worker. This producer never
turns an uncalibrated recording into a metric navigation permission.
"""
from dataclasses import dataclass, replace
import hashlib
import threading
import time

from .ground import (GroundDeclaration, PatchworkSegmenter, DeclaredGroundCandidate,
                     DeclaredTraversability, declared_ground_candidates, fuse_declared)
from .navfn import finite
from .occupancy import (ClearanceProjection, DeclaredObservationBatch, DeclaredClearanceResult, OctomapProjector,
                        MAXIMUM_RAYS, canonical)


@dataclass(frozen=True)
class TerrainBudget:
    maximum_scans: int = 32
    maximum_rays: int = 65536
    maximum_span_seconds: float = 4.
    maximum_processing_seconds: float = 2.
    maximum_age_seconds: float = 3.
    maximum_work_units: int = 4000000

    def __post_init__(self):
        if (type(self.maximum_scans) is not int or not 2 <= self.maximum_scans <= 128
                or type(self.maximum_rays) is not int or not 3 <= self.maximum_rays <= MAXIMUM_RAYS
                or not finite(self.maximum_span_seconds) or not .1 <= self.maximum_span_seconds <= 30.
                or not finite(self.maximum_processing_seconds) or not .05 <= self.maximum_processing_seconds <= 10.
                or not finite(self.maximum_age_seconds) or not .1 <= self.maximum_age_seconds <= 15.
                or type(self.maximum_work_units) is not int or not 1 <= self.maximum_work_units <= 16000000):
            raise ValueError('bounded_terrain_window_and_deadlines_required')


@dataclass(frozen=True)
class TerrainSnapshot:
    sequence: int
    observations: DeclaredObservationBatch
    clearance: DeclaredClearanceResult
    support: DeclaredGroundCandidate
    fused: DeclaredTraversability
    received_at: float
    started_at: float
    completed_at: float
    expires_at: float

    def offline_occupancy_values(self):
        """Only the isolated offline ROS workspace may consume this export."""
        return tuple({0: 0, 254: 100, 255: -1}[value] for value in self.fused.costs)

    def summary(self):
        return dict(sequence=self.sequence, mapRevision=self.observations.revision,
            observationsSha256=self.observations.sha256,
            clearanceSha256=self.clearance.sha256, supportSha256=self.support.sha256,
            traversabilitySha256=self.fused.sha256,
            traversabilityBytesSha256=hashlib.sha256(self.fused.costs).hexdigest(),
            scans=len(self.observations.scans), rays=sum(len(scan.returns) for scan in self.observations.scans),
            cells=dict(zip(('free', 'occupied', 'unknown'), (self.fused.costs.count(i) for i in (0, 254, 255)))),
            processingSeconds=self.completed_at - self.started_at,
            ingressToCompletionSeconds=self.completed_at - self.received_at,
            offlineOnly=True, physicalScaleValidated=False, groundSupportValidated=False, navigationReady=False)


class TerrainProducer:
    """One map epoch and owner; no unbounded backlog or partial/stale publication.

    Each input is a complete, currently corrected window. Rebuild both ground
    and air from that window, never union old coordinates after loop correction.
    Caller receipt time must refer to acquisition ingress, not worker completion.
    A corrected copy of the same final capture cannot renew its freshness.
    """

    def __init__(self, projector, segmenter, projection, declaration, budget=TerrainBudget(), *, clock=time.monotonic):
        if (type(projector) is not OctomapProjector or type(segmenter) is not PatchworkSegmenter
                or type(projection) is not ClearanceProjection or type(declaration) is not GroundDeclaration
                or type(budget) is not TerrainBudget or not callable(clock)):
            raise ValueError('typed_native_terrain_components_required')
        self.projector, self.segmenter, self.projection = projector, segmenter, projection
        self.declaration, self.budget, self.clock = declaration, budget, clock
        self.owner, self.sequence, self.revision = threading.get_ident(), -1, -1
        self.epoch = self.capture = self.capture_received = self.snapshot = self.last_clock = None

    def _now(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_terrain_producer_owner_required')
        now = self.clock()
        if not finite(now) or now < 0 or self.last_clock is not None and now < self.last_clock:
            self.snapshot = None
            raise ValueError('monotonic_terrain_clock_required')
        self.last_clock = now
        return now

    def current(self):
        now = self._now()
        if self.snapshot is not None and now >= self.snapshot.expires_at:
            self.snapshot = None
        return self.snapshot

    def update(self, observations, *, sequence, received_at):
        now = self._now()
        self.snapshot = None
        if (type(observations) is not DeclaredObservationBatch or type(sequence) is not int
                or not 0 <= sequence < 2**53 or sequence <= self.sequence
                or not finite(received_at) or not 0 <= received_at <= now):
            raise ValueError('ordered_complete_declared_terrain_update_required')
        epoch = (observations.map_id, observations.receipt_sha256, observations.binding_sha256,
                 observations.geometry_declaration_sha256)
        if self.epoch is not None and (epoch != self.epoch or observations.revision < self.revision):
            raise ValueError('same_terrain_map_epoch_and_nonretired_correction_required')
        scans = observations.scans
        if (len(scans) > self.budget.maximum_scans
                or sum(len(scan.returns) for scan in scans) > self.budget.maximum_rays
                or scans[-1].stamp - scans[0].stamp > self.budget.maximum_span_seconds):
            raise ValueError('terrain_window_budget_exceeded')
        capture = (scans[-1].stamp, scans[-1].source_sha256)
        if self.capture is not None:
            if capture[0] < self.capture[0] or capture[0] == self.capture[0] and capture != self.capture:
                raise ValueError('ordered_same_capture_source_required')
            if capture == self.capture:
                received_at = min(received_at, self.capture_received)
        # Consume the generation before native work. Failure cannot restore an
        # older correction/sequence, nor can retries refresh the same capture.
        self.epoch, self.sequence, self.revision = epoch, sequence, observations.revision
        self.capture, self.capture_received = capture, received_at
        expires = received_at + self.budget.maximum_age_seconds
        if now >= expires:
            raise TimeoutError('terrain_input_expired')
        support = declared_ground_candidates(observations, self.projection, self.declaration, self.segmenter)
        if self._now() - now > self.budget.maximum_processing_seconds or self.last_clock >= expires:
            raise TimeoutError('terrain_native_processing_deadline_exceeded')
        clearance = self.projector.project_declared(observations, self.projection,
            maximum_work_units=self.budget.maximum_work_units)
        statistics = clearance.statistics
        if (type(statistics) is not tuple or len(statistics) != 7
                or any(type(value) is not int or value < 0 for value in statistics)
                or statistics[:2] != (sum(len(scan.returns) for scan in scans), len(scans))
                or statistics[3:6] != tuple(clearance.costs.count(i) for i in (0, 254, 255))
                or statistics[6] > self.budget.maximum_work_units):
            raise ValueError('complete_current_terrain_clearance_required')
        fused = fuse_declared(clearance, support)
        completed = self._now()
        if completed - now > self.budget.maximum_processing_seconds or completed >= expires:
            raise TimeoutError('terrain_native_processing_deadline_exceeded')
        self.snapshot = TerrainSnapshot(sequence, observations, clearance, support, fused, received_at, now, completed, expires)
        return self.snapshot


@dataclass(frozen=True)
class RecordedTerrainWindow:
    sequence: int
    source_start: int
    source_end: int
    observations: DeclaredObservationBatch


def recorded_windows(observations, budget=TerrainBudget(), *, stride=16):
    """Coalesce recording arrivals, retaining source indices and real map revision.

    Window ordinal renumbering is explicit in the selection digest. No map pose,
    raw return, saved archive, declared geometry or map revision is changed.
    """
    if (type(observations) is not DeclaredObservationBatch or type(budget) is not TerrainBudget
            or type(stride) is not int or not 1 <= stride <= budget.maximum_scans):
        raise ValueError('declared_recording_bounded_window_and_stride_required')
    scans = observations.scans
    if any(len(scan.returns) > budget.maximum_rays for scan in scans):
        raise ValueError('one_scan_exceeds_terrain_window_budget')
    ends = sorted(set(range(stride, len(scans) + 1, stride)) | {len(scans)})
    for sequence, end in enumerate(ends):
        start, rays = end - 1, len(scans[end - 1].returns)
        while (start > 0 and end - start < budget.maximum_scans
                and rays + len(scans[start - 1].returns) <= budget.maximum_rays
                and scans[end - 1].stamp - scans[start - 1].stamp <= budget.maximum_span_seconds):
            start -= 1
            rays += len(scans[start].returns)
        selection = hashlib.sha256(canonical(dict(sourceSelection=observations.selection_sha256,
            start=start, end=end, sourceScans=[scan.sha256 for scan in scans[start:end]]))).hexdigest()
        batch = replace(observations, selection_sha256=selection,
            scans=tuple(replace(scan, ordinal=i) for i, scan in enumerate(scans[start:end])))
        yield RecordedTerrainWindow(sequence, start, end, batch)
