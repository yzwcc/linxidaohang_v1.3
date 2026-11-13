"""One registration owner with a replaceable frame slot, separate from LIO input."""
from collections import deque
from dataclasses import replace
from copy import deepcopy
import threading
import time

from .point_data import arrays
from .acquisition_output import EncodedAcquisition


class LocalizationLane:
    """Coalesce corrected frames only; never drop raw LiDAR or IMU messages.

    Original ingress time and generation travel with every frame. Session
    expiry and explicit reseeding still apply when registration falls behind.
    The enclosing child-process supervisor owns hard native-call deadlines.
    """

    def __init__(self, session, *, on_acquisition=None, on_frame=None):
        self.session, self.on_acquisition, self.on_frame = session, on_acquisition, on_frame
        self.condition = threading.Condition()
        self.ready = threading.Event()
        self.pending, self.thread = None, None
        self.closed, self.failed = False, False
        self.offered = self.processed = self.replaced = self.discarded = 0
        self.maximum_seconds = 0.
        self.timings = {name: deque(maxlen=512) for name in (
            'ingressToLane', 'decodeAcquisition', 'registration', 'projection', 'ingressToCapture',
            'nativePreprocess', 'nativeOdometry')}

    def start(self):
        if self.thread is not None:
            raise RuntimeError("localization_lane_cannot_restart")
        self.thread = threading.Thread(target=self._run, name="lingxi-map-registration", daemon=True)
        self.thread.start()
        if not self.ready.wait(10.):
            self.stop()
            raise RuntimeError("localization_lane_startup_deadline")
        self.check()
        return self

    def check(self):
        with self.condition:
            if self.closed or self.failed:
                raise RuntimeError("localization_lane_unavailable")

    def offer(self, packet, received_at, generation, acquisition, metadata):
        # Native output buffers may be reused as soon as the next frame arrives.
        if packet is not None:
            xyz, pose = arrays(packet)
            xyz, pose = xyz.copy(), pose.copy()
            xyz.flags.writeable = pose.flags.writeable = False
            packet = replace(packet, points=xyz, world_lidar_pose=pose)
        metadata = deepcopy(metadata)
        with self.condition:
            if self.closed or self.failed:
                raise RuntimeError("localization_lane_unavailable")
            self.replaced += self.pending is not None
            self.pending = (packet, received_at, generation, acquisition, metadata)
            self.offered += 1
            self.condition.notify()

    def _run(self):
        try:
            self.session.open_worker()
            self.ready.set()
            while True:
                with self.condition:
                    if self.closed:
                        break
                    task, self.pending = self.pending, None
                    if task is None:
                        self.condition.wait(.02)
                if task is None:
                    self.session.tick()
                    continue
                packet, received_at, generation, acquisition, metadata = task
                started = time.monotonic()
                marks = {'ingressToLane': started - received_at}
                self.session.tick()
                if packet is not None:
                    if type(acquisition) is EncodedAcquisition:
                        acquisition = acquisition.decode()
                    decoded = time.monotonic()
                    marks['decodeAcquisition'] = decoded - started
                    self.session.observe(packet, session=self.session.binding.session,
                        input_mode=self.session.binding.input_mode, received_at=received_at,
                        expected_generation=generation)
                    registered_at = time.monotonic()
                    marks['registration'] = registered_at - decoded
                    if acquisition is not None:
                        registered = self.session.register_acquisition(acquisition, packet,
                            received_at=received_at, expected_generation=generation)
                        if registered is not None and self.on_acquisition is not None:
                            self.on_acquisition(registered)
                if self.on_frame is not None:
                    self.on_frame(*metadata)
                completed = time.monotonic()
                marks['ingressToCapture'] = completed - received_at
                if packet is not None:
                    marks['projection'] = completed - registered_at
                statistics = metadata[3]
                if getattr(statistics, 'shape', None) == (4,):
                    marks.update(nativePreprocess=float(statistics[0]) / 1000.,
                                 nativeOdometry=float(statistics[1]) / 1000.)
                with self.condition:
                    self.processed += 1
                    self.maximum_seconds = max(self.maximum_seconds, completed - started)
                    for name, duration in marks.items():
                        self.timings[name].append(duration)
                task = packet = acquisition = metadata = None
        except BaseException:
            with self.condition:
                self.failed = True
            self.session.stop_admission()
        finally:
            self.ready.set()
            try:
                if self.session.owner == threading.get_ident() and not self.session.worker_closed:
                    self.session.close_worker(failed=self.failed)
            except BaseException:
                with self.condition:
                    self.failed = True

    def stop(self):
        # Revocation is immediate even while a native registration is blocked.
        self.session.stop_admission()
        with self.condition:
            self.closed = True
            self.discarded += self.pending is not None
            self.pending = None
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(1.5)
            if self.thread.is_alive():
                self.failed = True
                raise RuntimeError("localization_lane_shutdown_deadline")
        if self.failed:
            raise RuntimeError("localization_lane_failed")

    def summary(self):
        with self.condition:
            def quantiles(values):
                ordered = sorted(values)
                return {'count': len(ordered), 'p50Seconds': ordered[(len(ordered) - 1) // 2],
                    'p95Seconds': ordered[min(len(ordered) - 1, int(len(ordered) * .95))],
                    'maximumSeconds': ordered[-1]} if ordered else {'count': 0}
            return dict(offered=self.offered, processed=self.processed, replaced=self.replaced,
                discarded=self.discarded, pending=int(self.pending is not None),
                closed=self.closed, failed=self.failed,
                threadAlive=self.thread is not None and self.thread.is_alive(),
                maximumProcessingSeconds=self.maximum_seconds,
                recentStageTimings={name: quantiles(values) for name, values in self.timings.items()})
