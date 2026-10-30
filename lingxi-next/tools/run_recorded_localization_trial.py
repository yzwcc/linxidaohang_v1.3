"""Supervised complete recorded-point tracking diagnostic; no robot access."""
import argparse
from collections import Counter
from dataclasses import asdict
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time

import numpy as np

import run_glim_point_output_trial as points
import run_glim_supervised_replay as replay
import run_seeded_localization_trial as seeded
from lingxi_slam.localization_tracking import LocalizationTracker
from lingxi_slam.process_protocol import session_id
from lingxi_slam.seeded_localization import RegistrationConfig, SeededMapRegistration
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
RESULT = "complete_recorded_localization_diagnostic_not_independent_accuracy_or_navigation"
SEED_KINDS = tuple(label for label, _ in seeded.diagnostic_seeds(np.eye(4)))
SOURCE_NAMES = (
    "tools/run_recorded_localization_trial.py", "tools/run_seeded_localization_trial.py",
    "robot-slam/lingxi_slam/seeded_localization.py",
    "robot-slam/lingxi_slam/localization_tracking.py", "robot-slam/lingxi_slam/point_data.py",
    "robot-slam/lingxi_slam/point_output.py",
)
METRICS = (
    "milliseconds", "overlap", "inlierRmsRaw", "inlierP95Raw",
    "seedTranslationCorrectionRaw", "seedRotationCorrectionRadians",
    "geometryInformationRatio", "nativeInformationRatio",
)
BOUNDARIES = {
    **dict.fromkeys(replay.BOUNDARIES, False),
    "recordedPoseUsedForInitialSeed": True, "possibleMappingSequenceReuse": True,
    "worldPointsConvertedBackToSensor": True, "automaticReseeding": False,
    "groundTruthAccuracyMeasured": False, "globalRelocalizationValidated": False,
    "localizationReady": False, "navigationReady": False,
}


def hashes():
    return {name: sha256(ROOT / name) for name in SOURCE_NAMES}


def statistics(values):
    ordered = sorted(values)
    count = len(ordered)
    return {
        "count": count, "minimum": ordered[0] if count else None,
        "p50": ordered[math.ceil(count * .5) - 1] if count else None,
        "p95": ordered[math.ceil(count * .95) - 1] if count else None,
        "maximum": ordered[-1] if count else None,
    }


class MeteredRegistration:
    """Keep bounded scalar measurements, never scan coordinates or transforms."""

    def __init__(self, registrar):
        self.registrar, self.calls, self.accepted = registrar, 0, 0
        self.reasons = Counter()
        self.metrics = {name: [] for name in METRICS}

    def register(self, scan, seed):
        if self.calls >= 20_000:
            raise ValueError("bounded_recorded_registration_calls_required")
        self.calls += 1
        estimate = self.registrar.register(scan, seed)
        self.accepted += estimate.accepted is True
        self.reasons.update(estimate.reasons)
        for name in METRICS:
            value = estimate.metrics.get(name)
            if value is not None:
                if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                    raise ValueError("finite_nonnegative_registration_metric_required")
                self.metrics[name].append(value)
        return estimate

    def report(self):
        return {
            "calls": self.calls, "accepted": self.accepted, "rejected": self.calls - self.accepted,
            "rejectionReasons": dict(self.reasons),
            "metrics": {name: statistics(values) for name, values in self.metrics.items()},
        }


def snapshot_summary(snapshot):
    return {
        "state": snapshot.state, "generation": snapshot.generation,
        "consecutiveMatches": snapshot.consecutive_matches, "lastMatchedFrame": snapshot.last_frame,
        "reason": snapshot.reason, "hasDiagnosticPose": snapshot.map_from_sensor is not None,
        "localizationReady": snapshot.localization_ready, "navigationReady": snapshot.navigation_ready,
    }


class TrackingSummary(points.PacketSummary):
    """Run all controls on the native worker owner, retaining only aggregates."""

    def __init__(self, map_points, seed_pose, reference, session, *,
                 clock=time.monotonic, registrar_factory=SeededMapRegistration):
        super().__init__()
        self.map_points, self.seed_pose, self.reference = map_points, seed_pose, reference
        self.session, self.clock = session_id(session), clock
        self.registrar_factory, self.controls = registrar_factory, []
        self.last_stamp, self.closed_controls = None, None

    def begin(self):
        super().begin()
        for label, seed in seeded.diagnostic_seeds(self.seed_pose):
            meter = MeteredRegistration(self.registrar_factory(self.map_points))
            tracker = LocalizationTracker(
                meter, session=self.session, input_mode="recorded", clock=self.clock)
            self.controls.append({
                "seedKind": label, "seed": seed, "meter": meter, "tracker": tracker,
                "states": Counter(), "firstTrackingFrame": None, "firstLostFrame": None,
                "firstLossReason": None, "lastObservation": None,
            })
        # The registration objects now own the cached geometry; no extra source copy.
        self.map_points = None

    def accept(self, packet):
        self.require_owner()
        if self.count == 0 and (
                packet.native_frame_id != self.reference["frameId"]
                or packet.algorithm_stamp != self.reference["algorithmStamp"]
                or len(packet.points) != self.reference["copiedPoints"]):
            raise ValueError("matching_preserved_first_packet_seed_reference_required")
        if self.last_stamp is not None and packet.algorithm_stamp <= self.last_stamp:
            raise ValueError("strictly_ordered_recorded_localization_packets_required")
        super().accept(packet)
        self.last_stamp = packet.algorithm_stamp
        # One receipt time for all controls; time spent in earlier controls counts.
        received = self.clock()
        for control in self.controls:
            tracker = control["tracker"]
            if self.count == 1:
                control["generation"] = tracker.begin(control.pop("seed"), packet, received_at=received)
            snapshot = tracker.observe(
                packet, session=self.session, generation=control["generation"], received_at=received)
            control["states"][snapshot.state] += 1
            if snapshot.state == "tracking" and control["firstTrackingFrame"] is None:
                control["firstTrackingFrame"] = packet.native_frame_id
            if snapshot.state == "lost" and control["firstLostFrame"] is None:
                control["firstLostFrame"], control["firstLossReason"] = (
                    packet.native_frame_id, snapshot.reason)
            control["lastObservation"] = snapshot_summary(snapshot)

    def finish(self, output):
        self.require_owner()
        controls = []
        for control in self.controls:
            tracker = control["tracker"]
            before_stop = snapshot_summary(tracker.snapshot())
            stopped = snapshot_summary(tracker.stop())
            controls.append({
                "seedKind": control["seedKind"], "observedPackets": sum(control["states"].values()),
                "stateCounts": dict(control["states"]),
                **{key: control[key] for key in (
                    "firstTrackingFrame", "firstLostFrame", "firstLossReason", "lastObservation")},
                "atNativeClose": before_stop, "afterExplicitStop": stopped,
                "registration": control["meter"].report(),
            })
        self.closed_controls = controls
        super().finish(output)

    def tracking_report(self, expected_poses):
        self.report(expected_poses)
        if (len(self.closed_controls) != len(SEED_KINDS)
                or any(value["observedPackets"] != self.count for value in self.closed_controls)):
            raise ValueError("complete_localization_control_accounting_required")
        return {
            "packets": self.count, "seedAttemptsPerControl": 1, "automaticReseeding": False,
            "inputMode": "recorded", "poseFrame": "lidar_not_robot_base",
            "controls": self.closed_controls,
        }


def seed_reference(args, frozen):
    reference = points.evidence(args.point_reference)
    original = replay.load(args.point_reference / "report.json")["inputIntegrity"]
    for key in ("journalSha256", "replayManifestSha256", "configurationSha256",
                "pointExtensionLibrarySha256", "nativeLibrariesSha256"):
        if original[key] != frozen[key]:
            raise ValueError("seed_reference_from_same_frozen_recording_required")
    sample = args.point_reference / "private-first-points.npz"
    local, pose, digest = seeded.load_recorded_scan(sample, RegistrationConfig())
    with np.load(sample, allow_pickle=False) as data:
        frame, stamp = int(data["frame_id"]), float(data["algorithm_stamp"])
    if frame != 1 or seeded.digest(sample) != digest:
        raise ValueError("unchanged_preserved_first_packet_seed_required")
    return pose, {
        "reportSha256": reference["privateReportsSha256"],
        "sampleSha256": digest, "frameId": frame, "algorithmStamp": stamp,
        "copiedPoints": len(local),
    }


def integrity(args):
    frozen = points.integrity(args)
    _, map_identity = seeded.load_saved_points(args.saved_map, RegistrationConfig())
    _, reference = seed_reference(args, frozen)
    import small_gicp
    return {
        **frozen, "localizationSourceSha256": hashes(), "savedMap": map_identity,
        "seedReference": reference, "registrationConfig": asdict(RegistrationConfig()),
        "smallGicpExtensionSha256": sha256(Path(small_gicp.__file__)),
    }


def child(args, sink):
    from rosbags.typesys import Stores, get_typestore
    from run_glim_stream_trial import actual_libraries

    frozen = integrity(args)
    expected = replay.child_environment(args.work)
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise ValueError("unchanged_explicit_localization_child_environment_required")
    map_points, _ = seeded.load_saved_points(args.saved_map, RegistrationConfig())
    pose, reference = seed_reference(args, frozen)
    summary = TrackingSummary(
        map_points, pose, reference, session_id(os.environ.get("LINGXI_SLAM_SESSION")))
    types = get_typestore(Stores.ROS2_HUMBLE)
    for name in ("Imu", "PointCloud2"):
        types.get_msgdef("sensor_msgs/msg/" + name)
    loaded_extension = {}

    def verify_loaded():
        import small_gicp
        observed = actual_libraries(args.work, args.adapter, frozen["nativeLibrariesSha256"])
        baseline = replay.load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
        if observed != baseline["actualLoadedLibrariesSha256"]:
            raise ValueError("unchanged_recorded_core_libraries_required")
        loaded_extension.update(points.loaded_extension(args, frozen["pointExtensionLibrarySha256"]))
        library = Path(small_gicp.__file__).resolve(strict=True)
        mapped = {
            Path(line.split(None, 5)[5]) for line in Path("/proc/self/maps").read_text().splitlines()
            if len(line.split(None, 5)) == 6 and Path(line.split(None, 5)[5]).name == library.name
        }
        if mapped != {library} or sha256(library) != frozen["smallGicpExtensionSha256"]:
            raise ValueError("matching_actually_loaded_small_gicp_required")
        return observed

    code = replay.recorded_child(
        args, sink, integrity=frozen, native_factory=points.native_factory(args, summary),
        verify_loaded=verify_loaded, verify_inputs=lambda: integrity(args),
        decode=lambda kind, data: types.deserialize_cdr(
            data, "sensor_msgs/msg/" + ("Imu" if kind == "imu" else "PointCloud2")),
        versions={name: version(name) for name in ("numpy", "scipy", "small-gicp", "rosbags")})
    if code:
        return code
    child_report = replay.load(args.output / "child-report.json")
    count = child_report["worker"]["stream"]["counts"]["estimatedPoses"]
    replay.private_json(args.output / "point-report.json", {
        **summary.report(count), "actualLoadedExtensionSha256": loaded_extension})
    replay.private_json(args.output / "tracking-report.json", summary.tracking_report(count))
    return 0


def stage(args):
    staged, runtime, _ = replay.stage_private_inputs(args)
    for name in set((*points.SOURCE_NAMES, *SOURCE_NAMES)):
        target = runtime / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(ROOT / name, target)
        if sha256(target) != sha256(ROOT / name):
            raise ValueError("identical_localization_runtime_source_required")
    target_map = args.output / "private-inputs/saved-map"
    target_reference = args.output / "private-inputs/point-reference"
    shutil.copytree(args.saved_map, target_map, symlinks=False)
    target_reference.mkdir(mode=0o700)
    # Only these immutable reports and the two bounded samples are needed.
    for name in ("report.json", "child-report.json", "point-report.json", "private-child.log",
                 "private-first-points.npz", "private-latest-points.npz"):
        shutil.copyfile(args.point_reference / name, target_reference / name)
    staged.saved_map, staged.point_reference = target_map, target_reference
    return staged, runtime


def run(args):
    frozen, output = integrity(args), points.private_output(args.output)
    if any(output == path or path in output.parents or output in path.parents for path in (
            args.work, args.adapter, args.points, args.bundle, args.reference,
            args.saved_map, args.point_reference)):
        raise ValueError("separate_private_localization_output_required")
    output.mkdir(mode=0o700, parents=True)
    staged, runtime = stage(args)
    if integrity(staged) != frozen:
        raise ValueError("identical_staged_localization_inputs_required")
    command = [sys.executable, "-B", str(runtime / "tools/run_recorded_localization_trial.py"),
               "child", "--acknowledge-recording-reuse"]
    for name in ("work", "adapter", "points", "bundle", "reference", "saved-map",
                 "point-reference", "output", "hypothesis", "rate"):
        command.extend(("--" + name, str(getattr(staged, name.replace("-", "_")))))
    owner = replay.ProcessSupervisor(
        command, cwd=output, environment=replay.child_environment(args.work),
        stderr_path=output / "private-child.log", limits=replay.LIMITS)
    report = {
        "schemaVersion": 1, "result": "running", "inputIntegrity": frozen,
        "limits": asdict(replay.LIMITS), "boundaries": BOUNDARIES.copy(),
        "replayRate": args.rate, "healthChannelContainsPointsOrLocalization": False,
    }
    try:
        report["process"] = replay.coordinate(owner, frozen["journal"]["records"], 110.)
        result = replay.load(output / "child-report.json")
        replay.complete_child(result, frozen["journal"])
        if (result["result"] != replay.CHILD_RESULT or result["inputIntegrity"] != frozen
                or result["session"] != report["process"]["session"] or integrity(args) != frozen):
            raise ValueError("same_owned_child_and_unchanged_localization_inputs_required")
        points.verified_points(replay.load(output / "point-report.json"),
                               result["worker"]["stream"]["counts"]["estimatedPoses"])
        verified_tracking(replay.load(output / "tracking-report.json"),
                          result["worker"]["stream"]["counts"]["estimatedPoses"] - 1)
        report["filesSha256"] = {
            name: sha256(output / name) for name in (
                "child-report.json", "point-report.json", "tracking-report.json")}
        report["result"] = RESULT
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        report["process"] = owner.stop()
        report["privateLogSha256"] = (
            sha256(output / "private-child.log") if (output / "private-child.log").exists() else None)
        replay.private_json(output / "report.json", report)
    return report


def verified_tracking(report, count):
    if (set(report) != {"packets", "seedAttemptsPerControl", "automaticReseeding",
                        "inputMode", "poseFrame", "controls"}
            or type(report["packets"]) is not int or report["packets"] != count
            or not 1 <= count <= 20_000 or type(report["seedAttemptsPerControl"]) is not int
            or report["seedAttemptsPerControl"] != 1 or report["automaticReseeding"] is not False
            or report["inputMode"] != "recorded" or report["poseFrame"] != "lidar_not_robot_base"
            or [value["seedKind"] for value in report["controls"]] != list(SEED_KINDS)):
        raise ValueError("exact_complete_recorded_tracking_controls_required")
    for control in report["controls"]:
        if set(control) != {
                "seedKind", "observedPackets", "stateCounts", "firstTrackingFrame", "firstLostFrame",
                "firstLossReason", "lastObservation", "atNativeClose", "afterExplicitStop", "registration"}:
            raise ValueError("exact_recorded_tracking_control_fields_required")
        states = control["stateCounts"]
        if (not set(states) <= {"acquiring", "tracking", "lost"}
                or any(type(value) is not int or value < 1 for value in states.values())
                or sum(states.values()) != count or type(control["observedPackets"]) is not int
                or control["observedPackets"] != count):
            raise ValueError("all_recorded_tracking_observations_accounted_for_required")
        first, lost = control["firstTrackingFrame"], control["firstLostFrame"]
        if (first is not None and (type(first) is not int or first != 3)
                or lost is not None and (type(lost) is not int or not 1 <= lost <= count)
                or (first is None) != (states.get("tracking", 0) == 0)
                or (lost is None) != (states.get("lost", 0) == 0)
                or lost is not None and states["lost"] != count - lost + 1
                or first is not None and states["tracking"] != (lost or count + 1) - first
                or (control["firstLossReason"] is None) != (lost is None)
                or lost is not None and control["firstLossReason"] not in {
                    "corrected_point_stream_expired", "stale_corrected_point_input",
                    "corrected_point_time_gap_or_reversal", "registration_completed_after_freshness_deadline",
                    "scan_map_registration_rejected", "registration_exception"}):
            raise ValueError("one_seed_without_hidden_recovery_required")
        for name in ("lastObservation", "atNativeClose", "afterExplicitStop"):
            snapshot = control[name]
            if (set(snapshot) != {"state", "generation", "consecutiveMatches", "lastMatchedFrame",
                                  "reason", "hasDiagnosticPose", "localizationReady", "navigationReady"}
                    or snapshot["state"] not in {"acquiring", "tracking", "lost", "stopped"}
                    or snapshot["localizationReady"] is not False or snapshot["navigationReady"] is not False
                    or type(snapshot["generation"]) is not int
                    or snapshot["generation"] != (2 if name == "afterExplicitStop" else 1)
                    or type(snapshot["consecutiveMatches"]) is not int
                    or not 0 <= snapshot["consecutiveMatches"] <= 3
                    or type(snapshot["lastMatchedFrame"]) is not int
                    or not 0 <= snapshot["lastMatchedFrame"] <= count
                    or snapshot["hasDiagnosticPose"] is not (snapshot["state"] == "tracking")):
                raise ValueError("bounded_tracking_snapshot_without_readiness_claim_required")
        if (control["afterExplicitStop"]["state"] != "stopped"
                or control["afterExplicitStop"]["reason"] != "explicit_stop"
                or control["afterExplicitStop"]["consecutiveMatches"] != 0):
            raise ValueError("explicit_localization_cleanup_required")
        registration = control["registration"]
        if (set(registration) != {"calls", "accepted", "rejected", "rejectionReasons", "metrics"}
                or any(type(registration[key]) is not int or not 0 <= registration[key] <= count
                       for key in ("calls", "accepted", "rejected"))
                or registration["calls"] != registration["accepted"] + registration["rejected"]
                or not 1 <= registration["calls"] <= (lost or count)
                or set(registration["metrics"]) != set(METRICS)
                or not set(registration["rejectionReasons"]) <= {
                    "invalid_native_transform", "native_not_converged_or_insufficient_inliers",
                    "insufficient_scan_map_overlap", "excessive_scan_map_residual",
                    "excessive_seed_correction", "degenerate_registration_geometry"}
                or any(type(value) is not int or not 1 <= value <= registration["rejected"]
                       for value in registration["rejectionReasons"].values())):
            raise ValueError("bounded_registration_aggregate_required")
        for metric in registration["metrics"].values():
            if (set(metric) != {"count", "minimum", "p50", "p95", "maximum"}
                    or type(metric["count"]) is not int
                    or not 0 <= metric["count"] <= registration["calls"]):
                raise ValueError("bounded_registration_statistics_required")
            values = [metric[key] for key in ("minimum", "p50", "p95", "maximum")]
            if (metric["count"] == 0 and values != [None] * 4
                    or metric["count"] > 0 and (
                        any(type(value) not in (float, int) or not math.isfinite(value) or value < 0
                            for value in values) or values != sorted(values))):
                raise ValueError("finite_ordered_registration_statistics_required")
    return report


def evidence(trial):
    parent, child_report = (replay.load(trial / name) for name in ("report.json", "child-report.json"))
    frozen, process = parent["inputIntegrity"], parent["process"]
    if (parent["result"] != RESULT or child_report["result"] != replay.CHILD_RESULT
            or frozen["sourceSha256"] != replay.hashes()
            or frozen["pointSourceSha256"] != points.source_hashes()
            or frozen["localizationSourceSha256"] != hashes()
            or child_report["inputIntegrity"] != frozen
            or child_report["session"] != process["session"]
            or parent["boundaries"] != BOUNDARIES
            or parent["healthChannelContainsPointsOrLocalization"] is not False
            or parent["privateLogSha256"] != sha256(trial / "private-child.log")
            or set(parent["filesSha256"]) != {
                "child-report.json", "point-report.json", "tracking-report.json"}
            or any(sha256(trial / name) != digest for name, digest in parent["filesSha256"].items())):
        raise ValueError("unchanged_owned_recorded_localization_reports_required")
    replay.replay_rate(parent["replayRate"])
    replay.complete_child(child_report, frozen["journal"])
    if (process["state"] != "stopped" or process["exitCode"] != 0 or process["forcedTermination"]
            or process["processAlive"] or process["monitorAlive"] or not process["cleanupComplete"]
            or any(process[key] for key in ("mappingReady", "localizationReady", "navigationReady"))
            or process["completedMessages"] != frozen["journal"]["records"]
            or process["processedScans"] != child_report["nativeCalls"]["frame"]
            or process["estimatedPoses"] != child_report["worker"]["stream"]["counts"]["estimatedPoses"]
            or child_report["replayRate"] != parent["replayRate"]):
        raise ValueError("complete_closed_localization_process_required")
    point_report = replay.load(trial / "point-report.json")
    points.verified_points(point_report, process["estimatedPoses"])
    if point_report["actualLoadedExtensionSha256"] != {
            points.LIBRARY: frozen["pointExtensionLibrarySha256"]}:
        raise ValueError("matching_recorded_point_extension_required")
    tracking = verified_tracking(replay.load(trial / "tracking-report.json"), point_report["packets"])
    # All selected fields are bounded scalars/aggregate schemas, never arbitrary private reports.
    return {
        "schemaVersion": 1, "date": "2026-10-30", "result": RESULT,
        "privateReportsSha256": {"report.json": sha256(trial / "report.json"), **parent["filesSha256"]},
        "sourceSha256": hashes(), "frozenSourceSha256": replay.hashes(),
        "pointSourceSha256": points.source_hashes(), "sourceCommits": frozen["sourceCommits"],
        "host": {"machine": child_report["machine"], "versions": child_report["versions"]},
        "savedMap": {key: frozen["savedMap"][key] for key in ("receiptSha256", "pointsSha256")},
        "seedReference": {key: frozen["seedReference"][key] for key in (
            "sampleSha256", "frameId", "copiedPoints")},
        "configurationSha256": frozen["configurationSha256"],
        "registrationConfigRawUnitsNotPhysicalAcceptance": frozen["registrationConfig"],
        "smallGicpExtensionSha256": frozen["smallGicpExtensionSha256"],
        "actualLoadedLibrariesSha256": child_report["actualLoadedLibrariesSha256"],
        "inputJournal": {**frozen["journal"], "sha256": frozen["journalSha256"]},
        "process": {key: value for key, value in process.items() if key != "session"},
        "points": {key: point_report[key] for key in (
            "packets", "copiedPoints", "copiedPointsPerPacketMinMax", "nativeCounts",
            "allPacketBytesSha256", "maximumRetainedPackets", "initializationFrameExcluded")},
        "tracking": tracking, "limits": parent["limits"], "nativeCalls": child_report["nativeCalls"],
        "replayTiming": replay.verified_timing(child_report, frozen["journal"]),
        "observedWorkerTiming": replay.observed_worker_timing(child_report),
        "boundaries": BOUNDARIES.copy(), "replayRate": parent["replayRate"],
        "pacedRecordedInputNotLivePerformance": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "child"):
        command = commands.add_parser(name)
        for field in ("work", "adapter", "points", "bundle", "reference", "saved-map",
                      "point-reference", "output"):
            command.add_argument("--" + field, required=True, type=Path)
        command.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
        command.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"),
                             required=True)
        command.add_argument("--acknowledge-recording-reuse", action="store_true", required=True)
    export = commands.add_parser("export")
    for name in ("trial", "output"):
        export.add_argument("--" + name, required=True, type=Path)
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
        if args.command == "export":
            report = evidence(args.trial)
            replay.private_json(args.output, report)
        else:
            report = run(args)
        print(json.dumps({"result": report["result"], "localizationReady": False,
                          "navigationReady": False}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
