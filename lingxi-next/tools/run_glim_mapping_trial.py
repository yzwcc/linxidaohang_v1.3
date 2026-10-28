"""Private offline map/save/fresh-process-load diagnostic, never navigation acceptance."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

import numpy as np

import run_glim_offline_trial as replay
from build_slam_cpu_trial import PINS
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools/glim-mapping"
CORE_LIBRARIES = (
    "libglim.so", "libsub_mapping_passthrough.so", "libglobal_mapping_pose_graph.so",
    "libglobal_mapping.so", "libgtsam_points.so", "libgtsam.so", "libgtsam_unstable.so",
)
ROUND_TRIP_TOLERANCE = 1e-4  # Raw coordinate units, serialization only, not geometric accuracy.
SOURCE_FILES = (
    "run_glim_mapping_trial.py", "run_glim_offline_trial.py",
    "run_kiss_offline_trial.py", "diagnose_slam_sensors.py", "glim_trial_config.py",
    "verify_slam_capture.py", "slam_bag_audit.py", "build_slam_cpu_trial.py",
)


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def core_hashes(work):
    return {name: sha256(work / "prefix/lib" / name) for name in CORE_LIBRARIES}


def build(work, output):
    work, output = work.resolve(strict=True), output.resolve()
    core = json.loads((work / "build-report.json").read_text())
    if (platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0
            or core.get("result") != "core_libraries_built_not_target_acceptance"
            or core.get("sourceCommits") != PINS):
        raise ValueError("ordinary_user_reviewed_native_cpu_build_required")
    if output.exists():
        raise ValueError("existing_mapping_build_is_preserved")
    output.mkdir(mode=0o700, parents=True)
    report = {
        "purpose": "ros_free_glim_mapping_extension_build",
        "sourceCommits": PINS, "coreBuildReportSha256": sha256(work / "build-report.json"),
        "sources": {name: sha256(SOURCE / name) for name in ("mapping.cpp", "CMakeLists.txt")},
        "coreLibrariesSha256": core_hashes(work), "stages": [], "navigationValidated": False,
    }
    try:
        for name in ("mapping.cpp", "CMakeLists.txt"):
            shutil.copyfile(SOURCE / name, output / name)
        for name, command in (
            ("configure", ["cmake", "-S", str(output), "-B", str(output / "build")]),
            ("build", ["cmake", "--build", str(output / "build"), "--parallel", "2"]),
        ):
            with (output / (name + ".log")).open("x") as log:
                result = subprocess.run(command, env=replay.library_environment(work), stdout=log, stderr=log)
            report["stages"].append({"name": name, "exitCode": result.returncode})
            if result.returncode:
                raise RuntimeError("mapping_build_failed_see_private_logs")
        report["librarySha256"] = sha256(output / "build/liblingxi_glim_mapping.so")
        report["result"] = "extension_built_not_mapping_acceptance"
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        save_json(output / "build-report.json", report)
    return report


def verify_extension(adapter, work):
    report = json.loads((adapter / "build-report.json").read_text())
    library = adapter / "build/liblingxi_glim_mapping.so"
    if (report.get("result") != "extension_built_not_mapping_acceptance"
            or report.get("sourceCommits") != PINS
            or report.get("sources") != {name: sha256(SOURCE / name) for name in ("mapping.cpp", "CMakeLists.txt")}
            or report.get("coreBuildReportSha256") != sha256(work / "build-report.json")
            or report.get("coreLibrariesSha256") != core_hashes(work)
            or report.get("librarySha256") != sha256(library)):
        raise ValueError("mapping_extension_provenance_mismatch")
    if os.environ.get("LD_LIBRARY_PATH") != replay.library_environment(work)["LD_LIBRARY_PATH"]:
        raise ValueError("explicit_reviewed_mapping_library_path_required")
    return library


class MappingLibrary:
    def __init__(self, path):
        self.library = ctypes.CDLL(str(path))
        pointer = ctypes.POINTER(ctypes.c_double)
        self.library.lingxi_mapping_error.restype = ctypes.c_char_p
        self.library.lingxi_mapping_open.argtypes = []
        self.library.lingxi_mapping_finish.argtypes = [ctypes.c_char_p, ctypes.c_char_p, pointer]
        self.library.lingxi_mapping_shutdown.argtypes = []
        self.library.lingxi_mapping_reload.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, pointer,
        ]

    def call(self, name, *args):
        if getattr(self.library, "lingxi_mapping_" + name)(*args) < 0:
            raise RuntimeError(self.library.lingxi_mapping_error().decode("utf-8", errors="replace"))


def inventory_map(path):
    entries = []
    for entry in sorted(path.rglob("*")):
        if entry.is_symlink() or (not entry.is_dir() and not entry.is_file()):
            raise ValueError("indirect_map_artifact_rejected")
        if entry.is_file():
            if entry.stat().st_size == 0 and entry.name != "imu_rate.txt":
                raise ValueError("empty_map_artifact_rejected")
            entries.append((entry.relative_to(path).as_posix(), entry.stat().st_size, sha256(entry)))
    required = ("graph.bin", "values.bin", "graph.txt", "odom_lidar.txt", "traj_lidar.txt",
                "odom_imu.txt", "traj_imu.txt")
    if not all((path / name).is_file() for name in required):
        raise ValueError("incomplete_global_map_dump")
    lines = (path / "graph.txt").read_text().splitlines()
    if len(lines) != 3:
        raise ValueError("unexpected_global_graph_metadata")
    metadata = {}
    for line, key in zip(lines, ("num_submaps:", "num_all_frames:", "num_matching_cost_factors:")):
        tokens = line.split()
        if len(tokens) != 2 or tokens[0] != key:
            raise ValueError("unexpected_global_graph_metadata")
        metadata[key[:-1]] = int(tokens[1])
    if (metadata["num_submaps"] <= 0 or metadata["num_all_frames"] <= 0
            or metadata["num_matching_cost_factors"] != 0):
        raise ValueError("nonempty_short_pose_graph_required")
    directories = [entry.name for entry in path.iterdir() if entry.is_dir()]
    if sorted(directories) != [f"{index:06d}" for index in range(metadata["num_submaps"])]:
        raise ValueError("complete_numbered_submaps_required")
    for name in ("odom_lidar.txt", "traj_lidar.txt", "odom_imu.txt", "traj_imu.txt"):
        trajectory = np.loadtxt(path / name, ndmin=2)
        if (trajectory.shape != (metadata["num_all_frames"], 8)
                or not np.isfinite(trajectory).all() or np.any(np.diff(trajectory[:, 0]) <= 0)):
            raise ValueError("complete_finite_ordered_saved_trajectory_required")
    digest = hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()
    return {
        **metadata, "fileCount": len(entries), "totalBytes": sum(item[1] for item in entries),
        "artifactInventorySha256": digest,
    }


def compare_points(before, after, count):
    first, second = (np.fromfile(path, dtype="<f8") for path in (before, after))
    if (first.size != count * 3 or first.shape != second.shape or count <= 0
            or not np.isfinite(first).all() or not np.isfinite(second).all()):
        raise ValueError("same_count_nonempty_finite_point_maps_required")
    errors = np.linalg.norm((second - first).reshape(-1, 3), axis=1)
    maximum = float(errors.max())
    if maximum > ROUND_TRIP_TOLERANCE:
        raise ValueError("map_round_trip_exceeds_predeclared_serialization_tolerance")
    return {
        "pointCount": count, "maximumPointDisplacementRawUnits": maximum,
        "rmsPointDisplacementRawUnits": float(np.sqrt(np.mean(errors ** 2))),
        "predeclaredToleranceRawUnits": ROUND_TRIP_TOLERANCE,
        "samePointOrderCompared": True, "geometricAccuracyValidated": False,
    }


def reload_map(args):
    library = verify_extension(args.mapping_adapter, args.work)
    before = inventory_map(args.output / "map")
    stats = np.zeros(4)
    native = MappingLibrary(library)
    native.call("reload", os.fsencode(args.output / "config"), os.fsencode(args.output / "map"),
                os.fsencode(args.output / "private-reloaded-points.f64"), replay.Native.pointer(stats))
    if not np.isfinite(stats).all() or np.any(stats != np.floor(stats)):
        raise ValueError("finite_integer_reload_counters_required")
    if (int(stats[0]) != before["num_submaps"] or int(stats[2]) != before["num_submaps"]
            or int(stats[3]) != before["num_submaps"]):
        raise ValueError("reloaded_graph_counts_mismatch")
    result = compare_points(args.output / "private-saved-points.f64",
                            args.output / "private-reloaded-points.f64", int(stats[1]))
    if before != inventory_map(args.output / "map"):
        raise ValueError("map_dump_changed_during_readonly_reload")
    save_json(args.output / "reload-report.json", {
        "result": "fresh_process_cpu_map_round_trip_passed_not_relocalization",
        "pid": os.getpid(), "loadedSubmaps": int(stats[0]),
        "loadedGraphFactors": int(stats[2]), "loadedGraphValues": int(stats[3]),
        "comparison": result, "mapInventory": before,
        "privateReloadedPointsSha256": sha256(args.output / "private-reloaded-points.f64"),
        "relocalizationValidated": False, "navigationValidated": False,
    })


def _run(args):
    library = verify_extension(args.mapping_adapter, args.work)
    mapping = {}

    class NativeMapping(replay.Native):
        def __init__(self, *parameters):
            super().__init__(*parameters)
            self.mapping = MappingLibrary(library)
            self.closing, self.remaining = False, 0
            try:
                self.mapping.call("open")
            except BaseException:
                self.mapping.call("shutdown")
                super().close()
                raise

        def close(self):
            if self.closing:
                return self.remaining
            self.closing = True
            try:
                self.remaining = super().close()
                stats = np.zeros(6)
                self.mapping.call("finish", os.fsencode(args.output / "map"),
                                  os.fsencode(args.output / "private-saved-points.f64"),
                                  self.pointer(stats))
                if not np.isfinite(stats).all():
                    raise ValueError("finite_mapping_counters_required")
                for key, value in zip(("finalizedFramesFedOnce", "submaps", "points",
                                       "savedFrames", "minimumLoopTravel", "submapTravel"), stats):
                    mapping[key] = int(value) if key in (
                        "finalizedFramesFedOnce", "submaps", "points", "savedFrames",
                    ) else float(value)
                return self.remaining
            finally:
                self.mapping.call("shutdown")

    def recorded(native, report):
        if mapping["finalizedFramesFedOnce"] != report["processedScans"] - report["initializationScans"]:
            raise ValueError("every_initialized_frame_must_be_finalized_exactly_once")
        report["purpose"] = "real_glim_global_map_save_diagnostic_not_navigation"
        report["mapping"] = mapping
        report["boundaries"].update(globalMappingExecuted=True, loopClosureAlgorithmEnabled=True)

    started = time.monotonic()
    report = replay.run(args, native_factory=NativeMapping, on_native_closed=recorded)
    inventory = inventory_map(args.output / "map")
    if inventory["num_submaps"] != mapping["submaps"] or inventory["num_all_frames"] != mapping["savedFrames"]:
        raise ValueError("saved_map_counts_mismatch")
    sources = args.output / "mapping-source-snapshot"
    sources.mkdir(mode=0o700)
    source_hashes = {}
    for name in SOURCE_FILES:
        shutil.copyfile(ROOT / "tools" / name, sources / name)
        source_hashes[name] = sha256(sources / name)
    command = [sys.executable, str(Path(__file__).resolve()), "reload",
               "--mapping-adapter", str(args.mapping_adapter), "--work", str(args.work),
               "--output", str(args.output)]
    with (args.output / "private-reload.log").open("x") as log:
        child = subprocess.run(command, stdout=log, stderr=log, env=replay.library_environment(args.work),
                               timeout=180)
    if child.returncode:
        raise RuntimeError("fresh_process_reload_failed_see_private_logs")
    reloaded = json.loads((args.output / "reload-report.json").read_text())
    if reloaded["pid"] == os.getpid():
        raise ValueError("reload_must_run_in_a_different_process")
    result = {
        "purpose": "offline_glim_map_persistence_not_relocalization_or_navigation",
        "result": "nonempty_map_saved_and_fresh_process_reloaded_not_quality_acceptance",
        "dataset": args.dataset, "timingHypothesis": args.hypothesis,
        "mapping": mapping, "mapInventory": inventory,
        "roundTrip": reloaded["comparison"], "sourceSha256": source_hashes,
        "mappingBuildReportSha256": sha256(args.mapping_adapter / "build-report.json"),
        "baseReplayReportSha256": sha256(args.output / "report.json"),
        "reloadReportSha256": sha256(args.output / "reload-report.json"),
        "privateSavedPointsSha256": sha256(args.output / "private-saved-points.f64"),
        "privateReloadedPointsSha256": reloaded["privateReloadedPointsSha256"],
        "privateReloadLogSha256": sha256(args.output / "private-reload.log"),
        "totalWallSecondsIncludingReload": time.monotonic() - started,
        "replayHost": report["host"], "sourceCommits": PINS,
        "checks": {"globalMappingExecuted": True, "nonemptyMapSaved": True,
                   "allFinalizedFramesFedExactlyOnce": True, "freshProcessReloadPassed": True,
                   "finiteSameCountPointRoundTripPassed": True, "mapUnchangedDuringReload": True},
        "boundaries": {key: False for key in (
            "geometricAccuracyValidated", "robotCalibrationValidated", "timingHypothesisValidated",
            "loopClosureValidated", "relocalizationValidated", "targetArm64RuntimeValidated",
            "navigationValidated", "deploymentApproved", "rawDataPublished",
        )},
    }
    save_json(args.output / "mapping-report.json", result)
    return result


def run(args):
    if args.output.exists():
        raise ValueError("existing_mapping_trial_is_preserved")
    try:
        return _run(args)
    except BaseException as error:
        if args.output.is_dir():
            save_json(args.output / "mapping-report.json", {
                "purpose": "offline_glim_map_persistence_not_relocalization_or_navigation",
                "result": "failed_or_interrupted", "errorType": type(error).__name__,
                "navigationValidated": False, "mapPersistencePassed": False,
            })
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    for name in ("work", "output"):
        builder.add_argument("--" + name, type=Path, required=True)
    runner = commands.add_parser("run")
    for name in ("manifest", "data-root", "diagnostic", "profile", "adapter", "mapping-adapter",
                 "work", "upstream", "output"):
        runner.add_argument("--" + name, type=Path, required=True)
    runner.add_argument("--dataset", choices=("static", "dynamic"), required=True)
    runner.add_argument("--hypothesis", choices=replay.HYPOTHESES, required=True)
    loader = commands.add_parser("reload")
    for name in ("mapping-adapter", "work", "output"):
        loader.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    if (platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0
            or sys.prefix == sys.base_prefix):
        raise ValueError("ordinary_user_isolated_linux_cpu_environment_required")
    old_umask = os.umask(0o077)
    try:
        if args.command == "build":
            print(json.dumps(build(args.work, args.output), indent=2))
        elif args.command == "reload":
            reload_map(args)
        else:
            print(json.dumps(run(args), indent=2))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
