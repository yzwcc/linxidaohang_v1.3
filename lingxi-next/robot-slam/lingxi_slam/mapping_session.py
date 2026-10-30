"""Explicit-save lifecycle for the existing GLIM mapping ABI; no robot control."""
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import traceback

from .process_protocol import session_id
from .map_provenance import sensor_provenance as checked_provenance


class SaveDecision:
    """The control thread may request saving or revoke it, but never call native code."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = "open"

    def request_save(self):
        with self.lock:
            if self.state not in ("open", "save"):
                return False
            self.state = "save"
            return True

    def discard(self):
        with self.lock:
            if self.state != "finished":
                self.state = "discard"

    def requested(self):
        with self.lock:
            return self.state == "save"

    def finish(self, publish=None):
        with self.lock:
            try:
                if self.state == "save" and publish is not None:
                    return publish()
                return None
            finally:
                self.state = "finished"


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class MappingSession:
    """Decorate an engine with once-only flush, shutdown, independent reload and receipt.

    ``mapping_factory`` creates the existing MappingLibrary after odometry.
    ``inspect_map`` is its existing
    inventory validator; ``reload_map`` must run the existing loader in a fresh
    process and return its report. Native methods, verification and cleanup all
    run on the creating worker thread. Callers must budget separately for save.
    """

    def __init__(self, native_factory, mapping_factory, decision, output, *, name, session,
                 input_mode, can_save, inspect_map, reload_map,
                 sensor_provenance=None, verify_sensor_provenance=None, verify_save=None):
        if (not isinstance(decision, SaveDecision) or not isinstance(name, str)
                or not 1 <= len(name.strip()) <= 64
                or input_mode not in ("live", "recorded", "synthetic")
                or not all(callable(value) for value in
                           (native_factory, mapping_factory, can_save, inspect_map, reload_map))):
            raise ValueError("explicit_mapping_save_configuration_required")
        if (sensor_provenance is None) != (verify_sensor_provenance is None):
            raise ValueError("paired_sensor_provenance_verifier_required")
        self.sensor_provenance = (None if sensor_provenance is None else checked_provenance(sensor_provenance))
        if verify_sensor_provenance is not None and not callable(verify_sensor_provenance):
            raise ValueError("callable_sensor_provenance_verifier_required")
        self.verify_sensor_provenance = verify_sensor_provenance
        if verify_save is not None and not callable(verify_save):
            raise ValueError("callable_mapping_save_verifier_required")
        self.verify_save = verify_save
        self._verify_provenance()
        self.session = session_id(session)
        self.output, self.name, self.input_mode = Path(output), name.strip(), input_mode
        self.output.mkdir(mode=0o700)
        self.owner, self.closed, self.failed = threading.get_ident(), False, False
        self.mapping, self.decision = None, decision
        self.can_save, self.inspect_map, self.reload_map = can_save, inspect_map, reload_map
        self.receipt, self.remaining, self.close_error = None, None, None
        try:
            self.native = native_factory()
        except BaseException:
            decision.discard()
            decision.finish()
            raise
        try:
            self.mapping = mapping_factory()
            self.mapping.call("open")
        except BaseException:
            decision.discard()
            try:
                if self.mapping is not None:
                    self.mapping.call("shutdown")
            finally:
                try:
                    self.native.close()
                finally:
                    decision.finish()
            raise

    def _active(self):
        if threading.get_ident() != self.owner or self.closed or self.failed:
            raise RuntimeError("active_owner_thread_mapping_session_required")

    def _verify_provenance(self):
        if (self.sensor_provenance is not None
                and checked_provenance(self.verify_sensor_provenance()) != self.sensor_provenance):
            raise ValueError("unchanged_mapping_sensor_provenance_required")

    def _call(self, method, *args):
        self._active()
        try:
            return getattr(self.native, method)(*args)
        except BaseException:
            self.failed = True
            self.decision.discard()
            raise

    def imu(self, *args):
        return self._call("imu", *args)

    def frame(self, *args):
        return self._call("frame", *args)

    def close(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("owner_thread_mapping_close_required")
        if self.closed:
            if self.close_error is not None:
                raise RuntimeError("mapping_save_previously_failed") from self.close_error
            return self.remaining
        self.closed = True
        counts = None
        try:
            try:
                # Closing odometry emits its remaining marginalized frames exactly once.
                self.remaining = self.native.close()
                if not self.failed and self.decision.requested() and self.can_save() is True:
                    # Full binary/journal verification must not stall the active input watchdog.
                    if self.verify_save is not None:
                        self.verify_save()
                    self._verify_provenance()
                    stats = (ctypes.c_double * 6)()
                    self.mapping.call("finish", os.fsencode(self.output / "map"),
                                      os.fsencode(self.output / "private-saved-points.f64"), stats)
                    if (not all(math.isfinite(value) for value in stats)
                            or any(not 0 < value <= 2**53 or value != int(value) for value in stats[:4])
                            or stats[0] != stats[3]):
                        raise ValueError("complete_finite_saved_mapping_counts_required")
                    counts = dict(zip(("finalizedFrames", "submaps", "points", "savedFrames"),
                                      map(int, stats[:4])))
            finally:
                # Reload must not share a live mapper or its callbacks.
                self.mapping.call("shutdown")
            if counts is not None:
                self._verify_and_publish(counts)
        except BaseException as error:
            self.close_error = error
            self.decision.discard()
            origin = traceback.extract_tb(error.__traceback__)[-1]
            print(f"mapping_close_failure:{type(error).__name__}:"
                  f"{Path(origin.filename).name}:{origin.lineno}", file=sys.stderr, flush=True)
            raise
        finally:
            self.decision.finish()
        return self.remaining

    def _verify_and_publish(self, counts):
        map_path, points_path = self.output / "map", self.output / "private-saved-points.f64"
        before = self.inspect_map(map_path)
        if (before["num_submaps"] != counts["submaps"]
                or before["num_all_frames"] != counts["savedFrames"]
                or points_path.stat().st_size != counts["points"] * 24):
            raise ValueError("saved_map_inventory_must_match_native_counts")
        point_digest = _digest(points_path)
        reloaded = self.reload_map(self.output)
        if self.verify_save is not None:
            self.verify_save()
        self._verify_provenance()
        if (self.sensor_provenance is not None
                and checked_provenance(reloaded.get("sensorProvenance")) != self.sensor_provenance):
            raise ValueError("same_sensor_provenance_in_fresh_reload_required")
        comparison = reloaded.get("comparison", {})
        maximum = comparison.get("maximumPointDisplacementRawUnits")
        if (type(reloaded.get("pid")) is not int or reloaded["pid"] <= 0 or reloaded["pid"] == os.getpid()
                or reloaded.get("loadedSubmaps") != counts["submaps"]
                or comparison.get("pointCount") != counts["points"]
                or comparison.get("samePointOrderCompared") is not True
                or comparison.get("predeclaredToleranceRawUnits") != 1e-4
                or type(maximum) not in (int, float) or not math.isfinite(maximum)
                or not 0 <= maximum <= 1e-4
                or self.inspect_map(map_path) != before or _digest(points_path) != point_digest):
            raise ValueError("independent_unchanged_map_round_trip_required")
        if self.can_save() is not True:
            self.decision.discard()

        def publish():
            receipt = {
                "schemaVersion": 2 if self.sensor_provenance is not None else 1,
                "session": self.session, "name": self.name,
                "inputMode": self.input_mode, "counts": counts, "mapInventory": before,
                "savedPointsSha256": point_digest, "freshProcessReloadPassed": True,
                "navigationReady": False, "localizationReady": False,
                "geometricAccuracyValidated": False,
                **({"sensorProvenance": self.sensor_provenance} if self.sensor_provenance is not None else {}),
            }
            temporary = self.output / "map-ready.pending"
            with temporary.open("x", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                json.dump(receipt, stream, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.output / "map-ready.json")
            self.receipt = receipt

        self.decision.finish(publish)
