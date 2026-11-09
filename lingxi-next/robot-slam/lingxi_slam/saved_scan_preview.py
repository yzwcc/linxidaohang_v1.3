"""Display-only detail from registered laser returns; never replaces the SLAM map."""
import argparse
import json
import os
from pathlib import Path

import numpy as np

from .observation_archive import ObservationArchive, sha256

MAX_PREVIEW_POINTS = 80000


def reduce_points(points):
    """Retain actual returns at 5 cm spacing, reserving detail for the default Z band."""
    if not len(points):
        return points
    _, indices = np.unique(np.floor(points / .05), axis=0, return_index=True)
    cloud = points[np.sort(indices)]
    inside = (cloud[:, 2] >= .1) & (cloud[:, 2] <= 1.4)
    bands = [cloud[inside], cloud[~inside]]
    limits = [min(len(bands[0]), 60000), 0]
    limits[1] = min(len(bands[1]), MAX_PREVIEW_POINTS - limits[0])
    limits[0] = min(len(bands[0]), MAX_PREVIEW_POINTS - limits[1])
    return np.concatenate([band[np.linspace(0, len(band) - 1, limit, dtype=int)]
                           for band, limit in zip(bands, limits) if limit])


def build(saved, *, pending=False):
    saved = Path(saved)
    receipt_path = saved / ("map-ready.pending" if pending else "map-ready.json")
    receipt_hash = sha256(receipt_path)
    receipt = json.loads(receipt_path.read_text())
    binding_path = saved / "observations-ready.json"
    binding_hash = sha256(binding_path)
    binding = json.loads(binding_path.read_text())
    if (binding["mapReceiptSha256"] != receipt_hash or binding["inputMode"] != receipt["inputMode"]
            or binding["mapInventory"] != receipt["mapInventory"]):
        raise ValueError("same_map_scan_preview_required")
    archive_path = saved / "private-observations.bin"
    archive = ObservationArchive(archive_path.absolute(), binding["observations"]["archiveSha256"])
    cloud, batch = np.empty((0, 3)), []
    for scan in archive:
        batch.append(scan.endpoints_raw)
        if len(batch) >= 32:
            cloud = reduce_points(np.concatenate([cloud, *batch]))
            batch.clear()
    if batch:
        cloud = reduce_points(np.concatenate([cloud, *batch]))
    if (archive.scans != receipt["counts"]["savedFrames"] or not len(cloud)
            or sha256(receipt_path) != receipt_hash or sha256(binding_path) != binding_hash):
        raise ValueError("unchanged_complete_scan_preview_sources_required")
    value = {"schemaVersion": 1, "mapReceiptSha256": receipt_hash,
             "archiveSha256": archive.sha256, "sourceReturns": archive.points,
             "voxelSize": .05, "points": np.round(cloud, 6).reshape(-1).tolist()}
    temporary = saved / "scan-preview.pending"
    with temporary.open("x", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, allow_nan=False, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, saved / "scan-preview.json")
    return {"points": len(cloud), "sourceReturns": archive.points,
            "defaultZPoints": int(np.count_nonzero((cloud[:, 2] >= .1) & (cloud[:, 2] <= 1.4)))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("saved", type=Path)
    print(json.dumps(build(parser.parse_args().saved)))
