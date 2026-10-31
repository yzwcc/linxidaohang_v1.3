"""Bounded callback transport; native creation, work and cleanup have one owner."""
from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass
import math
from pathlib import Path
import sys
import threading
import time
import traceback

from .messages import SensorProfile, cloud_snapshot, decode_cloud, imu_snapshot
from .stream import Limits, SensorStream


@dataclass(frozen=True)
class WorkerLimits:
    maximum_messages: int = 4096
    maximum_clouds: int = 16
    maximum_payload_bytes: int = 64 * 1024 * 1024
    maximum_cloud_payload_bytes: int = 8 * 1024 * 1024
    maximum_queue_age_seconds: float = .5
    maximum_processing_seconds: float = .75
    tick_seconds: float = .05
    shutdown_seconds: float = 3.

    def __post_init__(self):
        for name, value in asdict(self).items():
            valid = (type(value) in (int, float) and math.isfinite(value) and value > 0
                     if name.endswith("_seconds") else type(value) is int and value > 0)
            if not valid:
                raise ValueError("finite_positive_worker_limits_required")
        if (self.tick_seconds >= self.maximum_processing_seconds
                or self.maximum_cloud_payload_bytes > self.maximum_payload_bytes):
            raise ValueError("consistent_worker_limits_required")


class SensorWorker:
    """Callbacks only snapshot/enqueue. A failed/stopped instance never restarts."""

    def __init__(self, native_factory, profile, hypothesis, *, limits=None, stream_limits=None,
                 on_frame=None, clock=time.monotonic):
        self.limits = WorkerLimits() if limits is None else limits
        self.stream_limits = Limits() if stream_limits is None else stream_limits
        if (not isinstance(self.limits, WorkerLimits) or not isinstance(self.stream_limits, Limits)
                or not isinstance(profile, SensorProfile) or not callable(native_factory)
                or not callable(clock) or (on_frame is not None and not callable(on_frame))
                or hypothesis not in ("header_is_scan_start", "header_is_scan_end")):
            raise ValueError("explicit_validated_worker_configuration_required")
        self.native_factory, self.profile, self.hypothesis = native_factory, profile, hypothesis
        self.on_frame, self.clock = on_frame, clock
        self.condition = threading.Condition()
        self.queue, self.thread = deque(), None
        self.state, self.failure = "new", None
        self.pending_bytes = self.pending_clouds = self.inflight = 0
        self.started_at = self.progress_at = self.last_clock = None
        self.stream_summary = None
        self.maximum_queue_age = 0.
        self.counts = dict.fromkeys((
            "acceptedImu", "acceptedClouds", "completedMessages", "discardedMessages", "rejectedMessages",
            "maximumQueuedMessages", "maximumQueuedClouds", "maximumQueuedPayloadBytes",
        ), 0)

    def _now_locked(self):
        try:
            now = self.clock()
        except Exception:
            self._fail_locked("monotonic_worker_clock_required")
            return None
        if (type(now) not in (int, float) or not math.isfinite(now)
                or (self.last_clock is not None and now < self.last_clock)):
            self._fail_locked("monotonic_worker_clock_required")
            return None
        self.last_clock = now
        return now

    def _fail_locked(self, reason):
        if self.state in ("stopped", "failed"):
            return
        self.state, self.failure = "failed", reason
        self.counts["discardedMessages"] += len(self.queue)
        self.queue.clear()
        self.pending_bytes = self.pending_clouds = 0
        self.condition.notify_all()

    def _health_locked(self, now):
        if now is None:
            return
        if self.state == "starting" and now - self.started_at > self.stream_limits.initial_wait_seconds:
            self._fail_locked("native_startup_deadline_exceeded")
        elif (self.state == "running" and
              now - self.progress_at > self.limits.maximum_processing_seconds):
            self._fail_locked("worker_progress_deadline_exceeded")
        if (self.state in ("starting", "running") and self.queue
                and now - self.queue[0][2] > self.limits.maximum_queue_age_seconds):
            self._fail_locked("callback_queue_age_exceeded")

    def start(self):
        with self.condition:
            if self.state != "new":
                raise RuntimeError("worker_instance_cannot_restart")
            now = self._now_locked()
            if now is None:
                raise RuntimeError("monotonic_worker_clock_required")
            self.started_at = self.progress_at = now
            self.state = "starting"
            self.thread = threading.Thread(target=self._run, name="lingxi-slam-input", daemon=True)
            try:
                self.thread.start()
            except Exception:
                self.thread = None
                self._fail_locked("worker_thread_start_failed")
                raise RuntimeError("worker_thread_start_failed") from None
        return self

    def _offer(self, kind, message):
        with self.condition:
            now = self._now_locked()
            self._health_locked(now)
            if self.state not in ("starting", "running"):
                self.counts["rejectedMessages"] += 1
                return False
            if len(self.queue) >= self.limits.maximum_messages:
                self._fail_locked("callback_queue_capacity_exceeded")
                self.counts["rejectedMessages"] += 1
                return False
            try:
                if kind == "cloud":
                    if self.pending_clouds >= self.limits.maximum_clouds:
                        raise ValueError("cloud_queue_capacity")
                    available = min(self.limits.maximum_cloud_payload_bytes,
                                    self.limits.maximum_payload_bytes - self.pending_bytes)
                    packet = cloud_snapshot(message, self.profile,
                                            self.stream_limits.maximum_points_per_cloud, available)
                else:
                    packet = imu_snapshot(message, self.profile)
                if self.pending_bytes + packet.payload_bytes > self.limits.maximum_payload_bytes:
                    raise ValueError("payload_queue_capacity")
            except Exception:
                self._fail_locked("invalid_or_oversized_sensor_packet")
                self.counts["rejectedMessages"] += 1
                return False
            self.queue.append((kind, packet, now))
            self.pending_bytes += packet.payload_bytes
            self.pending_clouds += kind == "cloud"
            self.counts["acceptedClouds" if kind == "cloud" else "acceptedImu"] += 1
            for name, value in (
                ("maximumQueuedMessages", len(self.queue)), ("maximumQueuedClouds", self.pending_clouds),
                ("maximumQueuedPayloadBytes", self.pending_bytes),
            ):
                self.counts[name] = max(self.counts[name], value)
            self.condition.notify()
            return True

    def offer_cloud(self, message):
        return self._offer("cloud", message)

    def offer_imu(self, message):
        return self._offer("imu", message)

    def _frame(self, *values):
        with self.condition:
            self._health_locked(self._now_locked())
            active = self.state == "running"
        if active and self.on_frame is not None:
            self.on_frame(*values)

    def _run(self):
        native = stream = None
        try:
            native = self.native_factory()
            stream = SensorStream(native, self.hypothesis, limits=self.stream_limits, on_frame=self._frame)
            stream.tick(self.started_at)
            with self.condition:
                self._health_locked(self._now_locked())
                if self.state == "starting":
                    self.state = "running"
                    self.progress_at = self.last_clock
            while True:
                with self.condition:
                    self._health_locked(self._now_locked())
                    if self.state != "running":
                        break
                    if not self.queue:
                        self.condition.wait(self.limits.tick_seconds)
                    now = self._now_locked()
                    self._health_locked(now)
                    if self.state != "running":
                        break
                    if self.queue:
                        kind, packet, arrival = self.queue.popleft()
                        self.pending_bytes -= packet.payload_bytes
                        self.pending_clouds -= kind == "cloud"
                        self.inflight = 1
                        self.maximum_queue_age = max(self.maximum_queue_age, now - arrival)
                    else:
                        packet = None
                try:
                    if packet is None:
                        # Capture the tick under the queue lock so later arrivals cannot precede it.
                        stream.tick(now)
                    elif kind == "imu":
                        stream.push_imu(packet.stamp_ns, packet.acceleration, packet.angular, arrival)
                    else:
                        points, offsets = decode_cloud(packet)
                        stream.push_cloud(packet.stamp_ns, points, offsets, arrival)
                except BaseException:
                    with self.condition:
                        if packet is not None:
                            self.counts["discardedMessages"] += 1
                            self.inflight = 0
                    raise
                with self.condition:
                    if packet is not None:
                        self.counts["completedMessages"] += 1
                        self.inflight = 0
                    now = self._now_locked()
                    self._health_locked(now)
                    self.progress_at = now
                    self.stream_summary = stream.snapshot()
                packet = None
        except BaseException as error:
            origin = traceback.extract_tb(error.__traceback__)[-1]
            # Private stderr includes code location, never exception text or sensor values.
            print(f"sensor_worker_failure:{type(error).__name__}:"
                  f"{Path(origin.filename).name}:{origin.lineno}", file=sys.stderr, flush=True)
            if stream is not None:
                print(f"sensor_stream_failure:{stream.failure}", file=sys.stderr, flush=True)
            with self.condition:
                self._fail_locked("sensor_stream_or_native_failed")
        finally:
            try:
                if stream is not None:
                    if stream.state == "running":
                        stream.finish()
                    with self.condition:
                        self.stream_summary = stream.snapshot()
            except BaseException:
                with self.condition:
                    self._fail_locked("stream_cleanup_failed")
            try:
                if native is not None:
                    native.close()
            except BaseException:
                with self.condition:
                    self._fail_locked("native_cleanup_failed")
            with self.condition:
                if self.state not in ("failed", "stopped"):
                    self.state = "stopped"
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            self._health_locked(self._now_locked())
            return {
                "state": self.state, "failure": self.failure,
                "threadAlive": self.thread is not None and self.thread.is_alive(),
                "counts": self.counts.copy(), "queuedMessages": len(self.queue),
                "queuedClouds": self.pending_clouds, "queuedPayloadBytes": self.pending_bytes,
                "inFlightMessages": self.inflight, "maximumObservedQueueAgeSeconds": self.maximum_queue_age,
                "limits": asdict(self.limits), "stream": deepcopy(self.stream_summary),
            }

    def stop(self):
        if self.thread is threading.current_thread():
            raise RuntimeError("external_owner_must_join_worker")
        with self.condition:
            if self.state == "new":
                self.state = "stopped"
            elif self.state in ("starting", "running"):
                self.state = "stopping"
                self.counts["discardedMessages"] += len(self.queue)
                self.queue.clear()
                self.pending_bytes = self.pending_clouds = 0
                self.condition.notify_all()
            thread = self.thread
        if thread is not None:
            thread.join(self.limits.shutdown_seconds)
            if thread.is_alive():
                with self.condition:
                    self._fail_locked("worker_shutdown_deadline_exceeded")
        return self.snapshot()
