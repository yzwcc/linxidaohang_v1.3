"""Offline recorded-point diagnostic; only aggregate results leave private files."""
import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.seeded_localization import (  # noqa: E402
    RegistrationConfig, SMALL_GICP_VERSION, SeededMapRegistration,
    rigid_pose, sensor_scan_from_world)
from run_glim_mapping_trial import inventory_map  # noqa: E402


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def load_saved_points(directory, config):
    directory = Path(directory)
    receipt_path, points_path = directory / "map-ready.json", directory / "private-saved-points.f64"
    if (directory.is_symlink() or receipt_path.is_symlink() or points_path.is_symlink()
            or (directory / "map").is_symlink() or receipt_path.stat().st_size > 65536):
        raise ValueError("direct_bounded_saved_map_required")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    count = receipt.get("counts", {}).get("points")
    if (receipt.get("schemaVersion") != 1 or receipt.get("freshProcessReloadPassed") is not True
            or any(receipt.get(key) is not False for key in
                   ("navigationReady", "localizationReady", "geometricAccuracyValidated"))
            or receipt.get("inputMode") != "recorded"
            or type(count) is not int or not config.minimum_points <= count <= config.maximum_map_points
            or points_path.stat().st_size != count * 24
            or digest(points_path) != receipt.get("savedPointsSha256")
            or inventory_map(directory / "map") != receipt.get("mapInventory")):
        raise ValueError("integrity_checked_recorded_saved_map_required")
    points = np.fromfile(points_path, dtype="<f8").reshape(-1, 3)
    identity = {"receiptSha256": digest(receipt_path), "pointsSha256": digest(points_path),
                "mapInventory": receipt["mapInventory"]}
    return points, identity


def load_recorded_scan(path, config):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > config.maximum_scan_points * 24 + 65536:
        raise ValueError("bounded_direct_recorded_npz_required")
    expected = {"points.npy", "world_lidar_pose.npy", "frame_id.npy", "algorithm_stamp.npy"}
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if (len(entries) != 4 or {entry.filename for entry in entries} != expected
                or sum(entry.file_size for entry in entries) > config.maximum_scan_points * 24 + 65536):
            raise ValueError("bounded_numeric_recorded_npz_required")
    before = digest(path)
    with np.load(path, allow_pickle=False) as record:
        points, pose = record["points"], rigid_pose(record["world_lidar_pose"])
        frame, stamp = record["frame_id"], record["algorithm_stamp"]
        if (frame.shape != () or frame.dtype.kind not in "iu" or not 1 <= int(frame) <= 2**53 - 1
                or stamp.shape != () or stamp.dtype.kind not in "fi"
                or not np.isfinite(stamp) or float(stamp) <= 0):
            raise ValueError("finite_recorded_frame_and_stamp_required")
        local = sensor_scan_from_world(points, pose)
        if not config.minimum_points <= len(local) <= config.maximum_scan_points:
            raise ValueError("bounded_recorded_scan_points_required")
    if digest(path) != before:
        raise ValueError("recorded_scan_changed_while_loading")
    return local, pose, before


def diagnostic_seeds(recorded_pose):
    perturbed = recorded_pose.copy()
    perturbed[:3, 3] += [.2, -.15, .1]
    angle = np.deg2rad(5)
    rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                         [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    perturbed[:3, :3] = rotation @ recorded_pose[:3, :3]
    remote = recorded_pose.copy()
    remote[:3, 3] += [100, 100, 100]
    return (("recorded_pose_reuse", recorded_pose),
            ("controlled_perturbation", perturbed), ("remote_seed_negative_control", remote))


def run(saved_map, scan_paths):
    if not 1 <= len(scan_paths) <= 8:
        raise ValueError("one_to_eight_recorded_scans_required")
    config = RegistrationConfig()
    points, identity = load_saved_points(saved_map, config)
    registration = SeededMapRegistration(points, config)
    scans = []
    for path in scan_paths:
        scan, pose, scan_digest = load_recorded_scan(path, config)
        estimates = []
        for label, seed in diagnostic_seeds(pose):
            estimate = registration.register(scan, seed)
            estimates.append({
                "seedKind": label, "acceptedByTrialGate": estimate.accepted,
                "rejectionReasons": list(estimate.reasons), "metrics": estimate.metrics,
            })
        if digest(Path(path)) != scan_digest:
            raise ValueError("recorded_scan_changed_during_registration")
        scans.append({"inputSha256": scan_digest, "trials": estimates})
    _, after = load_saved_points(saved_map, config)
    if after != identity:
        raise ValueError("saved_map_changed_during_registration")
    return {
        "schemaVersion": 1, "kind": "seeded_recorded_scan_registration_diagnostic",
        "machine": platform.machine(), "python": platform.python_version(),
        "dependencies": {"small-gicp": SMALL_GICP_VERSION, "numpy": np.__version__,
                         "scipy": version("scipy")},
        "sourceSha256": {
            "registration": digest(ROOT / "robot-slam/lingxi_slam/seeded_localization.py"),
            "runner": digest(Path(__file__)),
        },
        "configRawUnitsNotPhysicalAcceptance": asdict(config), "savedMap": identity,
        "scans": scans,
        "boundaries": {
            "recordedPoseUsedForSeeds": True, "possibleMappingSequenceReuse": True,
            "worldPointsConvertedBackToSensor": True, "inputFilesUnchanged": True,
            "poseCoordinatesExported": False, "groundTruthAccuracyMeasured": False,
            "globalRelocalizationValidated": False, "continuousTrackingValidated": False,
            "robotExecution": False, "localizationReady": False, "navigationReady": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saved-map", required=True, type=Path)
    parser.add_argument("--scan", required=True, action="append", type=Path)
    parser.add_argument("--acknowledge-recording-reuse", required=True, action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_trial_output_preserved")
    if not args.output.parent.is_dir():
        raise ValueError("existing_output_parent_required")
    result = run(args.saved_map, args.scan)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"result": result["kind"], "scans": len(result["scans"]),
                      "localizationReady": False, "navigationReady": False}))


if __name__ == "__main__":
    main()
