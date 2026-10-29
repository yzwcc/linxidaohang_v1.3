"""Shared admission/drain boundary for recorded and passive mapping input."""
from .mapping_worker import MappingWorker
from .sensor_admission import SensorAdmission


class MappingInput:
    def __init__(self, controller):
        if not isinstance(controller, MappingWorker):
            raise ValueError("new_mapping_controller_required")
        self.controller = controller
        self.admission = SensorAdmission(controller.worker, start=controller.start)

    def finish(self, reason, *, save=False, name=None, cancelled=lambda: False):
        self.admission.close()
        try:
            if type(save) is not bool or not callable(cancelled):
                raise ValueError("explicit_mapping_completion_required")
            if reason != "requested_stop" or not self.admission.started or cancelled():
                self.controller.cancel_save()
                return self.controller.stop()
            self.admission.drain(cancelled=cancelled)
            if cancelled():
                raise RuntimeError("mapping_completion_cancelled")
            return self.controller.stop(save=save, name=name)
        except BaseException:
            self.controller.cancel_save()
            self.controller.stop()
            raise
