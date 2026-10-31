"""Saved acquisition rays to offline-only clearance; no guessed metric acceptance."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

import numpy as np

from lingxi_slam.observation_archive import ObservationArchive, rigid, sha256
from lingxi_slam.observation_binding import read_json, verify_observations
from .navfn import finite
from .occupancy import (DeclaredObservationBatch, LidarReturn, MAXIMUM_RAYS,
                        ScanObservation, canonical, digest_value)


@dataclass(frozen=True)
class OfflineGeometry:
    metres_per_raw_unit: float
    declared_from_raw: tuple

    def __post_init__(self):
        if (not finite(self.metres_per_raw_unit) or not .001 <= self.metres_per_raw_unit <= 1000
                or type(self.declared_from_raw) is not tuple or len(self.declared_from_raw) != 16
                or not all(map(finite, self.declared_from_raw))):
            raise ValueError("explicit_bounded_offline_geometry_declaration_required")
        matrix = rigid(np.array(self.declared_from_raw).reshape(4, 4))
        if np.max(np.abs(matrix[:3, 3])) > 500:
            raise ValueError("bounded_declared_ground_frame_translation_required")

    @property
    def sha256(self):
        return hashlib.sha256(canonical({**self.__dict__, "purpose": "offline_evaluation_only",
            "physicalScaleValidated": False, "groundFrameValidated": False})).hexdigest()

    def apply(self, values):
        matrix = np.array(self.declared_from_raw).reshape(4, 4)
        return (values * self.metres_per_raw_unit) @ matrix[:3, :3].T + matrix[:3, 3]


@dataclass(frozen=True)
class RaySelection:
    maximum_rays: int = 131072
    maximum_per_scan: int = 8192

    def __post_init__(self):
        if (type(self.maximum_rays) is not int or not 4096 <= self.maximum_rays <= MAXIMUM_RAYS
                or type(self.maximum_per_scan) is not int or not 1 <= self.maximum_per_scan <= 8192):
            raise ValueError("bounded_offline_ray_selection_required")

    def indices(self, count, scans):
        # Keep measured pairs together and cover every scan; never average ray origins.
        selected = min(count, self.maximum_per_scan, self.maximum_rays // scans)
        return (2 * np.arange(selected, dtype=np.int64) + 1) * count // (2 * selected)


@dataclass(frozen=True)
class RecordedClearanceInput:
    batch: DeclaredObservationBatch
    geometry: OfflineGeometry
    selection: RaySelection
    source_returns: int
    selected_returns: int
    archive_sha256: str
    source_geometry_sha256: str

    def summary(self):
        return {"mapId": self.batch.map_id, "mapReceiptSha256": self.batch.receipt_sha256,
            "bindingSha256": self.batch.binding_sha256, "revision": self.batch.revision,
            "archiveSha256": self.archive_sha256, "registeredGeometrySha256": self.source_geometry_sha256,
            "geometryDeclarationSha256": self.geometry.sha256, "raySelection": self.selection.__dict__,
            "selectionSha256": self.batch.selection_sha256, "observationsSha256": self.batch.sha256,
            "scans": len(self.batch.scans), "sourceReturns": self.source_returns,
            "selectedReturns": self.selected_returns, "omittedReturns": self.source_returns - self.selected_returns,
            "frame": self.batch.frame, "units": self.batch.units, "offlineOnly": True,
            "physicalScaleValidated": False, "groundFrameValidated": False, "groundSupportValidated": False,
            "obstacleCompletenessValidated": False, "navigationReady": False}


def prepare_recorded_clearance(saved, *, receipt_sha256, binding_sha256, implementation_sha256,
                               inspect_map, geometry, selection=RaySelection()):
    """No output escapes before complete archive iteration and final source recheck.

    inspect_map must recompute the actual saved-map inventory, not echo a receipt.
    The caller pins the expected binding/build; no current-source substitution is made.
    """
    saved = Path(saved)
    if (not saved.is_absolute() or saved.resolve(strict=True) != saved or saved.is_symlink()
            or not saved.is_dir() or not callable(inspect_map) or type(geometry) is not OfflineGeometry
            or type(selection) is not RaySelection
            or not all(map(digest_value, (receipt_sha256, binding_sha256, implementation_sha256)))):
        raise ValueError("direct_saved_map_pinned_identity_and_offline_declaration_required")
    receipt_path, binding_path = saved / "map-ready.json", saved / "observations-ready.json"

    def verify():
        if sha256(receipt_path) != receipt_sha256 or sha256(binding_path) != binding_sha256:
            raise ValueError("unchanged_pinned_map_receipt_and_binding_required")
        receipt = read_json(receipt_path)
        if (type(receipt.get("session")) is not str
                or re.fullmatch("[a-f0-9]{32}", receipt["session"]) is None
                or saved.name != receipt["session"] or receipt.get("inputMode") != "recorded"):
            raise ValueError("recorded_map_session_identity_required")
        binding = verify_observations(saved, inspect_map(saved / "map"),
                                      receipt["sensorProvenance"], implementation_sha256)
        return receipt, binding

    receipt, binding = verify()
    details = binding["observations"]
    archive = ObservationArchive(saved / "private-observations.bin", details["archiveSha256"])
    scans, selection_digest, selected = [], hashlib.sha256(canonical(selection.__dict__)), 0
    for scan in archive:
        indices = selection.indices(len(scan.origins_raw), archive.scans)
        selection_digest.update(canonical({"ordinal": scan.ordinal, "frameId": scan.frame_id,
            "sourceSha256": scan.source_sha256, "originalReturns": len(scan.origins_raw)}))
        selection_digest.update(indices.astype("<u8").tobytes())
        origins, endpoints = geometry.apply(scan.origins_raw[indices]), geometry.apply(scan.endpoints_raw[indices])
        returns = tuple(LidarReturn(tuple(map(float, origin)), tuple(map(float, endpoint)))
                        for origin, endpoint in zip(origins, endpoints))
        scans.append(ScanObservation(scan.ordinal, scan.stamp, scan.source_sha256, returns))
        selected += len(returns)
    if verify() != (receipt, binding):
        raise ValueError("unchanged_complete_recorded_map_required")
    batch = DeclaredObservationBatch("slam-" + receipt["session"], receipt_sha256, archive.revision,
        binding_sha256, geometry.sha256, selection_digest.hexdigest(), tuple(scans))
    return RecordedClearanceInput(batch, geometry, selection, archive.points, selected,
                                   archive.sha256, details["registeredGeometrySha256"])
