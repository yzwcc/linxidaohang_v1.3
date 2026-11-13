"""Nonblocking single-slot terrain orchestration, separate from ROS executors.

Retirement hides in-flight results immediately. Native cleanup stays with its
owner thread and is bounded by TerrainProcess deadlines; no restart or backlog.
"""
from dataclasses import dataclass, replace
import threading
import time

from .navfn import finite
from .occupancy import DeclaredObservationBatch
from .runtime_fault import record_fault
from .terrain_process import TerrainGrid


@dataclass(frozen=True)
class LaneSnapshot:
    version: int
    state: str
    session: object = None
    grid: object = None
    failure: object = None
    child_pid: object = None


class TerrainLane:
    def __init__(self, factory, *, poll_seconds=.02):
        if not callable(factory) or not finite(poll_seconds) or not .005 <= poll_seconds <= .1:
            raise ValueError('bounded_terrain_lane_configuration_required')
        self.factory, self.poll_seconds = factory, poll_seconds
        self.condition = threading.Condition()
        self.value = LaneSnapshot(0, 'starting')
        self.pending, self.stopping, self.last_sequence = None, False, -1
        self.thread = threading.Thread(target=self._run, name='lingxi-terrain-owner', daemon=True)
        self.thread.start()

    def _set(self, **values):
        self.value = replace(self.value, version=self.value.version + 1, **values)
        self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            if self.value.grid is not None and time.monotonic() >= self.value.grid.expires_at:
                self._set(grid=None, failure='terrain_expired')
            return self.value

    def submit(self, observations, *, sequence, received_at):
        now = time.monotonic()
        with self.condition:
            if (type(observations) is not DeclaredObservationBatch or type(sequence) is not int
                    or not self.last_sequence < sequence < 2**53 or not finite(received_at)
                    or not 0 <= received_at <= now):
                self.retire('invalid_terrain_submission')
                raise ValueError('ordered_complete_terrain_lane_request_required')
            if self.stopping or self.value.state != 'ready':
                return False
            self.last_sequence = sequence
            self.pending = (observations, sequence, received_at)
            self._set(state='busy', grid=None, failure='terrain_pending')
            return True

    def retire(self, reason='source_revoked'):
        if type(reason) is not str or not reason or len(reason) > 128:
            raise ValueError('bounded_terrain_retirement_reason_required')
        with self.condition:
            self.stopping, self.pending = True, None
            self._set(state='retired', grid=None, failure=reason)

    def close(self, timeout=23.):
        if not finite(timeout) or not 0 < timeout <= 45.:
            raise ValueError('bounded_terrain_join_required')
        self.retire()
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise TimeoutError('terrain_owner_cleanup_not_confirmed')

    def _run(self):
        service = None
        phase, started = 'terrain_lane_factory', time.monotonic()
        try:
            service = self.factory()
            with self.condition:
                if self.stopping:
                    return
            phase, started = 'terrain_lane_start', time.monotonic()
            service.start()
            with self.condition:
                if self.stopping:
                    return
                self._set(state='ready', session=service.session,
                    child_pid=service.process.pid, failure='no_terrain')
            while True:
                with self.condition:
                    if self.stopping:
                        return
                    pending, self.pending = self.pending, None
                if pending is not None:
                    observations, sequence, received_at = pending
                    phase, started = 'terrain_lane_update', time.monotonic()
                    grid = service.update(observations, sequence=sequence, received_at=received_at)
                    if type(grid) is not TerrainGrid:
                        raise ValueError('complete_native_terrain_grid_required')
                    with self.condition:
                        if self.stopping:
                            return
                        self._set(state='ready', grid=grid, failure=None)
                phase, started = 'terrain_lane_current', time.monotonic()
                grid = service.current()
                with self.condition:
                    if self.stopping:
                        return
                    if service.state != 'ready':
                        raise RuntimeError('native_terrain_source_failed')
                    if grid is None and self.value.grid is not None:
                        self._set(grid=None, failure='terrain_expired')
                    # submit() may have raced with the health check.
                    if self.pending is None:
                        self.condition.wait(self.poll_seconds)
        except BaseException as error:
            record_fault(phase, error, started_at=started)
            with self.condition:
                if not self.stopping:
                    self._set(state='failed', grid=None, failure='terrain_lane_failed')
                    self.stopping, self.pending = True, None
        finally:
            if service is not None:
                started = time.monotonic()
                try:
                    service.close()
                except BaseException as error:
                    record_fault('terrain_lane_cleanup', error, started_at=started)
                    with self.condition:
                        self._set(state='failed', grid=None, failure='terrain_cleanup_failed')
