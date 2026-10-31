"""Explicit observed-mapper extension; a legacy map never silently gains ray provenance."""
import ctypes
import os
from pathlib import Path

from .async_mapping import AsyncMappingLibrary
from .observation_archive import inspect_observations, sha256


class ObservedMappingLibrary(AsyncMappingLibrary):
    # Receipt engine remains compatible; the independently verified archive is additive.
    def __init__(self, library):
        super().__init__(library)
        self.observation_report = None
        for name in ("enable", "shutdown"):
            function = getattr(self.library, "lingxi_observation_" + name)
            function.argtypes, function.restype = [], ctypes.c_int
        self.library.lingxi_observation_save.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64)]
        self.library.lingxi_observation_save.restype = ctypes.c_int
        self.library.lingxi_observation_error.argtypes = []
        self.library.lingxi_observation_error.restype = ctypes.c_char_p

    def _observation_call(self, name, *args):
        if getattr(self.library, "lingxi_observation_" + name)(*args) < 0:
            raise RuntimeError(self.library.lingxi_observation_error().decode("utf-8", errors="replace"))

    def call(self, name, *args):
        self._owner()
        if name == "open":
            if args or self.opened or self.closed:
                raise RuntimeError("one_async_mapping_open_required")
            self._observation_call("enable")
        try:
            return super().call(name, *args)
        finally:
            if name == "shutdown":
                self._observation_call("shutdown")

    def _finish(self, map_path, points_path, result):
        super()._finish(map_path, points_path, result)
        target = Path(os.fsdecode(map_path)).parent / "private-observations.bin"
        counts = (ctypes.c_uint64 * 4)()
        self._observation_call("save", os.fsencode(target), counts)
        os.chmod(target, 0o600)
        report = inspect_observations(target.absolute(), sha256(target))
        snapshot = self.snapshot()
        if (list(counts) != [report[key] for key in ("submaps", "scans", "returns", "revision")]
                or snapshot is None or report["revision"] != snapshot.revision
                or report["submaps"] != self.final_counts["submaps"]
                or report["scans"] != self.final_counts["savedFrames"]):
            raise ValueError("every_saved_frame_needs_complete_observation_provenance")
        self.observation_report = report
