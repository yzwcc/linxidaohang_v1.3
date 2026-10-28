"""Fresh-process full-map controls; change only preprocessing threads, not old trials."""
import argparse
from importlib import metadata
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

import run_glim_mapping_trial as mapping
import run_glim_offline_trial as replay
from export_glim_mapping_evidence import check_reload_log
from glim_target_mapping import validate_mapping_counts
from run_glim_preprocess_trial import PREPROCESS_CONFIG_SHA256, new_output, save
from verify_slam_capture import sha256, verify


ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = tuple((dataset, hypothesis, repeat)
                   for dataset in ("static", "dynamic") for hypothesis in replay.HYPOTHESES
                   for repeat in (1, 2, 3))
BOUNDARIES = (
    "robotContacted", "rosNodeStarted", "motionCommandsSent", "productionConfigurationSelected",
    "timingHypothesisValidated", "robotCalibrationValidated", "geometricAccuracyValidated",
    "loopClosureValidated", "relocalizationValidated", "targetArm64RuntimeValidated",
    "livePerformanceValidated", "voiceConcurrencyValidated", "navigationValidated",
    "deploymentApproved", "rawDataPublished",
)
SOURCE_FILES = tuple(dict.fromkeys((*mapping.SOURCE_FILES, "run_glim_controlled_mapping.py")))


def config_hashes(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError("direct_configuration_directory_required")
    files = list(path.iterdir())
    if any(item.is_symlink() or not item.is_file() for item in files):
        raise ValueError("only_direct_configuration_files_required")
    return {item.name: sha256(item) for item in sorted(files)}


def make_control(baseline, destination):
    if destination.exists() or destination.is_relative_to(baseline):
        raise ValueError("separate_new_control_configuration_required")
    before = config_hashes(baseline)
    manifest = json.loads((baseline / "trial-manifest.json").read_text())
    if (manifest["files"] != {name: digest for name, digest in before.items() if name != "trial-manifest.json"}
            or before["config_preprocess.json"] != PREPROCESS_CONFIG_SHA256
            or any(manifest[name] is not False for name in (
                "deploymentApproved", "robotCalibrationValidated", "navigationValidated"))):
        raise ValueError("verified_unchanged_two_thread_baseline_required")
    original = json5.loads((baseline / "config_preprocess.json").read_text(), allow_duplicate_keys=False)
    if type(original["preprocess"]["num_threads"]) is not int or original["preprocess"]["num_threads"] != 2:
        raise ValueError("explicit_two_to_one_thread_control_required")
    controlled = json.loads(json.dumps(original))
    controlled["preprocess"]["num_threads"] = 1
    shutil.copytree(baseline, destination)
    (destination / "config_preprocess.json").write_text(
        json.dumps(controlled, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    manifest["files"]["config_preprocess.json"] = sha256(destination / "config_preprocess.json")
    manifest["algorithmParameters"] = "upstream_defaults_except_single_preprocessing_thread_diagnostic"
    manifest["diagnosticControlOnly"] = True
    (destination / "trial-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    after = config_hashes(destination)
    if (config_hashes(baseline) != before or set(before) != set(after)
            or {name for name in before if before[name] != after[name]}
            != {"config_preprocess.json", "trial-manifest.json"}):
        raise ValueError("only_declared_configuration_files_may_change")
    return {
        "purpose": "single_preprocessing_thread_full_mapping_control_not_production",
        "baselineFilesSha256": before, "effectiveFilesSha256": after,
        "semanticParameterChanges": [{"file": "config_preprocess.json",
                                      "path": ["preprocess", "num_threads"], "before": 2, "after": 1}],
        "configurationCommentsAndWhitespaceMayDiffer": True,
        "sensorMappingOdometryAndRangeParametersUnchanged": True,
        "productionConfigurationSelected": False,
    }


def actual_libraries(work, adapter, mapping_adapter):
    prefix = work / "prefix/lib"
    expected = {path.name: path for path in prefix.glob("*.so*") if path.is_file()}
    expected.update({
        "liblingxi_glim_offline.so": adapter / "build/liblingxi_glim_offline.so",
        "liblingxi_glim_mapping.so": mapping_adapter / "build/liblingxi_glim_mapping.so",
    })
    found = {}
    for line in Path("/proc/self/maps").read_text().splitlines():
        parts = line.split(None, 5)
        if len(parts) != 6:
            continue
        path = Path(parts[5])
        if path.name not in expected:
            continue
        if path.resolve() != expected[path.name].resolve():
            raise ValueError("loaded_mapping_library_outside_verified_paths")
        found[path.name] = sha256(path)
    required = {"libglim.so", "libodometry_estimation_cpu.so",
                "liblingxi_glim_offline.so", "liblingxi_glim_mapping.so"}
    if not required.issubset(found):
        raise ValueError("actual_full_mapping_libraries_required")
    return found


def reload_map(args):
    library = mapping.verify_extension(args.mapping_adapter, args.work)
    before = mapping.inventory_map(args.output / "map")
    stats = np.zeros(4)
    native = mapping.MappingLibrary(library)
    native.call("reload", os.fsencode(args.output / "control-config"), os.fsencode(args.output / "map"),
                os.fsencode(args.output / "private-reloaded-points.f64"), replay.Native.pointer(stats))
    if (not np.isfinite(stats).all() or np.any(stats <= 0) or np.any(stats != np.floor(stats))
            or any(int(stats[index]) != before["num_submaps"] for index in (0, 2, 3))):
        raise ValueError("complete_fresh_process_controlled_reload_required")
    comparison = mapping.compare_points(args.output / "private-saved-points.f64",
                                        args.output / "private-reloaded-points.f64", int(stats[1]))
    if mapping.inventory_map(args.output / "map") != before:
        raise ValueError("controlled_map_changed_during_reload")
    save(args.output / "reload-report.json", {
        "result": "controlled_map_fresh_process_round_trip_not_relocalization",
        "pid": os.getpid(), "mapInventory": before, "comparison": comparison,
        "effectiveConfigFilesSha256": config_hashes(args.output / "control-config"),
        "privateReloadedPointsSha256": sha256(args.output / "private-reloaded-points.f64"),
        "boundaries": {name: False for name in BOUNDARIES},
    })


def run(args):
    new_output(args.output, (args.data_root, args.work, args.adapter, args.mapping_adapter, args.upstream))
    library = mapping.verify_extension(args.mapping_adapter, args.work)
    control, counts, loaded = {}, {}, {}

    class NativeControl(replay.Native):
        def __init__(self, odometry_library, baseline, module):
            control.update(make_control(baseline, args.output / "control-config"))
            save(args.output / "control-manifest.json", control)
            super().__init__(odometry_library, args.output / "control-config", module)
            self.mapping = mapping.MappingLibrary(library)
            self.closing, self.remaining = False, 0
            try:
                self.mapping.call("open")
                loaded.update(actual_libraries(args.work, args.adapter, args.mapping_adapter))
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
                                  os.fsencode(args.output / "private-saved-points.f64"), self.pointer(stats))
                if not np.isfinite(stats).all() or np.any(stats[:4] != np.floor(stats[:4])):
                    raise ValueError("finite_integer_controlled_mapping_counts_required")
                counts.update({name: int(value) if index < 4 else float(value)
                               for index, (name, value) in enumerate(zip((
                                   "finalizedFramesFedOnce", "submaps", "points", "savedFrames",
                                   "minimumLoopTravel", "submapTravel"), stats))})
                return self.remaining
            finally:
                self.mapping.call("shutdown")

    def mapped(native, report):
        validate_mapping_counts(counts, report["processedScans"] - report["initializationScans"])
        report["purpose"] = "controlled_full_mapping_replay_not_navigation"
        report["boundaries"].update(globalMappingExecuted=True, loopClosureAlgorithmEnabled=True)
        report["effectiveConfigurationDirectory"] = "control-config"
        report["baselineConfigurationDirectory"] = "config"
        report["controlManifestSha256"] = sha256(args.output / "control-manifest.json")

    started = time.monotonic()
    try:
        base = replay.run(args, native_factory=NativeControl, on_native_closed=mapped)
        validate_mapping_counts(counts, base["estimatedPoses"])
        inventory = mapping.inventory_map(args.output / "map")
        if (inventory["num_all_frames"] != counts["savedFrames"]
                or inventory["num_submaps"] != counts["submaps"]):
            raise ValueError("saved_controlled_map_counts_mismatch")
        child = [sys.executable, "-B", str(Path(__file__).resolve()), "reload",
                 "--mapping-adapter", str(args.mapping_adapter), "--work", str(args.work),
                 "--output", str(args.output)]
        with (args.output / "private-reload.log").open("x") as log:
            result = subprocess.run(child, stdout=log, stderr=log,
                                    env=replay.library_environment(args.work), timeout=180)
        if result.returncode:
            raise RuntimeError("controlled_fresh_process_reload_failed_see_private_log")
        reloaded = json.loads((args.output / "reload-report.json").read_text())
        if (reloaded["pid"] == os.getpid() or reloaded["mapInventory"] != inventory
                or reloaded["effectiveConfigFilesSha256"] != control["effectiveFilesSha256"]):
            raise ValueError("independent_controlled_reload_contract_required")
        for name in ("private-native.log", "private-reload.log"):
            check_reload_log(args.output / name)
        capture = json.loads(args.manifest.read_text())
        verify(capture, args.data_root)
        if (config_hashes(args.output / "config") != control["baselineFilesSha256"]
                or config_hashes(args.output / "control-config") != control["effectiveFilesSha256"]):
            raise ValueError("control_configuration_changed_during_execution")
        sources = args.output / "source-snapshot"
        sources.mkdir(mode=0o700)
        for name in SOURCE_FILES:
            shutil.copyfile(ROOT / "tools" / name, sources / name)
        report = {
            "result": "controlled_full_map_saved_and_reloaded_not_geometry_acceptance",
            "dataset": args.dataset, "timingHypothesis": args.hypothesis,
            "pid": os.getpid(), "hostMachine": platform.machine(),
            "sourceCommits": base["sourceCommits"], "mapping": counts, "mapInventory": inventory,
            "roundTrip": reloaded["comparison"], "actualLoadedLibrariesSha256": loaded,
            "sourceSha256": {name: sha256(sources / name) for name in SOURCE_FILES},
            "sourceVersions": {name: metadata.version(name) for name in ("numpy", "rosbags", "json5")},
            "mappingBuildReportSha256": sha256(args.mapping_adapter / "build-report.json"),
            "controlManifestSha256": sha256(args.output / "control-manifest.json"),
            "captureManifestSha256": sha256(args.manifest),
            "allOriginalCapturesUnchangedBeforeAndAfter": True,
            "totalWallSecondsIncludingReload": time.monotonic() - started,
            "filesSha256": {name: sha256(args.output / name) for name in (
                "report.json", "private-trajectory.npz", "private-native.log", "reload-report.json",
                "private-reload.log", "private-saved-points.f64", "private-reloaded-points.f64")},
            "boundaries": {name: False for name in BOUNDARIES},
        }
        save(args.output / "controlled-mapping-report.json", report)
        return report
    except BaseException as error:
        if args.output.is_dir():
            save(args.output / "controlled-mapping-report.json", {
                "result": "failed_or_interrupted", "errorType": type(error).__name__,
                "boundaries": {name: False for name in BOUNDARIES},
            })
        raise


def label(dataset, hypothesis, repeat):
    return f"{dataset}-{'start' if hypothesis == 'header_is_scan_start' else 'end'}-repeat{repeat}"


def worker_command(args, dataset, hypothesis, output):
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "run"]
    for name in ("manifest", "data_root", "diagnostic", "profile", "adapter", "mapping_adapter", "work", "upstream"):
        command.extend(("--" + name.replace("_", "-"), str(getattr(args, name))))
    return command + ["--output", str(output), "--dataset", dataset, "--hypothesis", hypothesis]


def batch(args):
    new_output(args.output, (args.data_root, args.work, args.adapter, args.mapping_adapter, args.upstream))
    args.output.mkdir(mode=0o700, parents=True)
    report = {"result": "running", "runnerSourceSha256": sha256(__file__), "runs": {}}
    try:
        for dataset, hypothesis, repeat in CONDITIONS:
            name = label(dataset, hypothesis, repeat)
            destination = args.output / name
            print(json.dumps({"phase": "controlled_full_mapping_started", "condition": name}), flush=True)
            with (args.output / (name + ".private-command.log")).open("x") as log:
                child = subprocess.run(worker_command(args, dataset, hypothesis, destination),
                                       stdout=log, stderr=log, timeout=300,
                                       env=replay.library_environment(args.work))
            report["runs"][name] = {"exitCode": child.returncode}
            if child.returncode:
                raise RuntimeError("controlled_mapping_process_failed_see_private_log")
            entry = json.loads((destination / "controlled-mapping-report.json").read_text())
            if entry["result"] != "controlled_full_map_saved_and_reloaded_not_geometry_acceptance":
                raise ValueError("complete_controlled_mapping_process_required")
            report["runs"][name]["reportSha256"] = sha256(destination / "controlled-mapping-report.json")
            (args.output / "collection-report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({"phase": "controlled_full_mapping_completed", "condition": name,
                              "completed": len(report["runs"]), "submaps": entry["mapping"]["submaps"],
                              "points": entry["mapping"]["points"]}), flush=True)
        report["result"] = "twelve_controlled_full_mapping_processes_completed_not_geometry_acceptance"
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        (args.output / "collection-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("batch", "run", "reload"):
        sub = commands.add_parser(command)
        names = ("work", "mapping-adapter", "output")
        if command != "reload":
            names += ("manifest", "data-root", "diagnostic", "profile", "adapter", "upstream")
        for name in names:
            sub.add_argument("--" + name, type=Path, required=True)
        if command == "run":
            sub.add_argument("--dataset", choices=("static", "dynamic"), required=True)
            sub.add_argument("--hypothesis", choices=replay.HYPOTHESES, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    if (platform.system() != "Linux" or platform.machine() != "x86_64"
            or os.geteuid() == 0 or sys.prefix == sys.base_prefix):
        raise ValueError("ordinary_user_isolated_x86_linux_environment_required")
    if metadata.version("json5") != "0.12.1" or metadata.version("numpy") != "2.5.4":
        raise ValueError("pinned_control_parser_and_original_numpy_required")
    old_umask = os.umask(0o077)
    try:
        result = {"batch": batch, "run": run, "reload": reload_map}[args.command](args)
        if result is not None:
            print(json.dumps({"result": result["result"]}), flush=True)
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
