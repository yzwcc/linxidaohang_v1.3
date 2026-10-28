"""Independent ARM64 recorded full-map controls; never initialize ROS or motion."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import numpy as np

import glim_target_mapping as mapping
import glim_target_replay as replay
from run_glim_mapping_trial import compare_points, inventory_map, save_json
from slam_target_probe import sha256, verify_bundle


PURPOSE = "arm64_recorded_controlled_full_mapping"
BASELINE_MANIFEST = "0c399c4be893a56283f15a69ebf78ce576200424d3f1be7e4ae078049c56b53a"
EFFECTIVE_MANIFEST = "1a39c0f550099d959c66038e46628df4326ab2ed32ef2c4963d60100581acd91"
BOUNDARIES = mapping.BOUNDARIES + (
    "productionConfigurationSelected", "crossArchitectureGeometryAccepted",
    "completeMapBitIdentityProven",
)
FILES = ("report.json", "private-trajectory.npz", "private-native.log", "reload-report.json",
         "private-reload.log", "private-saved-points.f64", "private-reloaded-points.f64")


def configuration_files(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("direct_control_configuration_required")
    paths = list(directory.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("flat_direct_control_configuration_required")
    return {path.name: sha256(path) for path in sorted(paths)}


def verify_control(root):
    control = json.loads((root / "control-manifest.json").read_text())
    before = configuration_files(root / "baseline-config")
    after = configuration_files(root / "config")
    if (before != control["baselineFilesSha256"] or after != control["effectiveFilesSha256"]
            or before["trial-manifest.json"] != BASELINE_MANIFEST
            or after["trial-manifest.json"] != EFFECTIVE_MANIFEST
            or control["semanticParameterChanges"] != [
                {"file": "config_preprocess.json", "path": ["preprocess", "num_threads"], "before": 2, "after": 1}]
            or control["productionConfigurationSelected"] is not False
            or control["sensorMappingOdometryAndRangeParametersUnchanged"] is not True):
        raise ValueError("byte_identical_host_single_parameter_control_required")
    for directory, actual in ((root / "baseline-config", before), (root / "config", after)):
        trial = json.loads((directory / "trial-manifest.json").read_text())
        if (trial["files"] != {name: digest for name, digest in actual.items() if name != "trial-manifest.json"}
                or any(trial[name] is not False for name in (
                    "robotCalibrationValidated", "navigationValidated", "deploymentApproved"))):
            raise ValueError("consistent_unapproved_control_manifest_required")
    value = json.loads((root / "config/config_preprocess.json").read_text())["preprocess"]["num_threads"]
    if type(value) is not int or value != 1:
        raise ValueError("explicit_single_preprocessing_thread_required")
    return control


def validate_inputs(args):
    replay.validate_environment(args.root, args.core_root)
    manifest = replay.verify_replay_bundle(args.root, PURPOSE)
    verify_bundle(args.core_root)
    verify_control(args.root)
    if (sha256(args.core_root / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]
            or sha256(args.root / "control-manifest.json") != manifest["controlManifestSha256"]
            or sha256(args.root / "lib/liblingxi_glim_mapping.so") != manifest["mappingAdapterSha256"]
            or sha256(args.root / "lib/liblingxi_glim_offline.so") != manifest["odometryAdapterSha256"]
            or args.output.parent != args.root):
        raise ValueError("verified_controlled_native_artifacts_and_private_output_required")
    return manifest


def loaded_libraries(root, core, manifest, text):
    expected = {Path(name).name: core / name for name in manifest["files"] if name.startswith("lib/")}
    expected.update({name: root / "lib" / name
                     for name in ("liblingxi_glim_offline.so", "liblingxi_glim_mapping.so")})
    found = {}
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6:
            continue
        path = Path(fields[5])
        if path.name not in expected:
            continue
        if path.resolve() != expected[path.name].resolve():
            raise ValueError("controlled_native_library_outside_verified_directories")
        found[path.name] = sha256(path)
    if not {"libglim.so", "libodometry_estimation_cpu.so",
            "liblingxi_glim_offline.so", "liblingxi_glim_mapping.so"}.issubset(found):
        raise ValueError("actual_controlled_full_mapping_libraries_required")
    return found


def reload_map(args):
    validate_inputs(args)
    before = inventory_map(args.output / "map")
    stats = np.zeros(4)
    native = mapping.MappingLibrary(args.root / "lib/liblingxi_glim_mapping.so")
    native.call("reload", os.fsencode(args.root / "config"), os.fsencode(args.output / "map"),
                os.fsencode(args.output / "private-reloaded-points.f64"), replay.Native.pointer(stats))
    if (not np.isfinite(stats).all() or np.any(stats <= 0) or np.any(stats != np.floor(stats))
            or any(int(stats[index]) != before["num_submaps"] for index in (0, 2, 3))):
        raise ValueError("complete_independent_controlled_reload_counts_required")
    comparison = compare_points(args.output / "private-saved-points.f64",
                                args.output / "private-reloaded-points.f64", int(stats[1]))
    if inventory_map(args.output / "map") != before:
        raise ValueError("controlled_target_map_changed_during_reload")
    save_json(args.output / "reload-report.json", {
        "result": "independent_controlled_arm64_map_reloaded_not_relocalization",
        "pid": os.getpid(), "machine": platform.machine(), "mapInventory": before,
        "comparison": comparison, "effectiveConfiguration": configuration_files(args.root / "config"),
        "boundaries": {name: False for name in BOUNDARIES},
    })


def _run(args):
    manifest = validate_inputs(args)
    counts, loaded = {}, {}

    class NativeControl(replay.Native):
        def __init__(self, *parameters):
            super().__init__(*parameters)
            self.mapping = mapping.MappingLibrary(args.root / "lib/liblingxi_glim_mapping.so")
            self.closing, self.remaining = False, 0
            try:
                self.mapping.call("open")
                core = json.loads((args.core_root / "bundle-manifest.json").read_text())
                loaded.update(loaded_libraries(args.root, args.core_root, core, Path("/proc/self/maps").read_text()))
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
                counts.update({
                    name: int(value) if index < 4 else float(value)
                    for index, (name, value) in enumerate(zip((
                        "finalizedFramesFedOnce", "submaps", "points", "savedFrames",
                        "minimumLoopTravel", "submapTravel"), stats))
                })
                return self.remaining
            finally:
                self.mapping.call("shutdown")

    def mapped(report):
        mapping.validate_mapping_counts(counts, report["estimatedPoses"])
        report["mapping"] = counts
        report["boundaries"]["globalMappingExecuted"] = True
        report["controlledMappingRunnerSha256"] = sha256(__file__)

    started = time.monotonic()
    base = replay.run(args, native_factory=NativeControl, on_native_closed=mapped, bundle_purpose=PURPOSE)
    inventory = inventory_map(args.output / "map")
    if (inventory["num_all_frames"] != counts["savedFrames"]
            or inventory["num_submaps"] != counts["submaps"]):
        raise ValueError("complete_controlled_saved_map_inventory_required")
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "reload",
               "--root", str(args.root), "--core-root", str(args.core_root), "--output", str(args.output)]
    with (args.output / "private-reload.log").open("x") as log:
        child = subprocess.run(command, stdout=log, stderr=log, env=os.environ.copy(), timeout=180)
    if child.returncode:
        raise RuntimeError("controlled_target_reload_failed_see_private_log")
    reloaded = json.loads((args.output / "reload-report.json").read_text())
    if (type(reloaded["pid"]) is not int or reloaded["pid"] <= 0 or reloaded["pid"] == os.getpid()
            or reloaded["machine"] != "aarch64"
            or reloaded["result"] != "independent_controlled_arm64_map_reloaded_not_relocalization"
            or reloaded["mapInventory"] != inventory or inventory_map(args.output / "map") != inventory
            or reloaded["comparison"]["pointCount"] != counts["points"]
            or reloaded["boundaries"] != {name: False for name in BOUNDARIES}
            or reloaded["effectiveConfiguration"] != configuration_files(args.root / "config")):
        raise ValueError("complete_independent_controlled_target_reload_required")
    validate_inputs(args)
    report = {
        "result": "complete_controlled_arm64_map_saved_and_reloaded_not_geometry_acceptance",
        "dataset": args.dataset, "timingHypothesis": args.hypothesis, "pid": os.getpid(),
        "target": base["target"], "sourceCommits": base["sourceCommits"],
        "runnerSha256": sha256(__file__), "replayManifestSha256": sha256(args.root / "replay-manifest.json"),
        "controlManifestSha256": manifest["controlManifestSha256"],
        "mapping": counts, "mapInventory": inventory, "roundTrip": reloaded["comparison"],
        "actualLoadedLibrariesSha256": loaded,
        "totalWallSecondsIncludingReload": time.monotonic() - started,
        "filesSha256": {name: sha256(args.output / name) for name in FILES},
        "boundaries": {name: False for name in BOUNDARIES},
    }
    save_json(args.output / "controlled-report.json", report)
    return report


def run(args):
    if args.output.exists():
        raise ValueError("existing_controlled_target_trial_is_preserved")
    try:
        return _run(args)
    except BaseException as error:
        if args.output.is_dir():
            save_json(args.output / "controlled-report.json", {
                "result": "failed_or_interrupted", "errorType": type(error).__name__,
                "boundaries": {name: False for name in BOUNDARIES},
            })
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "reload"):
        sub = commands.add_parser(command)
        for name in ("root", "core-root", "output"):
            sub.add_argument("--" + name, type=Path, required=True)
        if command == "run":
            sub.add_argument("--data-root", type=Path, required=True)
            sub.add_argument("--dataset", choices=("static", "dynamic"), required=True)
            sub.add_argument("--hypothesis", choices=replay.HYPOTHESES, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    old_umask = os.umask(0o077)
    try:
        if args.command == "reload":
            reload_map(args)
        else:
            result = run(args)
            print(json.dumps({"result": result["result"], "submaps": result["mapping"]["submaps"]}))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
