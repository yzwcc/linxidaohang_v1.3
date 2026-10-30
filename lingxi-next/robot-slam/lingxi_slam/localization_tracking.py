"""Session-bound diagnostic tracking over corrected odometry; no motion or TF."""
from dataclasses import dataclass
import math
import threading
import time

import numpy as np

from .point_data import arrays
from .process_protocol import session_id
from .seeded_localization import RegistrationEstimate, rigid_pose, sensor_scan_from_world


@dataclass(frozen=True)
class TrackingSnapshot:
    state: str
    generation: int
    consecutive_matches: int
    last_frame: int
    reason: str
    map_from_sensor: object
    localization_ready: bool = False
    navigation_ready: bool = False


class LocalizationTracker:
    """One input session and map; a lost track requires an explicit new seed.

    Tracking is a local diagnostic status. It does not certify calibration,
    unique/global localization, base alignment, or robot navigation readiness.
    The caller must keep native registration in a supervised child process.
    """

    def __init__(self, registrar, *, session, input_mode, clock=time.monotonic,
                 confirmations=3, maximum_age_seconds=1., maximum_gap_seconds=.5):
        self.session = session_id(session)
        if (not callable(getattr(registrar, "register", None)) or not callable(clock)
                or input_mode not in ("live", "recorded", "synthetic")
                or type(confirmations) is not int or not 2 <= confirmations <= 20
                or any(type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= 5
                       for value in (maximum_age_seconds, maximum_gap_seconds))):
            raise ValueError("bounded_explicit_localization_tracking_configuration_required")
        self.registrar, self.input_mode, self.clock = registrar, input_mode, clock
        self.confirmations = confirmations
        self.maximum_age, self.maximum_gap = maximum_age_seconds, maximum_gap_seconds
        self.owner, self.generation = threading.get_ident(), 0
        self.state, self.reason, self.matches = "unseeded", "explicit_seed_required", 0
        self.frame, self.stamp = 0, None
        self.minimum_frame, self.minimum_stamp = 0, 0.
        self.map_from_odom, self.pose = None, None
        self.last_received, self.last_clock = None, None

    def _now(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("single_localization_tracking_owner_required")
        now = self.clock()
        if (type(now) not in (float, int) or not math.isfinite(now) or now < 0
                or (self.last_clock is not None and now < self.last_clock)):
            self._lose("invalid_or_reversed_monotonic_clock")
            raise ValueError("nondecreasing_localization_clock_required")
        self.last_clock = now
        return now

    def _lose(self, reason):
        self.state, self.reason, self.matches = "lost", reason, 0
        self.map_from_odom, self.pose = None, None

    def begin(self, map_from_sensor, reference_packet, *, received_at):
        now = self._now()
        # Revoke the previous generation even if the replacement seed is invalid.
        self.generation += 1
        self._lose("invalid_replacement_seed")
        seed = rigid_pose(map_from_sensor)
        _, odom_from_sensor = arrays(reference_packet)
        if not self._fresh(received_at, now):
            raise ValueError("fresh_reference_packet_required")
        self.map_from_odom = seed @ np.linalg.inv(odom_from_sensor)
        self.state, self.reason = "acquiring", "confirmation_required"
        self.frame, self.stamp = 0, None
        self.minimum_frame, self.minimum_stamp = (
            reference_packet.native_frame_id, reference_packet.algorithm_stamp)
        self.last_received = received_at
        return self.generation

    def _fresh(self, received_at, now):
        return (type(received_at) in (float, int) and math.isfinite(received_at)
                and 0 <= received_at <= now and now - received_at <= self.maximum_age)

    def observe(self, packet, *, session, generation, received_at):
        now = self._now()
        self._expire(now)
        if (session != self.session or type(generation) is not int or generation != self.generation
                or self.state not in ("acquiring", "tracking")):
            return self._snapshot()
        try:
            world, odom_from_sensor = arrays(packet)
        except (ValueError, AttributeError, TypeError):
            self._lose("invalid_corrected_point_input")
            return self._snapshot()
        if (packet.native_frame_id < self.minimum_frame
                or packet.algorithm_stamp < self.minimum_stamp):
            return self._snapshot()
        if not self._fresh(received_at, now):
            self._lose("stale_corrected_point_input")
            return self._snapshot()
        if packet.native_frame_id <= self.frame or (
                self.stamp is not None and packet.algorithm_stamp <= self.stamp):
            # Duplicate/late callbacks cannot refresh the freshness deadline.
            return self._snapshot()
        previous_stamp = self.minimum_stamp if self.stamp is None else self.stamp
        if (packet.algorithm_stamp - previous_stamp > self.maximum_gap
                or self.last_received is not None and received_at < self.last_received):
            self._lose("corrected_point_time_gap_or_reversal")
            return self._snapshot()
        try:
            scan = sensor_scan_from_world(world, odom_from_sensor)
            seed = self.map_from_odom @ odom_from_sensor
            estimate = self.registrar.register(scan, seed)
            finished = self._now()
            if generation != self.generation or self.state not in ("acquiring", "tracking"):
                return self._snapshot()
            if not self._fresh(received_at, finished):
                self._lose("registration_completed_after_freshness_deadline")
            elif (not isinstance(estimate, RegistrationEstimate) or estimate.accepted is not True
                  or estimate.reasons):
                self._lose("scan_map_registration_rejected")
            else:
                accepted = rigid_pose(estimate.map_from_sensor)
                accepted.flags.writeable = False
                self.map_from_odom = accepted @ np.linalg.inv(odom_from_sensor)
                self.frame, self.stamp = packet.native_frame_id, packet.algorithm_stamp
                self.last_received, self.matches = received_at, min(self.confirmations, self.matches + 1)
                if self.matches >= self.confirmations:
                    self.state, self.reason, self.pose = "tracking", "diagnostic_quality_only", accepted
                else:
                    self.state, self.reason, self.pose = "acquiring", "confirmation_required", None
        except Exception:
            if generation == self.generation and self.state in ("acquiring", "tracking"):
                self._lose("registration_exception")
        return self._snapshot()

    def _expire(self, now):
        if (self.state in ("acquiring", "tracking") and self.last_received is not None
                and now - self.last_received > self.maximum_age):
            self._lose("corrected_point_stream_expired")

    def snapshot(self):
        self._expire(self._now())
        return self._snapshot()

    def _snapshot(self):
        # Give callers their own array so even deliberate mutation cannot alter state.
        pose = self.pose.copy() if self.pose is not None else None
        if pose is not None:
            pose.flags.writeable = False
        return TrackingSnapshot(self.state, self.generation, self.matches,
                                self.frame, self.reason, pose)

    def stop(self):
        self._now()
        self.generation += 1
        self.state, self.reason, self.matches = "stopped", "explicit_stop", 0
        self.map_from_odom, self.pose = None, None
        return self._snapshot()
