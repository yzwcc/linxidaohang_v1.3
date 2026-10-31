"""Paced recorded GLIM mapping with bounded background work, no robot control."""
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

import numpy as np

import run_glim_full_mapping as full

ROOT = full.ROOT
SOURCE = ROOT / "tools/glim-async-mapping"
NATIVE_SOURCES = ("CMakeLists.txt", "async_mapping.cpp", "bounded_worker.hpp")
ASYNC_COUNTERS = ("acceptedFrames", "completedFrames", "peakOutstandingFrames", "frameCapacity",
                  "acceptedImu", "completedImu", "peakOutstandingImu", "imuCapacity", "rejectedTasks",
                  "discardedInputs", "maximumAdmissionMilliseconds", "maximumQueueWaitMilliseconds",
                  "maximumWorkerTaskMilliseconds")


def inputs(args):
    result = full.inputs(args.work, args.upstream)
    result["asyncSources"] = {name: full.sha256(SOURCE / name) for name in NATIVE_SOURCES}
    result["asyncRunnerSha256"] = full.sha256(Path(__file__).resolve())
    # Review upstream scheduling without changing its unbounded queue implementations.
    result["upstreamSchedulingReview"] = {name: full.sha256(args.upstream / name) for name in (
        "src/glim/mapping/async_sub_mapping.cpp", "src/glim/mapping/async_global_mapping.cpp")}
    return result


def build(args):
    output = full.private_output(args.output)
    frozen = inputs(args)
    output.mkdir(mode=0o700)
    source = output / "source"
    source.mkdir()
    for name in NATIVE_SOURCES:
        shutil.copyfile(SOURCE / name, source / name)
    (source / "sync").mkdir()
    shutil.copyfile(full.SOURCE / "full_mapping.cpp", source / "sync/full_mapping.cpp")
    for name in full.UPSTREAM_INPUTS:
        target = source / "upstream" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.upstream / name, target)
    patch = full.SOURCE / "submapping-eof.patch"
    commands = (
        ("patch-check", ["git", "-C", str(source / "upstream"), "apply", "--check", "--recount", str(patch)]),
        ("patch", ["git", "-C", str(source / "upstream"), "apply", "--recount", str(patch)]),
        ("configure", ["cmake", "-S", str(source), "-B", str(output / "build")]),
        ("build", ["cmake", "--build", str(output / "build"), "--parallel", "1"]),
    )
    for name, command in commands:
        with (output / (name + ".private.log")).open("x") as log:
            result = subprocess.run(command, env=full.replay.library_environment(args.work),
                                    stdout=log, stderr=log, timeout=600)
        if result.returncode:
            raise RuntimeError("async_mapping_build_failed_see_private_log_" + name)
    library = output / "build" / full.LIBRARY
    if inputs(args) != frozen:
        raise ValueError("async_mapping_build_inputs_changed")
    report = {"result": "bounded_async_mapping_built_not_target_acceptance", "inputs": frozen,
              "patchedSourceSha256": {name: full.sha256(source / "upstream" / name)
                                      for name in full.UPSTREAM_INPUTS},
              "copiedSyncSha256": full.sha256(source / "sync/full_mapping.cpp"),
              "copiedAsyncSha256": {name: full.sha256(source / name) for name in NATIVE_SOURCES},
              "privateClassSymbols": full.private_class_symbols(library),
              "librarySha256": full.sha256(library), "boundaries": dict.fromkeys(full.BOUNDARIES, False)}
    full.save(output / "build-report.json", report)
    return report


def verified_adapter(args):
    report = json.loads((args.mapping_adapter / "build-report.json").read_text())
    source = args.mapping_adapter / "source"
    library = args.mapping_adapter / "build" / full.LIBRARY
    if (report.get("result") != "bounded_async_mapping_built_not_target_acceptance"
            or report.get("inputs") != inputs(args) or report.get("librarySha256") != full.sha256(library)
            or report.get("copiedSyncSha256") != full.sha256(source / "sync/full_mapping.cpp")
            or report.get("copiedSyncSha256") != full.sha256(full.SOURCE / "full_mapping.cpp")
            or report.get("copiedAsyncSha256") != {name: full.sha256(source / name) for name in NATIVE_SOURCES}
            or report.get("copiedAsyncSha256") != report["inputs"]["asyncSources"]
            or report.get("patchedSourceSha256") != {name: full.sha256(source / "upstream" / name)
                                                    for name in full.UPSTREAM_INPUTS}):
        raise ValueError("source_bound_async_mapping_adapter_required")
    return library


def checked_status(stats, counts):
    if (stats.shape != (len(ASYNC_COUNTERS),) or not np.isfinite(stats).all() or np.any(stats < 0)
            or np.any(stats[:10] != np.floor(stats[:10]))):
        raise ValueError("finite_async_worker_counters_required")
    result = {name: int(value) if index < 10 else float(value)
              for index, (name, value) in enumerate(zip(ASYNC_COUNTERS, stats))}
    for name, expected in (("Frames", counts["savedFrames"]), ("Imu", counts["imuSamples"])):
        if result["accepted" + name] != expected or result["completed" + name] != expected:
            raise ValueError("complete_async_drain_required")
    if (result["rejectedTasks"] or result["discardedInputs"] or result["frameCapacity"] != 128
            or result["imuCapacity"] != 8192 or result["peakOutstandingFrames"] > result["frameCapacity"]
            or result["peakOutstandingImu"] > result["imuCapacity"]):
        raise ValueError("bounded_fault_free_async_run_required")
    return result


class AsyncMapping(full.FullMapping):
    def __init__(self, library):
        super().__init__(library)
        pointer = ctypes.POINTER(ctypes.c_double)
        self.library.lingxi_async_status.argtypes = [pointer]
        self.library.lingxi_async_snapshot.argtypes = [pointer, pointer, ctypes.c_size_t]

    def snapshot(self, include_points=False):
        header = np.zeros(6)
        self.call_snapshot(header, None, 0)
        if not include_points or not header[2]:
            return header, None
        for _ in range(4):
            points = np.empty((int(header[2]), 3))
            if self.call_snapshot(header, full.replay.Native.pointer(points), len(points)) == 0:
                return header, points[:int(header[2])].copy()
        raise RuntimeError("snapshot_growing_retry_later")

    def call_snapshot(self, header, points, capacity):
        result = self.library.lingxi_async_snapshot(full.replay.Native.pointer(header), points, capacity)
        if result < 0:
            raise RuntimeError(self.library.lingxi_full_error().decode("utf-8", errors="replace"))
        return result


def reload(args):
    library = verified_adapter(args)
    before = full.inventory(args.output / "map")
    native, stats = full.FullMapping(library), np.zeros(3)
    native.call("reload", os.fsencode(args.output / "full-config"), os.fsencode(args.output / "map"),
                os.fsencode(args.output / "private-reloaded-points.f64"), full.replay.Native.pointer(stats))
    if (not np.isfinite(stats).all() or np.any(stats <= 0) or np.any(stats != np.floor(stats))
            or stats[0] != before["num_submaps"] or stats[2] != before["num_all_frames"]):
        raise ValueError("complete_async_readonly_reload_required")
    comparison = full.compare_points(args.output / "private-saved-points.f64",
                                     args.output / "private-reloaded-points.f64", int(stats[1]))
    if full.inventory(args.output / "map") != before:
        raise ValueError("async_map_changed_during_reload")
    report = {"pid": os.getpid(), "mapInventory": before, "comparison": comparison,
              "boundaries": dict.fromkeys(full.BOUNDARIES, False)}
    full.save(args.output / "full-reload-report.json", report)
    return report


def run(args):
    full.private_output(args.output, args.data_root)
    library, frozen = verified_adapter(args), inputs(args)
    profile_sha, capture_sha = full.sha256(args.profile), full.sha256(args.manifest)
    counts, scheduling, provenance, loaded, snapshot_headers = {}, {}, {}, {}, []
    final_snapshot = {}

    class NativeAsync(full.replay.Native):
        def __init__(self, odometry_library, baseline, module):
            provenance.update(full.full_config(args.upstream, baseline, args.output / "full-config"))
            super().__init__(odometry_library, args.output / "full-config", module)
            self.full, self.closing, self.remaining = AsyncMapping(library), False, 0
            self.origin, self.started, self.next_snapshot = None, None, 0
            try:
                self.full.call("open")
                loaded.update(full.observed_libraries(args, library))
            except BaseException:
                self.full.call("shutdown")
                super().close()
                raise

        def imu(self, stamp, acceleration, angular):
            if self.origin is None:
                self.origin, self.started = stamp, time.monotonic()
            if args.input_rate:
                time.sleep(max(0, self.started + (stamp - self.origin) / args.input_rate - time.monotonic()))
            self.full.call("imu", stamp, self.pointer(acceleration), self.pointer(angular))
            super().imu(stamp, acceleration, angular)

        def frame(self, stamp, points, offsets):
            status = np.zeros(len(ASYNC_COUNTERS))
            if self.full.library.lingxi_async_status(self.pointer(status)) < 0:
                raise RuntimeError(self.full.library.lingxi_full_error().decode("utf-8", errors="replace"))
            value = super().frame(stamp, points, offsets)
            if time.monotonic() >= self.next_snapshot:
                header, _ = self.full.snapshot()
                if header[0] and (not snapshot_headers or header[0] != snapshot_headers[-1][0]):
                    snapshot_headers.append(header.tolist())
                self.next_snapshot = time.monotonic() + 0.2
            return value

        def close(self):
            if self.closing:
                return self.remaining
            self.closing = True
            try:
                self.remaining = super().close()
                stats = np.zeros(len(full.COUNTERS))
                self.full.call("finish", os.fsencode(args.output / "map"),
                               os.fsencode(args.output / "private-saved-points.f64"),
                               os.fsencode(args.output / "private-global-events.jsonl"), self.pointer(stats))
                counts.update(full.checked_counts(stats, int(stats[0])))
                async_stats = np.zeros(len(ASYNC_COUNTERS))
                if self.full.library.lingxi_async_status(self.pointer(async_stats)) < 0:
                    raise RuntimeError("async_status_failed_after_drain")
                scheduling.update(checked_status(async_stats, counts))
                header, points = self.full.snapshot(include_points=True)
                if header[1:5].tolist() != [counts["submaps"], counts["points"], counts["matchingFactors"],
                                          counts["nonAdjacentMatchingFactors"]] or points is None:
                    raise ValueError("complete_corrected_final_snapshot_required")
                points.tofile(args.output / "private-snapshot-points.f64")
                final_snapshot.update(revision=int(header[0]), correctedSubmapPoseEvents=int(header[5]),
                    comparison=full.compare_points(args.output / "private-saved-points.f64",
                                                   args.output / "private-snapshot-points.f64", len(points)))
                return self.remaining
            finally:
                primary = sys.exc_info()[0]
                try:
                    self.full.call("shutdown")
                except BaseException:
                    if primary is None:
                        raise

    def finished(native, report):
        full.checked_counts(np.array([counts[name] for name in full.COUNTERS]),
                            report["processedScans"] - report["initializationScans"])
        report["purpose"] = "recorded_bounded_async_local_global_mapping_not_navigation"
        report["boundaries"].update(globalMappingExecuted=True, loopClosureAlgorithmEnabled=True)

    started = time.monotonic()
    try:
        base = full.replay.run(args, native_factory=NativeAsync, on_native_closed=finished)
        if counts["imuSamples"] != base["imuSamplesInserted"]:
            raise ValueError("same_real_imu_for_async_mapping_and_lio_required")
        events = full.checked_events(args.output / "private-global-events.jsonl", counts)
        mapped = full.inventory(args.output / "map")
        if (mapped["num_submaps"] != counts["submaps"] or mapped["num_all_frames"] != counts["savedFrames"]
                or mapped["num_matching_cost_factors"] != counts["matchingFactors"]
                or mapped["nonAdjacentMatchingPairs"] != counts["nonAdjacentMatchingFactors"]):
            raise ValueError("complete_async_saved_graph_required")
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "reload"]
        for name in ("work", "upstream", "mapping_adapter", "output"):
            command += ["--" + name.replace("_", "-"), str(getattr(args, name))]
        with (args.output / "private-full-reload.log").open("x") as log:
            child = subprocess.run(command, stdout=log, stderr=log,
                                   env=full.replay.library_environment(args.work), timeout=180)
        if child.returncode:
            raise RuntimeError("async_fresh_reload_failed_see_private_log")
        reloaded = json.loads((args.output / "full-reload-report.json").read_text())
        if reloaded["pid"] == os.getpid() or reloaded["mapInventory"] != mapped:
            raise ValueError("fresh_unchanged_async_reload_required")
        for name in ("private-native.log", "private-full-reload.log"):
            full.check_reload_log(args.output / name)
        full.verify(json.loads(args.manifest.read_text()), args.data_root)
        verified_adapter(args)
        if (inputs(args) != frozen or full.sha256(args.profile) != profile_sha
                or full.sha256(args.manifest) != capture_sha
                or full.configuration_hashes(args.output / "full-config") != provenance["effectiveConfigurationSha256"]):
            raise ValueError("async_inputs_must_remain_unchanged")
        result = {"result": "bounded_async_recorded_map_saved_reloaded_not_target_acceptance",
                  "dataset": args.dataset, "timingHypothesis": args.hypothesis, "inputRate": args.input_rate,
                  "mapping": counts, "worker": scheduling, "mapInventory": mapped, "incrementalUpdates": events,
                  "finalSnapshot": final_snapshot, "snapshotHeadersObservedDuringInput": snapshot_headers,
                  "roundTrip": reloaded["comparison"], "configuration": provenance,
                  "actualLoadedLibrariesSha256": loaded, "implementationInputs": frozen,
                  "captureManifestSha256": capture_sha, "profileSha256": profile_sha,
                  "processingWallSeconds": base["processingWallSeconds"], "peakResidentMiB": base["peakResidentMiB"],
                  "odometryAndCallbackMillisecondsP50P95P99": base["odometryMillisecondsP50P95P99"],
                  "worstCombinedFrameMilliseconds": base["combinedMillisecondsMaximum"],
                  "totalWallSecondsIncludingDrainAndReload": time.monotonic() - started,
                  "sourceInputsUnchanged": True,
                  "filesSha256": {name: full.sha256(args.output / name) for name in (
                      "report.json", "full-reload-report.json", "private-native.log", "private-full-reload.log",
                      "private-saved-points.f64", "private-reloaded-points.f64", "private-snapshot-points.f64",
                      "private-global-events.jsonl")}, "boundaries": dict.fromkeys(full.BOUNDARIES, False)}
        full.save(args.output / "async-mapping-report.json", result)
        return result
    except BaseException as error:
        if args.output.is_dir():
            full.save(args.output / "async-mapping-report.json", {"result": "failed_or_interrupted",
                "errorType": type(error).__name__, "boundaries": dict.fromkeys(full.BOUNDARIES, False)})
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
            command.add_argument("--hypothesis", choices=full.replay.HYPOTHESES, required=True)
            command.add_argument("--input-rate", type=float, choices=(0.0, 1.0), default=1.0)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        parser.error("ordinary_user_offline_linux_cpu_required")
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.absolute())
    environment = full.replay.library_environment(args.work)
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
