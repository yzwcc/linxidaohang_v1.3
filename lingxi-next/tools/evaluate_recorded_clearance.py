"""Supervised offline OctoMap evaluation; outputs are never navigation costmaps."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("robot-navigation/lingxi_navigation/occupancy.py",
           "robot-navigation/lingxi_navigation/recorded_occupancy.py",
           "robot-slam/lingxi_slam/observation_archive.py",
           "robot-slam/lingxi_slam/observation_binding.py",
           "tools/refine_saved_map.py", "tools/evaluate_recorded_clearance.py")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def configuration(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 32768:
        raise ValueError("bounded_direct_offline_configuration_required")
    value = json.loads(path.read_text(encoding="utf-8"))
    keys = {"schemaVersion", "purpose", "savedMap", "mapReceiptSha256", "bindingSha256",
            "observationImplementationSha256", "geometry", "selection", "projection",
            "octomap", "maximumWorkUnits", "wallTimeoutSeconds"}
    if (type(value) is not dict or set(value) != keys or type(value["schemaVersion"]) is not int
            or value["schemaVersion"] != 1 or value["purpose"] != "offline_declared_clearance_only"
            or type(value["wallTimeoutSeconds"]) is not int or not 1 <= value["wallTimeoutSeconds"] <= 300
            or type(value["maximumWorkUnits"]) is not int or not 1 <= value["maximumWorkUnits"] <= 16000000
            or type(value["octomap"]) is not dict or set(value["octomap"]) != {"library", "sha256", "dependencies"}):
        raise ValueError("explicit_bounded_offline_evaluation_configuration_required")
    return value


def worker(config, output):
    import resource
    worker_started = time.monotonic()
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024**2, 20 * 1024**2))
    sys.path[:0] = [str(ROOT / "robot-navigation"), str(ROOT / "robot-slam")]
    from refine_saved_map import inventory
    from lingxi_navigation.occupancy import ClearanceProjection, OctomapProjector
    from lingxi_navigation.recorded_occupancy import (OfflineGeometry, RaySelection,
                                                     prepare_recorded_clearance)
    from lingxi_slam.observation_binding import verify_observations
    value, config_sha256 = configuration(config), digest(config)
    sources = {name: digest(ROOT / name) for name in SOURCES}
    geometry = value["geometry"]
    if type(geometry) is not dict or set(geometry) != {"metresPerRawUnit", "declaredFromRaw"}:
        raise ValueError("explicit_offline_scale_and_ground_frame_required")
    projection = ClearanceProjection(**value["projection"])
    prepared = prepare_recorded_clearance(Path(value["savedMap"]),
        receipt_sha256=value["mapReceiptSha256"], binding_sha256=value["bindingSha256"],
        implementation_sha256=value["observationImplementationSha256"], inspect_map=inventory,
        geometry=OfflineGeometry(geometry["metresPerRawUnit"], tuple(geometry["declaredFromRaw"])),
        selection=RaySelection(**value["selection"]))
    preparation_duration = time.monotonic() - worker_started
    native = value["octomap"]
    projector = OctomapProjector(Path(native["library"]), native["sha256"], native["dependencies"])
    started = time.monotonic()
    result = projector.project_declared(prepared.batch, projection,
                                        maximum_work_units=value["maximumWorkUnits"])
    duration = time.monotonic() - started
    saved = Path(value["savedMap"])
    binding = verify_observations(saved, inventory(saved / "map"),
        json.loads((saved / "map-ready.json").read_text())["sensorProvenance"],
        value["observationImplementationSha256"])
    if (digest(config) != config_sha256 or digest(saved / "map-ready.json") != value["mapReceiptSha256"]
            or digest(saved / "observations-ready.json") != value["bindingSha256"]
            or binding["observations"]["archiveSha256"] != prepared.archive_sha256
            or any(digest(ROOT / name) != before for name, before in sources.items())):
        raise ValueError("unchanged_complete_evaluation_inputs_required")
    report = {"schemaVersion": 1, "result": "recorded_declared_clearance_evaluated_not_navigation",
        **prepared.summary(), "projection": projection.__dict__, "configurationSha256": config_sha256,
        "runnerSourcesSha256": sources, "octomapVersion": "1.9.7", "nativeSha256": native["sha256"],
        "loadedDependenciesSha256": native["dependencies"], "nativeDurationSeconds": duration,
        "preparationDurationSeconds": preparation_duration,
        "workerDurationSeconds": time.monotonic() - worker_started,
        "peakRssKiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "statistics": dict(zip(("rays", "scans", "nodes", "freeCells", "occupiedCells", "unknownCells", "workUnits"),
                               result.statistics)),
        "clearanceSha256": result.sha256, "clearanceBytesSha256": hashlib.sha256(result.costs).hexdigest(),
        "robotContacted": False, "sensorsSubscribed": False, "physicalMotionSent": False}
    with (output / "private-declared-clearance.bin").open("xb") as stream:
        stream.write(result.costs)
        stream.flush()
        os.fsync(stream.fileno())
    pending = output / "report.pending"
    with pending.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return report


def run(config, output):
    from refine_saved_map import private_output
    config = Path(config).absolute()
    value = configuration(config)
    if config.resolve(strict=True) != config:
        raise ValueError("direct_offline_configuration_required")
    output = private_output(output, Path(value["savedMap"]))
    output.mkdir(mode=0o700)
    environment = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                   "MKL_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        with (output / "private-worker.log").open("x") as stream:
            child = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
                "--config", str(config), "--output", str(output)], stdout=stream, stderr=stream,
                env=environment, timeout=value["wallTimeoutSeconds"])
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("offline_clearance_worker_timed_out_no_accepted_output_see_private_log") from error
    pending = output / "report.pending"
    if (child.returncode or not pending.is_file() or pending.is_symlink()
            or not 1 <= pending.stat().st_size <= 32768):
        raise RuntimeError("offline_clearance_worker_failed_no_accepted_output_see_private_log")
    report = json.loads(pending.read_text(encoding="utf-8"))
    if (type(report) is not dict
            or report.get("result") != "recorded_declared_clearance_evaluated_not_navigation"
            or report.get("configurationSha256") != digest(config)
            or report.get("offlineOnly") is not True
            or any(report.get(key) is not False for key in ("physicalScaleValidated", "groundFrameValidated",
                "groundSupportValidated", "obstacleCompletenessValidated", "navigationReady",
                "robotContacted", "sensorsSubscribed", "physicalMotionSent"))
            or report.get("runnerSourcesSha256") != {name: digest(ROOT / name) for name in SOURCES}
            or report.get("nativeSha256") != value["octomap"]["sha256"]
            or report.get("loadedDependenciesSha256") != value["octomap"]["dependencies"]):
        raise ValueError("complete_offline_only_worker_report_required")
    artifact = output / "private-declared-clearance.bin"
    projection, statistics = report.get("projection"), report.get("statistics")
    if (type(projection) is not dict or type(statistics) is not dict
            or any(projection.get(key) != setting for key, setting in value["projection"].items())
            or any(type(projection.get(key)) is not int or not 8 <= projection[key] <= 1024
                   for key in ("width", "height"))
            or artifact.is_symlink() or not artifact.is_file()
            or artifact.stat().st_size != projection["width"] * projection["height"]
            or digest(artifact) != report.get("clearanceBytesSha256")):
        raise ValueError("complete_unchanged_offline_clearance_artifact_required")
    costs = artifact.read_bytes()
    if (sum(costs.count(code) for code in (0, 254, 255)) != len(costs)
            or any(type(statistics.get(key)) is not int or statistics[key] != costs.count(code)
                   for key, code in (("freeCells", 0), ("occupiedCells", 254), ("unknownCells", 255)))):
        raise ValueError("complete_offline_clearance_cell_accounting_required")
    os.replace(pending, output / "report.json")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        report = worker(args.config, args.output) if args.worker else run(args.config, args.output)
        print(json.dumps({key: report[key] for key in ("result", "scans", "sourceReturns", "selectedReturns",
            "statistics", "physicalScaleValidated", "navigationReady", "physicalMotionSent")}))
    finally:
        os.umask(previous)
