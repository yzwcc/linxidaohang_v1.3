"""ARM64 recorded global mapping and fresh-process reload; no live ROS or motion."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import numpy as np

import glim_target_replay as replay
from run_glim_mapping_trial import MappingLibrary, compare_points, inventory_map, save_json
from slam_target_probe import sha256, verify_bundle


PURPOSE = "arm64_recorded_glim_mapping_trial"
BOUNDARIES = (
    "rosNodeStarted", "liveSensorsSubscribed", "motionCommandsSent",
    "timingHypothesisValidated", "robotCalibrationValidated", "geometricAccuracyValidated",
    "loopClosureValidated", "relocalizationValidated", "navigationValidated",
    "targetRealtimePerformanceValidated", "voiceConcurrencyValidated", "deploymentApproved",
    "rawDataPublished",
)


def validate_inputs(args):
    replay.validate_environment(args.root, args.core_root)
    manifest = replay.verify_replay_bundle(args.root, PURPOSE)
    verify_bundle(args.core_root)
    if (sha256(args.core_root / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]
            or sha256(args.root / "lib/liblingxi_glim_mapping.so") != manifest["mappingAdapterSha256"]
            or sha256(args.root / "lib/liblingxi_glim_offline.so") != manifest["odometryAdapterSha256"]
            or args.output.parent != args.root):
        raise ValueError("explicit_verified_mapping_artifacts_and_private_output_required")
    return manifest


def validate_mapping_counts(mapping, estimated):
    if (any(type(mapping.get(name)) is not int or mapping[name] <= 0
            for name in ("finalizedFramesFedOnce", "submaps", "points", "savedFrames"))
            or mapping["savedFrames"] != estimated
            or mapping["finalizedFramesFedOnce"] != estimated
            or not np.isfinite([mapping["minimumLoopTravel"], mapping["submapTravel"]]).all()
            or not 0 <= mapping["submapTravel"] < mapping["minimumLoopTravel"]):
        raise ValueError("complete_once_only_short_sequence_mapping_required")


def reload_map(args):
    validate_inputs(args)
    before = inventory_map(args.output / "map")
    stats = np.zeros(4)
    native = MappingLibrary(args.root / "lib/liblingxi_glim_mapping.so")
    native.call("reload", os.fsencode(args.root / "config"), os.fsencode(args.output / "map"),
                os.fsencode(args.output / "private-reloaded-points.f64"), replay.Native.pointer(stats))
    if (not np.isfinite(stats).all() or np.any(stats != np.floor(stats)) or np.any(stats <= 0)
            or any(int(stats[index]) != before["num_submaps"] for index in (0, 2, 3))):
        raise ValueError("finite_matching_target_reload_graph_counts_required")
    comparison = compare_points(args.output / "private-saved-points.f64",
                                args.output / "private-reloaded-points.f64", int(stats[1]))
    if before != inventory_map(args.output / "map"):
        raise ValueError("target_saved_map_changed_during_reload")
    save_json(args.output / "reload-report.json", {
        "result": "fresh_process_arm64_map_round_trip_passed_not_relocalization",
        "pid": os.getpid(), "machine": platform.machine(),
        "loadedSubmaps": int(stats[0]), "loadedGraphFactors": int(stats[2]),
        "loadedGraphValues": int(stats[3]), "comparison": comparison, "mapInventory": before,
        "privateReloadedPointsSha256": sha256(args.output / "private-reloaded-points.f64"),
        "runnerSha256": sha256(__file__), "boundaries": {name: False for name in BOUNDARIES},
    })


def _run(args):
    manifest = validate_inputs(args)
    mapping = {}

    class NativeMapping(replay.Native):
        def __init__(self, *parameters):
            super().__init__(*parameters)
            self.mapping = MappingLibrary(args.root / "lib/liblingxi_glim_mapping.so")
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
                if (not np.isfinite(stats).all() or np.any(stats[:4] <= 0)
                        or np.any(stats[:4] != np.floor(stats[:4]))):
                    raise ValueError("finite_positive_integer_target_mapping_counts_required")
                mapping.update({
                    name: int(value) if index < 4 else float(value)
                    for index, (name, value) in enumerate(zip((
                        "finalizedFramesFedOnce", "submaps", "points",
                        "savedFrames", "minimumLoopTravel", "submapTravel",
                    ), stats))
                })
                return self.remaining
            finally:
                self.mapping.call("shutdown")

    def mapped(report):
        validate_mapping_counts(mapping, report["estimatedPoses"])
        report["mapping"] = mapping
        report["boundaries"]["globalMappingExecuted"] = True
        report["mappingRunnerSha256"] = sha256(__file__)

    started = time.monotonic()
    base = replay.run(args, native_factory=NativeMapping, on_native_closed=mapped, bundle_purpose=PURPOSE)
    inventory = inventory_map(args.output / "map")
    if (inventory["num_all_frames"] != mapping["savedFrames"]
            or inventory["num_submaps"] != mapping["submaps"]):
        raise ValueError("target_saved_map_inventory_counts_mismatch")
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "reload",
               "--root", str(args.root), "--core-root", str(args.core_root), "--output", str(args.output)]
    with (args.output / "private-reload.log").open("x") as log:
        child = subprocess.run(command, stdout=log, stderr=log, env=os.environ.copy(), timeout=180)
    if child.returncode:
        raise RuntimeError("target_fresh_process_reload_failed_see_private_log")
    reloaded = json.loads((args.output / "reload-report.json").read_text())
    if (reloaded["pid"] == os.getpid() or reloaded["machine"] != "aarch64"
            or reloaded["result"] != "fresh_process_arm64_map_round_trip_passed_not_relocalization"
            or reloaded["mapInventory"] != inventory or inventory_map(args.output / "map") != inventory
            or reloaded["comparison"]["pointCount"] != mapping["points"]
            or reloaded["boundaries"] != {name: False for name in BOUNDARIES}):
        raise ValueError("independent_target_reload_contract_mismatch")
    report = {
        "purpose": "actual_arm64_recorded_global_mapping_and_persistence_not_navigation",
        "result": "arm64_nonempty_map_saved_and_fresh_process_reloaded_not_quality_acceptance",
        "dataset": args.dataset, "timingHypothesis": args.hypothesis, "replayPid": os.getpid(),
        "runnerSha256": sha256(__file__),
        "replayManifestSha256": sha256(args.root / "replay-manifest.json"),
        "mappingBuildReportSha256": manifest["adapterBuildReportSha256"],
        "baseReplayReportSha256": sha256(args.output / "report.json"),
        "reloadReportSha256": sha256(args.output / "reload-report.json"),
        "mapping": mapping, "mapInventory": inventory, "roundTrip": reloaded["comparison"],
        "privateSavedPointsSha256": sha256(args.output / "private-saved-points.f64"),
        "privateReloadedPointsSha256": reloaded["privateReloadedPointsSha256"],
        "privateReloadLogSha256": sha256(args.output / "private-reload.log"),
        "totalWallSecondsIncludingReload": time.monotonic() - started,
        "target": base["target"], "sourceCommits": base["sourceCommits"],
        "checks": {name: True for name in (
            "globalMappingExecutedOnArm64", "nonemptyMapSavedOnArm64",
            "allFinalizedFramesFedExactlyOnce", "freshArm64ProcessReloadPassed",
            "finiteSameCountPointRoundTripPassed", "mapUnchangedDuringReload",
        )},
        "boundaries": {name: False for name in BOUNDARIES},
    }
    save_json(args.output / "mapping-report.json", report)
    return report


def run(args):
    if args.output.exists():
        raise ValueError("existing_target_mapping_trial_is_preserved")
    try:
        return _run(args)
    except BaseException as error:
        if args.output.is_dir():
            save_json(args.output / "mapping-report.json", {
                "result": "failed_or_interrupted", "errorType": type(error).__name__,
                "navigationValidated": False, "mapPersistencePassed": False,
            })
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run")
    loader = commands.add_parser("reload")
    for subparser in (runner, loader):
        for name in ("root", "core-root", "output"):
            subparser.add_argument("--" + name, type=Path, required=True)
    runner.add_argument("--data-root", type=Path, required=True)
    runner.add_argument("--dataset", choices=("static", "dynamic"), required=True)
    runner.add_argument("--hypothesis", choices=replay.HYPOTHESES, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    old_umask = os.umask(0o077)
    try:
        if args.command == "reload":
            reload_map(args)
        else:
            print(json.dumps(run(args), indent=2, allow_nan=False))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
