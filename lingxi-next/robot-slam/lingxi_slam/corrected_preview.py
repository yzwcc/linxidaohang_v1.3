"""Revisioned display-only samples; file output never blocks native sensor input."""
import json
import os
from pathlib import Path
import threading
import time

import numpy as np

from .process_protocol import session_id


class CorrectedPreviewWriter:
    def __init__(self, path, session, input_mode):
        self.path, self.session = Path(path), session_id(session)
        if input_mode not in ("live", "recorded", "synthetic"):
            raise ValueError("explicit_corrected_preview_input_mode_required")
        self.mode, self.revision = input_mode, 0
        self.condition = threading.Condition()
        self.pending, self.closed, self.failed = None, False, False
        self.thread = threading.Thread(target=self._run, name="corrected-map-preview", daemon=True)
        self.thread.start()

    def offer(self, snapshot):
        if snapshot.revision <= self.revision:
            return
        count = len(snapshot.points)
        indices = np.linspace(0, count - 1, min(count, 8000), dtype=np.int64)
        record = {"schemaVersion": 1, "session": self.session, "inputMode": self.mode,
                  "revision": snapshot.revision, "submaps": snapshot.submaps,
                  "totalPoints": count, "matchingFactors": snapshot.matching_factors,
                  "nonAdjacentMatchingFactors": snapshot.nonadjacent_factors,
                  "coordinateFrame": "lingxi_glim_map", "coordinateUnits": "native-raw",
                  "updatedAtMs": time.time() * 1000,
                  "points": snapshot.points[indices].reshape(-1).tolist()}
        with self.condition:
            if self.closed or self.failed:
                raise RuntimeError("active_corrected_preview_writer_required")
            self.pending, self.revision = record, snapshot.revision
            self.condition.notify()

    def _run(self):
        temporary = self.path.with_suffix(".pending")
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or self.pending is not None)
                    if self.pending is None and self.closed:
                        return
                    record, self.pending = self.pending, None
                with temporary.open("w", encoding="utf-8") as stream:
                    os.chmod(temporary, 0o600)
                    json.dump(record, stream, allow_nan=False, separators=(",", ":"))
                os.replace(temporary, self.path)
        except Exception:
            with self.condition:
                self.failed, self.pending = True, None
            if temporary.exists():
                temporary.unlink()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.thread.join(2)
        if self.thread.is_alive() or self.failed:
            raise RuntimeError("corrected_preview_write_failed")
