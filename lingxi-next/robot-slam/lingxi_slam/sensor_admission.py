"""Close callback admission before draining a bounded sensor worker."""
import math
import threading
import time

from .worker import SensorWorker


class SensorAdmission:
    def __init__(self, worker, *, start=None):
        if (not isinstance(worker, SensorWorker) or worker.snapshot()["state"] != "new"
                or start is not None and not callable(start)):
            raise ValueError("new_sensor_worker_and_optional_starter_required")
        self.worker, self.starter = worker, worker.start if start is None else start
        self.lock = threading.Lock()
        self.started = self.closed = False
        self.rejected_after_close = 0

    def start(self):
        with self.lock:
            if self.started or self.closed:
                raise RuntimeError("sensor_admission_cannot_restart")
            self.starter()
            self.started = True

    def _offer(self, callback, message):
        # Closing waits for any callback already snapshotting its message.
        with self.lock:
            if not self.started or self.closed:
                self.rejected_after_close += 1
                return False
            return callback(message)

    def offer_cloud(self, message):
        return self._offer(self.worker.offer_cloud, message)

    def offer_imu(self, message):
        return self._offer(self.worker.offer_imu, message)

    def close(self):
        with self.lock:
            self.closed = True

    def drain(self, *, seconds=.5, cancelled=lambda: False):
        if (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or not 0 < seconds <= .5 or not callable(cancelled)):
            raise ValueError("bounded_sensor_drain_required")
        with self.lock:
            if not self.closed or not self.started:
                raise RuntimeError("closed_started_sensor_admission_required")
        deadline = time.monotonic() + seconds
        while True:
            if cancelled():
                raise RuntimeError("sensor_drain_cancelled")
            state = self.worker.snapshot()
            if state["state"] != "running" or state["failure"]:
                raise RuntimeError("sensor_worker_failed_before_drain")
            if not state["queuedMessages"] and not state["inFlightMessages"]:
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("sensor_input_drain_deadline")
            time.sleep(min(.002, remaining))
