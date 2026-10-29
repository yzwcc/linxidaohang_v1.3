"""A single mapping session; sensor callbacks never perform save or native work."""
from dataclasses import replace
from pathlib import Path
import threading

from .mapping_session import MappingSession, SaveDecision
from .process_protocol import session_id
from .worker import SensorWorker, WorkerLimits


class MappingWorker:
    def __init__(self, native_factory, mapping_factory, profile, hypothesis, output, *,
                 name, session, input_mode, inspect_map, reload_map, save_timeout_seconds=90.):
        if (type(save_timeout_seconds) not in (float, int)
                or not 5 <= save_timeout_seconds <= 120
                or not callable(mapping_factory)):
            raise ValueError("bounded_mapping_save_configuration_required")
        self.session, self.input_mode = session_id(session), input_mode
        self.output = Path(output)
        if self.output.exists() or self.output.is_symlink():
            raise ValueError("new_mapping_session_output_required")
        self.decision = SaveDecision()
        self.phase, self.native_session = "new", None
        self.phase_lock, self.stop_lock = threading.Lock(), threading.Lock()

        def factory():
            self.native_session = MappingSession(
                native_factory, mapping_factory, self.decision, self.output,
                name=name, session=session, input_mode=input_mode, can_save=self._can_save,
                inspect_map=inspect_map, reload_map=reload_map)
            return self.native_session

        self.worker = SensorWorker(
            factory, profile, hypothesis,
            limits=replace(WorkerLimits(), shutdown_seconds=float(save_timeout_seconds)))

    def _can_save(self):
        state = self.worker.snapshot()
        with self.phase_lock:
            requested = self.phase == "saving"
        return (requested and state["state"] == "stopping" and state["failure"] is None
                and state["stream"] is not None and state["stream"]["state"] == "finished"
                and state["stream"]["failure"] is None)

    def start(self):
        with self.phase_lock:
            if self.phase != "new":
                raise RuntimeError("mapping_session_cannot_restart")
            self.phase = "mapping"
            try:
                self.worker.start()
            except BaseException:
                self.phase = "failed"
                raise
        return self

    def offer_imu(self, message):
        return self.worker.offer_imu(message)

    def offer_cloud(self, message):
        return self.worker.offer_cloud(message)

    def cancel_save(self):
        self.decision.discard()

    def stop(self, *, save=False):
        if type(save) is not bool:
            raise ValueError("explicit_boolean_save_decision_required")
        with self.stop_lock:
            with self.phase_lock:
                if self.phase in ("saved", "stopped"):
                    return self.snapshot_unlocked()
                self.phase = "saving" if save and self.phase == "mapping" else "stopping"
                if self.phase == "saving":
                    self.decision.request_save()
                else:
                    self.decision.discard()
            try:
                terminal = self.worker.stop()
            except BaseException:
                self.decision.discard()
                with self.phase_lock:
                    self.phase = "failed"
                raise
            with self.phase_lock:
                receipt = None if self.native_session is None else self.native_session.receipt
                self.phase = ("failed" if terminal["state"] != "stopped" or terminal["threadAlive"]
                              or terminal["failure"] else "saved" if receipt is not None else "stopped")
                return self.snapshot_unlocked()

    def snapshot_unlocked(self):
        receipt = None if self.native_session is None else self.native_session.receipt
        return {
            "session": self.session, "inputMode": self.input_mode, "phase": self.phase,
            "saved": self.phase == "saved", "counts": None if receipt is None else receipt["counts"],
            "localizationReady": False, "navigationReady": False,
        }

    def snapshot(self):
        # Do not hold the phase lock while acquiring the worker condition.
        worker = self.worker.snapshot()
        with self.phase_lock:
            return {**self.snapshot_unlocked(), "worker": worker}
