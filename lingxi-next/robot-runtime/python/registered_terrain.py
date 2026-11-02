"""Opt-in offline composition; no sensor subscription or physical command API."""
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-slam'), str(ROOT / 'robot-navigation')]
from lingxi_slam.localization_session import LocalizationSession
from lingxi_slam.registered_acquisition import RegisteredAcquisition
from lingxi_navigation.recorded_occupancy import OfflineGeometry, RaySelection
from lingxi_navigation.occupancy import ClearanceProjection, canonical
from lingxi_navigation.registered_window import RegisteredRayWindow
from lingxi_navigation.terrain import TerrainBudget
from lingxi_navigation.terrain_lane import TerrainLane


class RegisteredTerrainSource:
    """Use as LocalizedNative's consumer and the isolated ROS publisher's source.

    Raw-window work stays on the localization owner; native terrain work stays
    on TerrainLane's supervised owner. Snapshot readers recheck localization,
    including while the native child is blocked. A changed epoch is terminal;
    an explicit new composition is required, never an automatic old-job retry.
    """

    def __init__(self, session, lane, geometry, *, projection, budget=TerrainBudget(), selection=RaySelection()):
        if (type(session) is not LocalizationSession or type(lane) is not TerrainLane
                or type(geometry) is not OfflineGeometry or type(projection) is not ClearanceProjection
                or type(budget) is not TerrainBudget
                or type(selection) is not RaySelection):
            raise ValueError('explicit_localization_lane_and_offline_geometry_required')
        state = lane.snapshot()
        if state.grid is not None or lane.last_sequence != -1 or state.state not in ('starting', 'ready'):
            raise ValueError('new_exclusive_registered_terrain_lane_required')
        self.session, self.lane, self.geometry, self.projection = session, lane, geometry, projection
        self.budget, self.selection = budget, selection
        self.lock, self.owner, self.window = threading.Lock(), None, None
        self.generation, self.submitted, self.retired = None, None, False
        self.submitted_sha256 = None
        self.submitted_epoch_sha256 = None

    def _valid(self, state):
        return (state['state'] == 'tracking' and not state['requestPending']
                and state['workerReady'] and not state['workerClosed']
                and (self.generation is None or state['generation'] == self.generation))

    def _retire(self, reason):
        if not self.retired:
            self.retired = True
            self.submitted = None
            if self.window is not None:
                self.window.clear()
            self.lane.retire(reason)

    def retire(self, reason='registered_source_revoked'):
        with self.lock:
            self._retire(reason)

    def __call__(self, packet):
        with self.lock:
            if self.retired:
                return False
            if self.owner is None:
                self.owner = threading.get_ident()
            if threading.get_ident() != self.owner or threading.get_ident() != self.session.owner:
                self._retire('wrong_registered_source_owner')
                raise RuntimeError('localization_owner_registered_terrain_consumer_required')
            try:
                state = self.session.state()
                if (type(packet) is not RegisteredAcquisition or packet.binding != self.session.binding
                        or not self._valid(state) or state['generation'] != packet.generation
                        or state['lastFrame'] != packet.native_frame_id
                        or state['referenceFrame'] != packet.native_frame_id):
                    self._retire('registered_localization_revoked')
                    return False
                if self.window is None:
                    self.generation = packet.generation
                    self.window = RegisteredRayWindow(packet.binding, packet.generation, self.geometry,
                        projection=self.projection, budget=self.budget, selection=self.selection, clock=self.session.clock)
                # Retain measured pairs while busy; only build a submission when the lane is ready.
                value = self.window.offer(packet, materialize=self.lane.snapshot().state == 'ready')
                if not self._valid(self.session.state()):
                    self._retire('registered_localization_revoked')
                    return False
                if value is None:
                    return False
                accepted = self.lane.submit(value.observations, sequence=packet.native_frame_id,
                                            received_at=value.received_at)
                if accepted:
                    self.submitted = value
                    self.submitted_sha256 = value.observations.sha256
                    batch = value.observations
                    self.submitted_epoch_sha256 = hashlib.sha256(canonical([batch.map_id,
                        batch.receipt_sha256, batch.binding_sha256, batch.geometry_declaration_sha256])).hexdigest()
                return accepted
            except BaseException:
                self._retire('registered_terrain_input_failed')
                raise

    def snapshot(self):
        with self.lock:
            try:
                state = self.session.state()
                if self.generation is not None and not self._valid(state):
                    self._retire('registered_localization_revoked')
                result = self.lane.snapshot()
                if result.state in ('failed', 'retired'):
                    self._retire(result.failure or 'registered_terrain_unavailable')
                    return self.lane.snapshot()
                if result.grid is not None:
                    value, grid = self.submitted, result.grid
                    if (value is None or grid.sequence != value.observations.revision
                            or grid.session != result.session
                            or grid.epoch_sha256 != self.submitted_epoch_sha256
                            or grid.observations_sha256 != self.submitted_sha256
                            or grid.map_revision != value.observations.revision
                            or grid.projection != self.projection
                            or grid.received_at != value.received_at
                            or grid.expires_at > value.received_at + self.budget.maximum_age_seconds):
                        self._retire('mismatched_registered_terrain_result')
                    if not self._valid(self.session.state()):
                        self._retire('registered_localization_revoked')
                    if self.retired:
                        return self.lane.snapshot()
                return replace(result, grid=None) if self.retired else result
            except BaseException:
                self._retire('registered_localization_unavailable')
                raise

    def close(self, timeout=23.):
        self.retire()
        self.lane.close(timeout)
