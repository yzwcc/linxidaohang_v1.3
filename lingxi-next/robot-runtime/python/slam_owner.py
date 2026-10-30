"""Reuse the proven mapping owner for one shared SLAM service and paired API."""
import os
from pathlib import Path

from mapping_owner import ManagedMappingOwner


class ManagedSlamOwner(ManagedMappingOwner):
    service_name = "lingxi-slam"
    continuous = True

    def __init__(self, *args, maps, output, api_environment, **kwargs):
        if (api_environment.get("LINGXI_PORT") == "3000"
                or set(api_environment) & {"LINGXI_LOCALIZATION_SOCKET", "LINGXI_LOCALIZATION_SERVICE",
                                   "LINGXI_LOCALIZATION_PARENT_STDIN"}
                or len(os.fsencode(Path(output) / "r" / ("a" * 32) / "localization.sock")) >= 100):
            raise ValueError("owned_short_slam_runtime_configuration_required")
        super().__init__(*args, maps=maps, output=output, api_environment=api_environment, **kwargs)
        self.runs = self.output / "r"
        self.runs.mkdir(mode=0o700)
        self.localization_endpoint = self.output / "localization.sock"

    def _service_arguments(self):
        return [*super()._service_arguments(), "--runs", str(self.runs),
                "--localization-socket", str(self.localization_endpoint)]

    def _api_configuration(self):
        return {**super()._api_configuration(), "LINGXI_LOCALIZATION_SERVICE": "1",
                "LINGXI_LOCALIZATION_SOCKET": str(self.localization_endpoint)}
