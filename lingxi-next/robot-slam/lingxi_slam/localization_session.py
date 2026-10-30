"""Thread-safe localization requests; native work remains on the point worker."""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import math
import re
import threading
import time

import numpy as np

from .localization_tracking import LocalizationTracker
from .point_data import arrays, MAXIMUM_FRAME_ID
from .point_output import PointPacket
from .process_protocol import session_id
from .seeded_localization import rigid_pose


class LocalizationControlError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class LocalizationBinding:
    """Identities supplied by the trusted, integrity-checking runtime owner.

    Hash equality binds a session; it does not itself verify a map or profile.
    Map loading and process supervision remain the caller's responsibility.
    """

    session: str
    input_mode: str
    map_id: str
    receipt_sha256: str
    points_sha256: str
    profile_sha256: str

    def __post_init__(self):
        session_id(self.session)
        if (self.input_mode not in ("live", "recorded", "synthetic")
                or type(self.map_id) is not str
                or re.fullmatch(r"slam-[0-9a-f]{32}", self.map_id) is None
                or any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
                       for value in (self.receipt_sha256, self.points_sha256, self.profile_sha256))):
            raise ValueError("explicit_map_profile_and_input_binding_required")


class LocalizationSession:
    """One immutable binding, one native owner and one replacement request slot.

    Requests revoke published results immediately, without waiting for native
    registration. Only the point worker calls open_worker/tick/observe/close_worker.
    A blocked native call still requires the enclosing process supervisor.
    """

    def __init__(self, binding, registrar_factory, *, clock=time.monotonic,
                 maximum_age_seconds=1.):
        if (not isinstance(binding, LocalizationBinding) or not callable(registrar_factory)
                or not callable(clock) or type(maximum_age_seconds) not in (float, int)
                or not math.isfinite(maximum_age_seconds) or not 0 < maximum_age_seconds <= 5):
            raise ValueError("bounded_localization_session_configuration_required")
        self.binding, self.factory, self.clock = binding, registrar_factory, clock
        self.maximum_age = maximum_age_seconds
        self.lock = threading.Lock()
        self.owner, self.tracker, self.registrar, self.latest = None, None, None, None
        self.worker_ready, self.worker_closed, self.accepting = False, False, True
        self.cleanup_complete = False
        self.generation, self.active_generation, self.tracking_generation = 0, None, None
        self.pending, self.last_clock, self.freshness_at = None, None, None
        self.phase, self.reason = "unseeded", "explicit_seed_required"
        self.pose, self.matches, self.last_frame, self.reference = None, 0, 0, None

    def _revoke_locked(self, state, reason):
        self.generation += 1
        self.phase, self.reason = state, reason
        self.pose, self.matches, self.last_frame, self.freshness_at = None, 0, 0, None
        self.pending = (self.generation, "revoke", None, None)

    def _now_locked(self):
        try:
            now = self.clock()
            valid = (type(now) in (float, int) and math.isfinite(now) and now >= 0
                     and (self.last_clock is None or now >= self.last_clock))
        except Exception:
            valid = False
        if not valid:
            self._revoke_locked("failed", "nondecreasing_localization_clock_required")
            self.accepting = False
            raise LocalizationControlError("localization_clock_failed", 503)
        self.last_clock = now
        return now

    def _expire_locked(self, now):
        if (self.phase in ("acquiring", "tracking") and self.freshness_at is not None
                and now - self.freshness_at > self.maximum_age):
            self._revoke_locked("lost", "corrected_point_stream_expired")

    def _state_locked(self, now):
        reference = self.reference
        return {
            "binding": asdict(self.binding), "generation": self.generation,
            "state": self.phase, "reason": self.reason,
            "requestPending": self.pending is not None,
            "workerReady": self.worker_ready, "workerClosed": self.worker_closed,
            "cleanupComplete": self.cleanup_complete,
            "consecutiveMatches": self.matches, "lastFrame": self.last_frame,
            "referenceFrame": (reference[0] if reference is not None
                               and 0 <= now - reference[1] <= self.maximum_age else None),
            "mapFromSensor": deepcopy(self.pose),
            "coordinateUnits": "native-raw", "sourceFrame": "IMU",
            "localizationReady": False, "navigationReady": False,
        }

    def state(self):
        """Read cached status only, including expiry if the worker is blocked."""
        with self.lock:
            try:
                now = self._now_locked()
                self._expire_locked(now)
            except LocalizationControlError:
                now = self.last_clock if self.last_clock is not None else 0.
            return self._state_locked(now)

    def request(self, action, binding, generation, *, seed=None, reference_frame=None):
        # Validate malformed requests before touching the current session.
        if (action not in ("seed", "pause", "stop") or type(binding) is not dict
                or type(generation) is not int or generation < 0):
            raise LocalizationControlError("invalid_localization_request", 400)
        if action == "seed":
            if (type(reference_frame) is not int or not 1 <= reference_frame <= MAXIMUM_FRAME_ID):
                raise LocalizationControlError("invalid_localization_reference", 400)
            try:
                seed = rigid_pose(seed)
            except (ValueError, TypeError):
                raise LocalizationControlError("invalid_localization_seed", 400) from None
            seed.flags.writeable = False
        elif seed is not None or reference_frame is not None:
            raise LocalizationControlError("invalid_localization_request", 400)
        with self.lock:
            now = self._now_locked()
            self._expire_locked(now)
            if binding != asdict(self.binding):
                raise LocalizationControlError("localization_binding_changed")
            if generation != self.generation:
                raise LocalizationControlError("localization_generation_changed")
            if not self.accepting:
                raise LocalizationControlError("localization_session_closed")
            if action == "seed" and not self.worker_ready:
                raise LocalizationControlError("localization_worker_not_ready", 503)
            self._revoke_locked(
                {"seed": "seed_requested", "pause": "paused", "stop": "stopped"}[action],
                {"seed": "worker_seed_confirmation_required", "pause": "explicit_seed_required",
                 "stop": "explicit_stop"}[action])
            self.pending = (self.generation, action, (seed, reference_frame), now)
            if action == "stop":
                self.accepting = False
            return self._state_locked(now)

    def open_worker(self):
        with self.lock:
            if self.owner is not None or self.worker_closed:
                raise RuntimeError("localization_worker_cannot_restart")
            self.owner = threading.get_ident()
        try:
            self.registrar = self.factory()
            self.tracker = LocalizationTracker(
                self.registrar, session=self.binding.session, input_mode=self.binding.input_mode,
                clock=self.clock, maximum_age_seconds=self.maximum_age)
            with self.lock:
                self.worker_ready = True
            self.tick()
        except BaseException:
            self.close_worker(failed=True)
            raise
        return self

    def _owner(self):
        if threading.get_ident() != self.owner or self.worker_closed:
            raise RuntimeError("active_localization_worker_owner_required")

    def _publish(self, generation, result):
        with self.lock:
            now = self._now_locked()
            if generation != self.generation or self.pending is not None:
                return
            self.phase, self.reason = result.state, result.reason
            self.matches, self.last_frame = result.consecutive_matches, result.last_frame
            self.pose = None if result.map_from_sensor is None else result.map_from_sensor.tolist()
            self.freshness_at = self.tracker.last_received
            self._expire_locked(now)

    def tick(self):
        """Drain one latest request and expire tracking, even without new points."""
        self._owner()
        with self.lock:
            now = self._now_locked()
            self._expire_locked(now)
            command, self.pending = self.pending, None
        if command is not None:
            generation, action, payload, requested_at = command
            self.tracker.stop()
            self.active_generation = self.tracking_generation = None
            if action == "seed":
                seed, reference_frame = payload
                reference = self.latest
                if (reference is None or reference[0].native_frame_id != reference_frame
                        or not 0 <= now - reference[1] <= self.maximum_age
                        or not 0 <= now - requested_at <= self.maximum_age):
                    with self.lock:
                        if generation == self.generation:
                            self.phase, self.reason = "lost", "fresh_matching_seed_reference_required"
                    return
                try:
                    internal = self.tracker.begin(seed, reference[0], received_at=reference[1])
                except Exception:
                    with self.lock:
                        if generation == self.generation:
                            self.phase, self.reason = "lost", "seed_application_failed"
                    return
                self.active_generation, self.tracking_generation = generation, internal
                self._publish(generation, self.tracker.snapshot())
            else:
                self.latest = None
                with self.lock:
                    # A newer command may already be waiting; only clear the old reference.
                    self.reference = None
        if self.active_generation is not None:
            self._publish(self.active_generation, self.tracker.snapshot())

    def observe(self, packet, *, session, input_mode, received_at):
        self._owner()
        self.tick()
        if session != self.binding.session or input_mode != self.binding.input_mode:
            return self.state()
        with self.lock:
            processing_generation = self.generation
            if not self.accepting:
                return self._state_locked(self._now_locked())
        try:
            if not isinstance(packet, PointPacket):
                raise ValueError("corrected_point_packet_required")
            xyz, pose = arrays(packet)
            with self.lock:
                now = self._now_locked()
                if (type(received_at) not in (float, int) or not math.isfinite(received_at)
                        or not 0 <= now - received_at <= self.maximum_age):
                    raise ValueError("fresh_corrected_point_required")
            # Keep one owned seed reference, never a backlog of old commands or scans.
            if self.latest is None or (
                    packet.native_frame_id > self.latest[0].native_frame_id
                    and packet.algorithm_stamp > self.latest[0].algorithm_stamp):
                xyz, pose = xyz.copy(), pose.copy()
                xyz.flags.writeable = pose.flags.writeable = False
                self.latest = (replace(packet, points=xyz, world_lidar_pose=pose), received_at)
                with self.lock:
                    self.reference = (packet.native_frame_id, received_at)
        except (ValueError, AttributeError, TypeError):
            self.latest = None
            with self.lock:
                self.reference = None
                if (processing_generation == self.generation
                        and self.phase in ("seed_requested", "acquiring", "tracking")):
                    self._revoke_locked("lost", "invalid_or_stale_corrected_point_input")
            return self.state()
        with self.lock:
            generation = self.active_generation
            active = generation == self.generation and self.phase in ("acquiring", "tracking")
        if active:
            result = self.tracker.observe(
                packet, session=session, generation=self.tracking_generation, received_at=received_at)
            self._publish(generation, result)
        return self.state()

    def close_worker(self, *, failed=False):
        if threading.get_ident() != self.owner:
            raise RuntimeError("same_localization_worker_cleanup_owner_required")
        with self.lock:
            if self.worker_closed:
                return
            self.accepting = self.worker_ready = False
            self.worker_closed = True
            self._revoke_locked("failed" if failed else "stopped",
                                "localization_worker_failed" if failed else "worker_closed")
            self.pending = self.reference = None
        # Drop native resources on their owner thread, including failed initialization.
        self.latest = None
        tracker, self.tracker = self.tracker, None
        registrar, self.registrar = self.registrar, None
        try:
            try:
                if tracker is not None:
                    tracker.stop()
            finally:
                close = getattr(registrar, "close", None)
                if callable(close):
                    close()
        except BaseException:
            with self.lock:
                self.phase, self.reason = "failed", "localization_worker_cleanup_failed"
            raise
        else:
            # Do not advertise cleanup while a close callback is still executing.
            tracker = registrar = None
            with self.lock:
                self.cleanup_complete = True


def dispatch(session, value):
    """Strict JSON-shaped management contract; no sockets or native calls here."""
    if type(value) is not dict or type(value.get("action")) is not str:
        raise LocalizationControlError("invalid_localization_request", 400)
    action = value["action"]
    if action == "state" and set(value) == {"action"}:
        return session.state()
    fields = {"action", "binding", "expectedGeneration"}
    if action == "seed":
        fields |= {"mapFromSensor", "referenceFrame"}
    if action not in ("seed", "pause", "stop") or set(value) != fields:
        raise LocalizationControlError("invalid_localization_request", 400)
    seed = value.get("mapFromSensor")
    if action == "seed":
        if (type(seed) is not list or len(seed) != 4 or any(
                type(row) is not list or len(row) != 4
                or any(type(number) not in (int, float) for number in row) for row in seed)):
            raise LocalizationControlError("invalid_localization_seed", 400)
        try:
            seed = np.array(seed, dtype=np.float64)
        except (ValueError, TypeError, OverflowError):
            raise LocalizationControlError("invalid_localization_seed", 400) from None
    return session.request(action, value["binding"], value["expectedGeneration"],
                           seed=seed, reference_frame=value.get("referenceFrame"))
