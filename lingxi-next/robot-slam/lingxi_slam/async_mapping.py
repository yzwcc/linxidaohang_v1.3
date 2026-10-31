"""Same-owner full GLIM mapping adapter for the product's explicit-save lifecycle."""
import ctypes
from dataclasses import dataclass
import math
import json
import os
from pathlib import Path
import threading
import time

import numpy as np

ENGINE = "glim-full-async-v1"
COUNTERS = ("finalizedFrames", "submaps", "points", "savedFrames", "imuSamples", "globalUpdates",
            "matchingFactors", "nonAdjacentMatchingFactors", "localOptimizations", "maximumGlobalInsertionMs")
WORKER = ("acceptedFrames", "completedFrames", "peakFrames", "frameCapacity", "acceptedImu", "completedImu",
          "peakImu", "imuCapacity", "rejectedTasks", "discardedInputs", "maximumAdmissionMs", "maximumWaitMs",
          "maximumTaskMs")


def compare_corrected_points(current, saved):
    if current.shape != saved.shape or not np.isfinite(saved).all():
        raise ValueError("same_order_finite_corrected_points_required")
    # Core and adapter Eigen transforms may use different SIMD/FMA instructions.
    # This permits double-precision roundoff only, not the much larger reload tolerance.
    error = np.abs(current - saved)
    allowance = 64 * np.finfo(np.float64).eps * np.maximum(1., np.maximum(np.abs(current), np.abs(saved)))
    return {"pointCount": len(current), "maximumCoordinateDifferenceRaw": float(error.max(initial=0)),
            "maximumRoundoffAllowanceRaw": float(allowance.max(initial=0)),
            "samePointOrderCompared": True, "withinDoublePrecisionRoundoff": bool(np.all(error <= allowance))}


def checked_worker(values, *, complete=False):
    values = np.asarray(values)
    if (values.shape != (13,) or not np.isfinite(values).all() or np.any(values < 0)
            or np.any(values[:10] != np.floor(values[:10]))):
        raise ValueError("finite_async_mapping_worker_status_required")
    result = {key: int(value) if i < 10 else float(value) for i, (key, value) in enumerate(zip(WORKER, values))}
    for kind, capacity in (("Frames", 128), ("Imu", 8192)):
        accepted, completed, peak = (result[key + kind] for key in ("accepted", "completed", "peak"))
        if (result["frameCapacity" if kind == "Frames" else "imuCapacity"] != capacity
                or completed > accepted or peak > capacity or accepted - completed > capacity
                or peak > accepted or complete and accepted != completed):
            raise ValueError("bounded_consistent_async_mapping_status_required")
    if result["rejectedTasks"] or result["discardedInputs"]:
        raise RuntimeError("async_mapping_inputs_rejected_or_discarded")
    return result


@dataclass(frozen=True)
class CorrectedMapSnapshot:
    revision: int
    submaps: int
    matching_factors: int
    nonadjacent_factors: int
    corrected_pose_events: int
    points: np.ndarray
    coordinate_frame: str = "lingxi_glim_map"
    kind: str = "globally_corrected_submaps"


class AsyncMappingLibrary:
    """The input/native owner calls this adapter; other threads read cached health only."""
    engine = ENGINE

    def __init__(self, library):
        self.owner = threading.get_ident()
        self.opened = self.closed = self.finishing = False
        self.imu_samples = self.estimated_frames = 0
        self.final_counts = None
        self.preview, self.preview_checked_at = None, 0.
        self.cache_lock, self.cache = threading.Lock(), None
        self.library = ctypes.CDLL(str(library))
        pointer = ctypes.POINTER(ctypes.c_double)
        signatures = {"error": [], "open": [], "imu": [ctypes.c_double, pointer, pointer],
                      "finish": [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, pointer],
                      "shutdown": [], "reload": [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, pointer]}
        for name, arguments in signatures.items():
            function = getattr(self.library, "lingxi_full_" + name)
            function.argtypes = arguments
            function.restype = ctypes.c_char_p if name == "error" else ctypes.c_int
        self.library.lingxi_async_status.argtypes = [pointer]
        self.library.lingxi_async_status.restype = ctypes.c_int
        self.library.lingxi_async_snapshot.argtypes = [pointer, pointer, ctypes.c_size_t]
        self.library.lingxi_async_snapshot.restype = ctypes.c_int

    def _owner(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("same_owner_async_mapping_required")

    def _active(self):
        self._owner()
        if not self.opened or self.closed or self.finishing:
            raise RuntimeError("active_async_mapping_required")

    @staticmethod
    def _pointer(array):
        return array.ctypes.data_as(ctypes.POINTER(ctypes.c_double))

    def _check(self, value):
        if value < 0:
            message = self.library.lingxi_full_error()
            raise RuntimeError(message.decode("utf-8", errors="replace") if message else "async_mapping_native_failure")
        return value

    def call(self, name, *args):
        self._owner()
        if name == "open":
            if args or self.opened or self.closed:
                raise RuntimeError("one_async_mapping_open_required")
            self._check(self.library.lingxi_full_open())
            self.opened = True
        elif name == "finish":
            self._active()
            self.finishing = True
            self._finish(*args)
        elif name == "shutdown":
            if args:
                raise ValueError("argumentless_async_shutdown_required")
            if not self.closed:
                self.closed = True
                try:
                    self._check(self.library.lingxi_full_shutdown())
                finally:
                    if self.preview is not None:
                        self.preview.close()
        else:
            raise ValueError("explicit_async_mapping_lifecycle_operation_required")

    def feed_imu(self, stamp, acceleration, angular):
        self._active()
        acc, gyro = (np.ascontiguousarray(value, dtype=np.float64) for value in (acceleration, angular))
        if (not math.isfinite(stamp) or acc.shape != (3,) or gyro.shape != (3,)
                or not np.isfinite(acc).all() or not np.isfinite(gyro).all()):
            raise ValueError("finite_real_async_imu_sample_required")
        self._check(self.library.lingxi_full_imu(stamp, self._pointer(acc), self._pointer(gyro)))
        self.imu_samples += 1

    def observe_frame(self, result):
        self._active()
        self.check_health()
        if result[0] is not None:
            self.estimated_frames += 1
        now = time.monotonic()
        if self.preview is not None and now - self.preview_checked_at >= .5:
            self.preview_checked_at = now
            snapshot = self.snapshot(after_revision=self.preview.revision)
            if snapshot is not None:
                self.preview.offer(snapshot)

    def start_preview(self, path, session, input_mode):
        self._active()
        if self.preview is not None:
            raise RuntimeError("one_corrected_preview_per_mapping_session_required")
        from .corrected_preview import CorrectedPreviewWriter
        self.preview = CorrectedPreviewWriter(path, session, input_mode)

    def check_health(self, *, complete=False):
        self._owner()
        values = np.zeros(13, dtype=np.float64)
        self._check(self.library.lingxi_async_status(self._pointer(values)))
        status = checked_worker(values, complete=complete)
        with self.cache_lock:
            self.cache = {"engine": ENGINE, "observedAtSeconds": time.monotonic(), **status}
        return status

    def cached_health(self):
        with self.cache_lock:
            return None if self.cache is None else self.cache.copy()

    def snapshot(self, *, after_revision=0):
        self._owner()
        if not self.opened or self.closed:
            raise RuntimeError("open_async_snapshot_reader_required")
        header = np.zeros(6, dtype=np.float64)
        for _ in range(4):
            self._check(self.library.lingxi_async_snapshot(self._pointer(header), None, 0))
            if (not np.isfinite(header).all() or np.any(header < 0) or np.any(header != np.floor(header))
                    or header[2] > 2_000_000 or header[4] > header[3]):
                raise ValueError("bounded_complete_corrected_snapshot_header_required")
            if header[0] == 0:
                if np.any(header):
                    raise ValueError("empty_snapshot_has_no_geometry_required")
                return None
            if header[1] <= 0 or header[2] <= 0:
                raise ValueError("nonempty_revisioned_submap_snapshot_required")
            if header[0] <= after_revision:
                return None
            capacity = int(header[2])
            xyz = np.empty((capacity, 3), dtype=np.float64)
            result = self._check(self.library.lingxi_async_snapshot(self._pointer(header), self._pointer(xyz), capacity))
            if result == 1:
                continue
            if (result != 0 or header[2] > capacity or not np.isfinite(header).all()
                    or np.any(header < 0) or np.any(header != np.floor(header))
                    or header[0] <= 0 or header[1] <= 0 or header[2] <= 0 or header[4] > header[3]):
                raise ValueError("complete_coherent_corrected_snapshot_required")
            xyz = xyz[:int(header[2])].copy()
            if not np.isfinite(xyz).all():
                raise ValueError("finite_corrected_map_points_required")
            xyz.flags.writeable = False
            return CorrectedMapSnapshot(int(header[0]), int(header[1]), int(header[3]), int(header[4]), int(header[5]), xyz)
        raise RuntimeError("corrected_snapshot_changed_retry_later")

    def _finish(self, map_path, points_path, result):
        map_path, points_path = Path(os.fsdecode(map_path)), Path(os.fsdecode(points_path))
        values = np.zeros(10, dtype=np.float64)
        self._check(self.library.lingxi_full_finish(os.fsencode(map_path), os.fsencode(points_path),
                    os.fsencode(map_path.parent / "private-global-events.jsonl"), self._pointer(values)))
        if (not np.isfinite(values).all() or np.any(values < 0) or np.any(values[:9] != np.floor(values[:9]))
                or values[0] != self.estimated_frames or values[3] != self.estimated_frames
                or values[4] != self.imu_samples or any(values[i] <= 0 for i in (0, 1, 2, 4, 5, 8))):
            raise ValueError("all_runtime_estimated_frames_and_imu_must_be_saved_once")
        counts = {key: int(value) if i < 9 else float(value) for i, (key, value) in enumerate(zip(COUNTERS, values))}
        status = self.check_health(complete=True)
        if status["completedFrames"] != self.estimated_frames or status["completedImu"] != self.imu_samples:
            raise ValueError("complete_runtime_async_drain_required")
        snapshot = self.snapshot()
        if (snapshot is None or snapshot.submaps != counts["submaps"] or len(snapshot.points) != counts["points"]
                or snapshot.matching_factors != counts["matchingFactors"]
                or snapshot.nonadjacent_factors != counts["nonAdjacentMatchingFactors"]):
            raise ValueError("final_corrected_snapshot_must_equal_saved_map")
        comparison = compare_corrected_points(snapshot.points, np.fromfile(points_path, dtype="<f8").reshape(-1, 3))
        with (map_path.parent / "private-snapshot-comparison.json").open("x", encoding="utf-8") as stream:
            json.dump(comparison, stream, allow_nan=False)
        if not comparison["withinDoublePrecisionRoundoff"]:
            raise ValueError("final_corrected_snapshot_must_equal_saved_map")
        self.final_counts = counts
        for index, value in enumerate((values[0], values[1], values[2], values[3], values[4], values[8])):
            result[index] = value

    def verify_inventory(self, inventory):
        counts = self.final_counts
        if (counts is None or inventory.get("num_matching_cost_factors") != counts["matchingFactors"]
                or inventory.get("nonAdjacentMatchingPairs") != counts["nonAdjacentMatchingFactors"]):
            raise ValueError("full_runtime_graph_must_match_native_final_counts")

    def reload(self, config, map_path, points_path):
        self._owner()
        if self.opened or self.closed:
            raise RuntimeError("fresh_process_full_map_reload_required")
        values = np.zeros(3, dtype=np.float64)
        self._check(self.library.lingxi_full_reload(os.fsencode(config), os.fsencode(map_path),
                    os.fsencode(points_path), self._pointer(values)))
        if not np.isfinite(values).all() or np.any(values <= 0) or np.any(values != np.floor(values)):
            raise ValueError("complete_finite_full_map_reload_required")
        self.closed = True
        return dict(zip(("submaps", "points", "frames"), map(int, values)))
