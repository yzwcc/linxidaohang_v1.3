"""Recorded LIO through complete CPU local/global mapping, never robot control."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

import json5
import numpy as np

import run_glim_offline_trial as replay
from build_slam_cpu_trial import PINS
from export_glim_mapping_evidence import check_reload_log
from glim_trial_config import verify_source
from refine_saved_map import (PYTHON_SOURCES, configuration_hashes, inventory, prepare_config,
                              private_output, save)
from run_glim_mapping_trial import compare_points, core_hashes
from verify_slam_capture import sha256, verify


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools/glim-full-mapping"
NATIVE_SOURCES = ("CMakeLists.txt", "full_mapping.cpp", "submapping-eof.patch")
RUNNERS = tuple(sorted(set(PYTHON_SOURCES) | {"refine_saved_map.py", "run_glim_full_mapping.py"}))
UPSTREAM_INPUTS = ("include/glim/mapping/sub_mapping.hpp", "src/glim/mapping/sub_mapping.cpp",
                   "config/config_sub_mapping_cpu.json", "config/config_global_mapping_cpu.json", "LICENSE")
LIBRARY = "liblingxi_glim_full_mapping.so"
COUNTERS = ("finalizedFramesFedOnce", "submaps", "points", "savedFrames", "imuSamples",
            "globalUpdateEvents", "matchingFactors", "nonAdjacentMatchingFactors", "localOptimizationCalls",
            "maximumGlobalInsertionMilliseconds")
BOUNDARIES = ("robotContacted", "liveSensorsSubscribed", "motionCommandsSent", "physicalScaleValidated",
              "geometricAccuracyValidated", "loopClosureValidated", "targetArm64RuntimeValidated",
              "navigationValidated", "deploymentApproved", "rawDataPublished")


def inputs(work, upstream):
    core = json.loads((work / "build-report.json").read_text())
    if core.get("result") != "core_libraries_built_not_target_acceptance" or core.get("sourceCommits") != PINS:
        raise ValueError("pinned_cpu_core_required")
    verify_source(upstream)
    original = {}
    for name in UPSTREAM_INPUTS:
        expected = subprocess.run(["git", "-C", str(upstream), "show", "HEAD:" + name],
                                  check=True, capture_output=True, timeout=10).stdout
        path = upstream / name
        if path.is_symlink() or path.read_bytes() != expected:
            raise ValueError("clean_pinned_submapping_source_required")
        original[name] = sha256(path)
    return {"sourceCommits": PINS, "coreBuildReportSha256": sha256(work / "build-report.json"),
            "coreLibrariesSha256": {**core_hashes(work),
                                    "libodometry_estimation_cpu.so": sha256(work / "prefix/lib/libodometry_estimation_cpu.so")},
            "nativeSources": {name: sha256(SOURCE / name) for name in NATIVE_SOURCES},
            "runnerSources": {name: sha256(ROOT / "tools" / name) for name in RUNNERS},
            "upstreamInputs": original}


def build(args):
    output = private_output(args.output)
    before = inputs(args.work, args.upstream)
    output.mkdir(mode=0o700)
    source = output / "source"
    source.mkdir()
    for name in NATIVE_SOURCES:
        shutil.copyfile(SOURCE / name, source / name)
    for name in UPSTREAM_INPUTS:
        target = source / "upstream" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.upstream / name, target)
    patch = source / "submapping-eof.patch"
    commands = (
        ("patch-check", ["git", "-C", str(source / "upstream"), "apply", "--check", "--recount", str(patch)]),
        ("patch", ["git", "-C", str(source / "upstream"), "apply", "--recount", str(patch)]),
        ("configure", ["cmake", "-S", str(source), "-B", str(output / "build")]),
        ("build", ["cmake", "--build", str(output / "build"), "--parallel", "1"]),
    )
    for name, command in commands:
        with (output / (name + ".private.log")).open("x") as log:
            result = subprocess.run(command, env=replay.library_environment(args.work), stdout=log, stderr=log, timeout=600)
        if result.returncode:
            raise RuntimeError("full_mapping_build_failed_see_private_log_" + name)
    if inputs(args.work, args.upstream) != before:
        raise ValueError("full_mapping_build_inputs_changed")
    report = {"result": "full_cpu_mapping_adapter_built_not_target_acceptance", "inputs": before,
              "patchedSourceSha256": {name: sha256(source / "upstream" / name) for name in UPSTREAM_INPUTS},
              "privateClassSymbols": private_class_symbols(output / "build" / LIBRARY),
              "librarySha256": sha256(output / "build" / LIBRARY), "boundaries": dict.fromkeys(BOUNDARIES, False)}
    save(output / "build-report.json", report)
    return report


def private_class_symbols(library):
    symbols = subprocess.run(["nm", "-D", "--defined-only", str(library)],
                             check=True, capture_output=True, text=True, timeout=10).stdout
    demangled = subprocess.run(["c++filt"], input=symbols, check=True,
                              capture_output=True, text=True, timeout=10).stdout
    names = ("glim::LingxiSubMappingEof::insert_finalized_frame(",
             "glim::LingxiSubMappingEof::submit_end_of_sequence()")
    if any(name not in demangled for name in names) or "glim::SubMapping::" in demangled:
        raise ValueError("independent_patched_submapping_class_symbols_required")
    return list(names)


def verified_adapter(args):
    value = json.loads((args.mapping_adapter / "build-report.json").read_text())
    library = args.mapping_adapter / "build" / LIBRARY
    if (value.get("result") != "full_cpu_mapping_adapter_built_not_target_acceptance"
            or value.get("inputs") != inputs(args.work, args.upstream)
            or value.get("librarySha256") != sha256(library)
            or value.get("patchedSourceSha256") != {name: sha256(args.mapping_adapter / "source/upstream" / name)
                                                    for name in UPSTREAM_INPUTS}):
        raise ValueError("source_bound_full_mapping_adapter_required")
    return library


def full_config(upstream, baseline, output):
    provenance = prepare_config(upstream, baseline, output)
    source = upstream / "config/config_sub_mapping_cpu.json"
    local = json5.loads(source.read_text())
    local["sub_mapping"]["enable_optimization"] = True
    save(output / source.name, local)
    config = json.loads((output / "config.json").read_text())
    config["global"]["config_sub_mapping"] = source.name
    (output / "config.json").unlink()
    save(output / "config.json", config)
    global_path = output / "config_global_mapping_cpu.json"
    world = json5.loads(global_path.read_text())
    world["global_mapping"]["randomsampling_rate"] = 1.0
    global_path.unlink()
    save(global_path, world)
    provenance["effectiveOverrides"] = {"localOptimization": True, "globalOptimization": True,
                                        "localAndGlobalImu": True, "globalSamplingRate": 1.0,
                                        "reloadOptimization": False, "terminalFramePolicy": "finalized_lio_pose_no_extrapolation"}
    provenance["effectiveConfigurationSha256"] = configuration_hashes(output)
    return provenance


class FullMapping:
    def __init__(self, library):
        self.library = ctypes.CDLL(str(library))
        pointer = ctypes.POINTER(ctypes.c_double)
        self.library.lingxi_full_error.restype = ctypes.c_char_p
        self.library.lingxi_full_open.argtypes = []
        self.library.lingxi_full_imu.argtypes = [ctypes.c_double, pointer, pointer]
        self.library.lingxi_full_finish.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, pointer]
        self.library.lingxi_full_reload.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, pointer]
        self.library.lingxi_full_shutdown.argtypes = []

    def call(self, name, *args):
        if getattr(self.library, "lingxi_full_" + name)(*args) < 0:
            raise RuntimeError(self.library.lingxi_full_error().decode("utf-8", errors="replace"))


def observed_libraries(args, library):
    expected = {name: (args.work / "prefix/lib" / name).resolve() for name in
                ("libglim.so", "libglobal_mapping.so", "libodometry_estimation_cpu.so",
                 "libgtsam.so", "libgtsam_points.so", "libgtsam_unstable.so")}
    expected.update({LIBRARY: library.resolve(),
                     "liblingxi_glim_offline.so": (args.adapter / "build/liblingxi_glim_offline.so").resolve()})
    found = {}
    by_path = {path: name for name, path in expected.items()}
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6:
            continue
        path = Path(fields[5])
        if path.name in ("libsub_mapping.so", "libsub_mapping_passthrough.so", "libglobal_mapping_pose_graph.so"):
            raise ValueError("old_submapping_or_posegraph_must_not_be_loaded")
        resolved = path.resolve()
        if resolved in by_path:
            found[by_path[resolved]] = sha256(path)
        elif path.name in expected:
            if resolved != expected[path.name]:
                raise ValueError("actual_full_mapping_library_path_mismatch")
    # These are optional thin factory / unused extension modules, not the class implementations.
    required = set(expected) - {"libglobal_mapping.so", "libgtsam_unstable.so"}
    if not required.issubset(found):
        raise ValueError("all_actual_full_mapping_libraries_required")
    return found


def checked_counts(stats, estimated):
    if (stats.shape != (len(COUNTERS),) or not np.isfinite(stats).all() or np.any(stats < 0)
            or np.any(stats[:9] != np.floor(stats[:9]))):
        raise ValueError("finite_integer_full_mapping_counters_required")
    value = {name: int(number) if index < 9 else float(number)
             for index, (name, number) in enumerate(zip(COUNTERS, stats))}
    if (value["finalizedFramesFedOnce"] != estimated or value["savedFrames"] != estimated or estimated <= 0
            or any(value[key] <= 0 for key in ("submaps", "points", "imuSamples", "globalUpdateEvents", "localOptimizationCalls"))):
        raise ValueError("complete_full_mapping_frame_accounting_required")
    return value


def checked_events(path, counts):
    events = [json.loads(line) for line in path.read_text().splitlines()]
    if len(events) != counts["globalUpdateEvents"]:
        raise ValueError("every_incremental_update_event_required")
    previous = 0
    for event in events:
        if (set(event) != {"submaps", "matchingFactors", "nonAdjacentMatchingFactors"}
                or any(type(number) is not int or number < 0 for number in event.values())
                or not previous <= event["submaps"] <= counts["submaps"] or event["submaps"] == 0
                or event["nonAdjacentMatchingFactors"] > event["matchingFactors"]):
            raise ValueError("ordered_complete_incremental_update_required")
        previous = event["submaps"]
    if (previous != counts["submaps"] or events[-1]["matchingFactors"] != counts["matchingFactors"]
            or events[-1]["nonAdjacentMatchingFactors"] != counts["nonAdjacentMatchingFactors"]):
        raise ValueError("final_incremental_update_must_match_saved_graph")
    return events


def reload(args):
    library = verified_adapter(args)
    before = inventory(args.output / "map")
    native, stats = FullMapping(library), np.zeros(3)
    native.call("reload", os.fsencode(args.output / "full-config"), os.fsencode(args.output / "map"),
                os.fsencode(args.output / "private-reloaded-points.f64"), replay.Native.pointer(stats))
    if (not np.isfinite(stats).all() or np.any(stats != np.floor(stats)) or np.any(stats <= 0)
            or stats[0] != before["num_submaps"] or stats[2] != before["num_all_frames"]):
        raise ValueError("complete_readonly_full_map_reload_required")
    comparison = compare_points(args.output / "private-saved-points.f64",
                                args.output / "private-reloaded-points.f64", int(stats[1]))
    if inventory(args.output / "map") != before:
        raise ValueError("full_map_changed_during_reload")
    result = {"pid": os.getpid(), "mapInventory": before, "comparison": comparison,
              "boundaries": dict.fromkeys(BOUNDARIES, False)}
    save(args.output / "full-reload-report.json", result)
    return result


def run(args):
    private_output(args.output, args.data_root)
    library = verified_adapter(args)
    frozen = inputs(args.work, args.upstream)
    profile_sha, capture_sha = sha256(args.profile), sha256(args.manifest)
    counts, provenance, loaded = {}, {}, {}

    class NativeFull(replay.Native):
        def __init__(self, odometry_library, baseline, module):
            provenance.update(full_config(args.upstream, baseline, args.output / "full-config"))
            super().__init__(odometry_library, args.output / "full-config", module)
            self.full, self.closing, self.remaining = FullMapping(library), False, 0
            try:
                self.full.call("open")
                loaded.update(observed_libraries(args, library))
            except BaseException:
                self.full.call("shutdown")
                super().close()
                raise

        def imu(self, stamp, acceleration, angular):
            self.full.call("imu", stamp, self.pointer(acceleration), self.pointer(angular))
            super().imu(stamp, acceleration, angular)

        def close(self):
            if self.closing:
                return self.remaining
            self.closing = True
            try:
                self.remaining = super().close()
                stats = np.zeros(len(COUNTERS))
                self.full.call("finish", os.fsencode(args.output / "map"),
                               os.fsencode(args.output / "private-saved-points.f64"),
                               os.fsencode(args.output / "private-global-events.jsonl"), self.pointer(stats))
                counts.update(checked_counts(stats, int(stats[0])))
                return self.remaining
            finally:
                self.full.call("shutdown")

    def finished(native, report):
        checked_counts(np.array([counts[name] for name in COUNTERS]),
                       report["processedScans"] - report["initializationScans"])
        report["purpose"] = "recorded_complete_local_global_mapping_not_navigation"
        report["boundaries"].update(globalMappingExecuted=True, loopClosureAlgorithmEnabled=True)

    started = time.monotonic()
    try:
        base = replay.run(args, native_factory=NativeFull, on_native_closed=finished)
        if counts["imuSamples"] != base["imuSamplesInserted"]:
            raise ValueError("same_real_imu_samples_for_lio_local_and_global_required")
        events = checked_events(args.output / "private-global-events.jsonl", counts)
        mapped = inventory(args.output / "map")
        if (mapped["num_submaps"] != counts["submaps"] or mapped["num_all_frames"] != counts["savedFrames"]
                or mapped["num_matching_cost_factors"] != counts["matchingFactors"]
                or mapped["nonAdjacentMatchingPairs"] != counts["nonAdjacentMatchingFactors"]):
            raise ValueError("incremental_native_graph_and_saved_map_must_match")
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "reload"]
        for name in ("work", "upstream", "mapping_adapter", "output"):
            command += ["--" + name.replace("_", "-"), str(getattr(args, name))]
        with (args.output / "private-full-reload.log").open("x") as log:
            child = subprocess.run(command, stdout=log, stderr=log, env=replay.library_environment(args.work), timeout=180)
        if child.returncode:
            raise RuntimeError("full_mapping_fresh_reload_failed_see_private_log")
        reloaded = json.loads((args.output / "full-reload-report.json").read_text())
        if reloaded["pid"] == os.getpid() or reloaded["mapInventory"] != mapped:
            raise ValueError("fresh_unchanged_full_map_reload_required")
        for name in ("private-native.log", "private-full-reload.log"):
            check_reload_log(args.output / name)
        verify(json.loads(args.manifest.read_text()), args.data_root)
        verified_adapter(args)
        if (inputs(args.work, args.upstream) != frozen or sha256(args.profile) != profile_sha
                or sha256(args.manifest) != capture_sha
                or configuration_hashes(args.output / "full-config") != provenance["effectiveConfigurationSha256"]):
            raise ValueError("full_mapping_inputs_must_remain_unchanged")
        result = {"result": "incremental_full_cpu_map_saved_and_reloaded_not_geometry_acceptance",
                  "dataset": args.dataset, "timingHypothesis": args.hypothesis, "mapping": counts,
                  "mapInventory": mapped, "incrementalUpdates": events, "roundTrip": reloaded["comparison"],
                  "configuration": provenance, "actualLoadedLibrariesSha256": loaded,
                  "implementationInputs": frozen, "captureManifestSha256": capture_sha, "profileSha256": profile_sha,
                  "processingWallSeconds": base["processingWallSeconds"], "peakResidentMiB": base["peakResidentMiB"],
                  "odometryAndCallbackMillisecondsP50P95P99": base["odometryMillisecondsP50P95P99"],
                  "totalWallSecondsIncludingReload": time.monotonic() - started, "sourceInputsUnchanged": True,
                  "filesSha256": {name: sha256(args.output / name) for name in
                                   ("report.json", "full-reload-report.json", "private-native.log", "private-full-reload.log",
                                    "private-saved-points.f64", "private-reloaded-points.f64", "private-global-events.jsonl")},
                  "boundaries": dict.fromkeys(BOUNDARIES, False)}
        save(args.output / "full-mapping-report.json", result)
        return result
    except BaseException as error:
        if args.output.is_dir():
            save(args.output / "full-mapping-report.json", {"result": "failed_or_interrupted",
                 "errorType": type(error).__name__, "boundaries": dict.fromkeys(BOUNDARIES, False)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "run", "reload"):
        command = commands.add_parser(name)
        for field in ("work", "upstream", "output") + (() if name == "build" else ("mapping-adapter",)):
            command.add_argument("--" + field, type=Path, required=True)
        if name == "run":
            for field in ("adapter", "manifest", "data-root", "diagnostic", "profile"):
                command.add_argument("--" + field, type=Path, required=True)
            command.add_argument("--dataset", choices=("static", "dynamic"), required=True)
            command.add_argument("--hypothesis", choices=replay.HYPOTHESES, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        parser.error("ordinary_user_offline_linux_cpu_required")
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.absolute())
    environment = replay.library_environment(args.work)
    if os.environ.get("LD_LIBRARY_PATH") != environment["LD_LIBRARY_PATH"]:
        os.execve(sys.executable, [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:]], environment)
    previous = os.umask(0o077)
    try:
        result = {"build": build, "run": run, "reload": reload}[args.command](args)
        print(json.dumps({"result": result.get("result", "readonly_reload_checked")}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
