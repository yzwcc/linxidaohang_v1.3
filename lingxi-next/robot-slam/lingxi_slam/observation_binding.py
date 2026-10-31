"""Bind acquisition sources to an independently reloaded map; never retrofit old maps."""
import json
import os
from pathlib import Path
import re

from .map_provenance import sensor_provenance
from .observation_archive import inspect_observations, sha256


def read_json(path, maximum=32768):
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= maximum:
        raise ValueError("bounded_observation_binding_required")
    return json.loads(path.read_text(encoding="utf-8"))


def expected_binding(saved, inventory, provenance, implementation_sha256, *, pending=False):
    if type(implementation_sha256) is not str or re.fullmatch("[a-f0-9]{64}", implementation_sha256) is None:
        raise ValueError("frozen_observation_implementation_digest_required")
    receipt_path = saved / ("map-ready.pending" if pending else "map-ready.json")
    receipt = read_json(receipt_path)
    provenance = sensor_provenance(provenance)
    if (receipt.get("schemaVersion") != 3 or receipt.get("mappingEngine") != "glim-full-async-v1"
            or receipt.get("inputMode") not in ("live", "recorded", "synthetic")
            or receipt.get("freshProcessReloadPassed") is not True or receipt.get("navigationReady") is not False
            or "observationImplementationSha256" in receipt
            and receipt["observationImplementationSha256"] != implementation_sha256
            or receipt.get("mapInventory") != inventory or receipt.get("sensorProvenance") != provenance):
        raise ValueError("same_reloaded_map_and_sensor_provenance_required")
    report = inspect_observations((saved / "private-observations.bin").absolute(),
                                  sha256(saved / "private-observations.bin"))
    if (report["submaps"] != receipt["counts"]["submaps"] or report["scans"] != receipt["counts"]["savedFrames"]):
        raise ValueError("all_saved_map_frames_need_source_observations")
    return {"schemaVersion": 1, "mapReceiptSha256": sha256(receipt_path), "mapInventory": inventory,
            "sensorProvenance": provenance, "implementationSha256": implementation_sha256,
            "inputMode": receipt["inputMode"], "observations": report}


def bind_observations(saved, inventory, provenance, implementation_sha256, *, pending=False):
    saved = Path(saved)
    target = saved / "observations-ready.json"
    if target.exists() or target.is_symlink():
        raise ValueError("new_observation_binding_required")
    report = expected_binding(saved, inventory, provenance, implementation_sha256, pending=pending)
    temporary = saved / "observations-ready.pending"
    with temporary.open("x", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(report, stream, allow_nan=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    return report


def verify_observations(saved, inventory, provenance, implementation_sha256):
    saved = Path(saved)
    binding = read_json(saved / "observations-ready.json")
    if binding != expected_binding(saved, inventory, provenance, implementation_sha256):
        raise ValueError("unchanged_map_bound_observation_archive_required")
    return binding
