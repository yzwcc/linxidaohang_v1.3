"""Opt-in ARM64 recorded-journal supervision; no ROS initialization or actuation."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time

import run_glim_supervised_replay as shared


ROOT = Path(__file__).resolve().parents[1]
RESULT = "arm64_recorded_native_supervision_closed_not_live_or_navigation_acceptance"
PURPOSE = "arm64_identical_callback_input_glim_diagnostic"
REFERENCE_RESULT = "same_input_session_closed_not_calibration_or_navigation_acceptance"
REFERENCE_FILES = ("private-callbacks.cdrjournal", "private-native.log", "private-results.npz")
WORKER_SOURCES = tuple(
    "robot-slam/lingxi_slam/" + name + ".py" for name in ("__init__", "messages", "stream", "worker")
)
SOURCES = tuple(dict.fromkeys((*shared.SOURCE_NAMES, *WORKER_SOURCES,
    "tools/glim_supervised_target.py", "tools/glim_target_worker.py",
    "tools/glim_target_replay.py", "tools/slam_target_probe.py")))


def hashes():
    from verify_slam_capture import sha256
    return {name: sha256(ROOT / name) for name in SOURCES}


def child_environment(root, core):
    root, core = Path(root).resolve(strict=True), Path(core).resolve(strict=True)
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
        "AMENT_PREFIX_PATH": "/opt/ros/humble",
        "PYTHONPATH": ("/opt/ros/humble/local/lib/python3.10/dist-packages:"
                       "/opt/ros/humble/lib/python3.10/site-packages"),
        "LD_LIBRARY_PATH": ":".join((str(root / "lib"), str(core / "lib"),
                                    "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
        "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
        "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }


def reference_integrity(reference, manifest_digest, configuration, hypothesis):
    from slam_callback_journal import inspect
    from verify_slam_capture import sha256

    report = shared.load(reference / "report.json")
    journal = reference / "private-callbacks.cdrjournal"
    if (report["result"] != REFERENCE_RESULT or report["mode"] != "live"
            or report["target"]["machine"] != "aarch64"
            or report["timingHypothesis"] != hypothesis
            or report["replayManifestSha256"] != manifest_digest
            or report["effectiveConfiguration"] != configuration
            or set(report["filesSha256"]) != set(REFERENCE_FILES)
            or any(sha256(reference / name) != report["filesSha256"][name] for name in REFERENCE_FILES)
            or inspect(journal) != report["journal"] or not report["journal"]["records"]):
        raise ValueError("complete_unchanged_arm64_live_reference_required")
    worker, expected = report["worker"], report["journal"]
    counts = worker["counts"]
    if (worker["state"] != "stopped" or worker["failure"] or worker["threadAlive"] is not False
            or worker["queuedMessages"] or worker["inFlightMessages"]
            or any(type(counts[name]) is not int for name in (
                "completedMessages", "acceptedImu", "acceptedClouds", "discardedMessages", "rejectedMessages"))
            or counts["completedMessages"] != expected["records"]
            or counts["acceptedImu"] != expected["counts"]["imu"]
            or counts["acceptedClouds"] != expected["counts"]["cloud"]
            or counts["discardedMessages"] or counts["rejectedMessages"]):
        raise ValueError("full_journal_not_only_completed_prefix_required")
    return report


def inputs(args):
    shared.replay_rate(args.rate)
    if (sys.platform != "linux" or platform.machine() != "aarch64"
            or os.geteuid() == 0 or sys.version_info[:2] != (3, 10)):
        raise ValueError("ordinary_user_arm64_humble_python310_required")
    import numpy as np
    from build_slam_cpu_trial import PINS
    from glim_target_controlled_mapping import configuration_files, verify_control
    from glim_target_replay import validate_environment, verify_replay_bundle
    from run_glim_offline_trial import explicit_diagnostic_profile
    from slam_target_probe import verify_bundle
    from verify_slam_capture import sha256

    validate_environment(args.root, args.core_root)
    expected_env = child_environment(args.root, args.core_root)
    if any(os.environ.get(key) != value for key, value in expected_env.items()):
        raise ValueError("explicit_arm64_child_environment_required")
    manifest = verify_replay_bundle(args.root, PURPOSE)
    core = verify_bundle(args.core_root)
    if sha256(args.core_root / "bundle-manifest.json") != manifest["coreBundleManifestSha256"]:
        raise ValueError("same_frozen_arm64_core_required")
    verify_control(args.root)
    for name in WORKER_SOURCES:
        if sha256(ROOT / name) != manifest["files"][name]["sha256"]:
            raise ValueError("unchanged_arm64_worker_source_required")
    explicit_diagnostic_profile(shared.load(args.root / "private-profile.json"),
                                shared.load(args.root / "private-sensor-diagnostic.json"), args.hypothesis)
    reference = reference_integrity(args.reference, sha256(args.root / "replay-manifest.json"),
                                    configuration_files(args.root / "config"), args.hypothesis)
    if reference["target"]["numpy"] != np.__version__:
        raise ValueError("same_recorded_arm64_numpy_version_required")
    return {
        "sourceSha256": hashes(), "sourceCommits": PINS,
        "coreBundleManifestSha256": sha256(args.core_root / "bundle-manifest.json"),
        "replayManifestSha256": sha256(args.root / "replay-manifest.json"),
        "referenceReportSha256": sha256(args.reference / "report.json"),
        "journalSha256": reference["filesSha256"]["private-callbacks.cdrjournal"],
        "journal": reference["journal"], "configurationSha256": reference["effectiveConfiguration"],
        "referenceLibrariesSha256": reference["actualLoadedLibrariesSha256"],
        "profileSha256": sha256(args.root / "private-profile.json"),
        "diagnosticSha256": sha256(args.root / "private-sensor-diagnostic.json"),
        "verifiedCoreLibrariesSha256": {
            Path(name).name: entry["sha256"] for name, entry in core["files"].items() if name.startswith("lib/")
        },
    }


def child(args, sink):
    import numpy as np
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import Imu, PointCloud2
    from glim_target_worker import observed_libraries
    from run_glim_offline_trial import Native
    from slam_target_probe import verify_bundle

    integrity = inputs(args)
    manifest = shared.load(args.root / "replay-manifest.json")
    core = verify_bundle(args.core_root)
    # Load type support before starting the worker's unchanged input watchdog.
    from rclpy.type_support import check_for_type_support
    for message_type in (Imu, PointCloud2):
        check_for_type_support(message_type)

    def loaded():
        libraries = observed_libraries(args.root, args.core_root, manifest, core)
        if libraries != integrity["referenceLibrariesSha256"]:
            raise ValueError("same_actual_arm64_native_libraries_required")
        return libraries

    return shared.recorded_child(
        args, sink, integrity=integrity,
        native_factory=lambda: Native(args.root / "lib/liblingxi_glim_offline.so", args.root / "config",
                                      args.core_root / "lib/libodometry_estimation_cpu.so"),
        verify_loaded=loaded,
        decode=lambda kind, payload: deserialize_message(payload, Imu if kind == "imu" else PointCloud2),
        verify_inputs=lambda: inputs(args),
        versions={"numpy": np.__version__, "decoder": "installed_humble_rclpy", "python": platform.python_version()},
    )


def stage(args):
    from verify_slam_capture import sha256

    runtime = args.output / "runtime-source"
    names = set(SOURCES)
    names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "tools").glob("*.py"))
    copied = {}

    def copy(source, destination):
        if source.is_symlink() or not source.is_file():
            raise ValueError("direct_arm64_staging_source_required")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if sha256(source) != sha256(destination):
            raise ValueError("identical_arm64_staging_bytes_required")

    for name in sorted(names):
        copy(ROOT / name, runtime / name)
        copied[name] = sha256(runtime / name)
    reference = args.output / "private-reference"
    for name in ("report.json", *REFERENCE_FILES):
        copy(args.reference / name, reference / name)
    return argparse.Namespace(**{**vars(args), "reference": reference}), runtime, copied


def run(args):
    from verify_slam_capture import sha256

    if getattr(args, "operator_confirmed", False) is not True:
        raise ValueError("stationary_supervised_estop_confirmation_required")
    integrity = inputs(args)
    allowed = ((ROOT / ".local").resolve(), (Path.home() / ".cache/lingxi-next").resolve())
    if (args.output.exists() or not any(args.output.is_relative_to(root) for root in allowed)
            or any(args.output == path or path in args.output.parents or args.output in path.parents
                   for path in (args.root, args.core_root, args.reference))):
        raise ValueError("new_private_arm64_output_outside_frozen_inputs_required")
    args.output.mkdir(mode=0o700, parents=True)
    started = time.monotonic()
    staged, runtime, copied = stage(args)
    if inputs(staged) != integrity:
        raise ValueError("same_staged_arm64_inputs_required")
    command = ["/usr/bin/nice", "-n", "10", sys.executable, "-B",
               str(runtime / "tools/glim_supervised_target.py"), "child"]
    for name in ("root", "core_root", "reference", "output", "hypothesis", "rate"):
        command.extend(("--" + name.replace("_", "-"), str(getattr(staged, name))))
    owner = shared.ProcessSupervisor(
        command, cwd=args.output, environment=child_environment(args.root, args.core_root),
        stderr_path=args.output / "private-child.log", limits=shared.LIMITS,
    )
    report = {
        "schemaVersion": 1, "result": "running", "inputIntegrity": integrity,
        "limits": asdict(shared.LIMITS), "boundaries": dict.fromkeys(shared.BOUNDARIES, False),
        "replayRate": args.rate, "pacedRecordedInputNotLivePerformance": True,
        "privateStagingSecondsNotRealtimePerformance": time.monotonic() - started,
        "runtimeSourceFilesSha256": copied,
    }
    try:
        report["process"] = shared.coordinate(owner, integrity["journal"]["records"], 110.)
        result = shared.load(args.output / "child-report.json")
        shared.complete_child(result, integrity["journal"])
        if (result["result"] != shared.CHILD_RESULT or result["machine"] != "aarch64"
                or result["session"] != report["process"]["session"] or result["replayRate"] != args.rate
                or result["inputIntegrity"] != integrity or inputs(args) != integrity):
            raise ValueError("same_complete_owned_arm64_child_required")
        report["childReportSha256"] = sha256(args.output / "child-report.json")
        report["result"] = RESULT
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        report["process"] = owner.stop()
        report["privateLogSha256"] = sha256(args.output / "private-child.log") if (
            args.output / "private-child.log").exists() else None
        shared.private_json(args.output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "child"):
        command = commands.add_parser(name)
        for field in ("root", "core-root", "reference", "output"):
            command.add_argument("--" + field, type=Path, required=True)
        command.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
        command.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
        if name == "run":
            command.add_argument("--operator-confirmed", action="store_true")
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    previous = os.umask(0o077)
    try:
        if args.command == "child":
            with os.fdopen(os.dup(1), "wb", buffering=0) as sink:
                os.dup2(2, 1)
                return child(args, sink)
        print(json.dumps({"result": run(args)["result"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
