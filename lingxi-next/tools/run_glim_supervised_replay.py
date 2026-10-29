"""Compose real GLIM with health-only process supervision using preserved CDR.

Offline diagnostic only: no ROS runtime, robot connection, map activation or motion.
Each child initializes the unchanged native adapter exactly once.
"""
import argparse
from dataclasses import asdict
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import selectors
import shutil
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.process_protocol import HealthEmitter, session_id
from lingxi_slam.process_supervisor import ProcessLimits, ProcessSupervisor


RESULT = "native_recorded_worker_supervision_closed_not_mapping_or_navigation_acceptance"
CHILD_RESULT = "complete_native_recorded_worker_closed"
BOUNDARIES = (
    "robotContacted", "rosNodeStarted", "liveSensorsSubscribed", "motionCommandsSent",
    "servicesChanged", "mapSaved", "mapActivated", "timingHypothesisValidated",
    "robotCalibrationValidated", "geometryValidated", "relocalizationValidated",
    "targetArm64SupervisionValidated", "systemdRecoveryValidated", "voiceConcurrencyValidated",
    "navigationValidated", "deploymentApproved", "rawDataPublished",
)
LIMITS = ProcessLimits(startup_seconds=30., progress_seconds=2., shutdown_seconds=6.)
SOURCE_NAMES = (
    "tools/run_glim_supervised_replay.py", "tools/slam_callback_journal.py",
    "tools/glim_trial_config.py", "tools/run_glim_stream_trial.py",
    "tools/run_glim_offline_trial.py", "tools/run_kiss_offline_trial.py",
    "tools/build_slam_cpu_trial.py", "tools/diagnose_slam_sensors.py",
    "tools/export_glim_controlled_mapping.py", "tools/compare_glim_architectures.py",
    "tools/glim_target_controlled_mapping.py", "tools/verify_slam_capture.py", "tools/slam_bag_audit.py",
    "tools/glim-replay/offline.cpp", "tools/glim-replay/CMakeLists.txt",
    "robot-slam/lingxi_slam/__init__.py", "robot-slam/lingxi_slam/messages.py",
    "robot-slam/lingxi_slam/worker.py", "robot-slam/lingxi_slam/stream.py",
    "robot-slam/lingxi_slam/process_protocol.py", "robot-slam/lingxi_slam/process_supervisor.py",
    "docs/slam-stream-input-evidence.json",
)
TIMING_CHANNELS = (
    "nativeCreateWall", "nativeLibraryVerificationWall", "nativeImuWall", "nativeFrameWall",
    "nativeCloseWall", "nativePreprocess", "nativeOdometry", "producerReadWall",
    "producerDecodeWall", "producerOfferWall", "producerDispatchLateness", "producerAdmissionLateness",
)
TIMING_STATISTICS = ("count", "totalSeconds", "minimumSeconds", "p50Seconds", "p95Seconds", "maximumSeconds")


class ReplayTiming:
    """Bounded scalar observations only; no sensor values, poses or native output."""

    def __init__(self, records, *, clock=time.monotonic):
        if type(records) is not int or not 1 <= records <= 20_000 or not callable(clock):
            raise ValueError("bounded_record_count_and_timing_clock_required")
        self.maximum, self.clock = records + 1, clock
        self.samples = {name: [] for name in TIMING_CHANNELS}
        self.lock = threading.Lock()

    def observe(self, name, seconds):
        if (type(name) is not str or name not in self.samples or type(seconds) not in (int, float)
                or not math.isfinite(seconds) or seconds < 0):
            raise ValueError("known_finite_nonnegative_timing_required")
        with self.lock:
            if len(self.samples[name]) >= self.maximum:
                raise ValueError("bounded_replay_timing_samples_required")
            self.samples[name].append(seconds)

    def call(self, name, function, *args):
        started = self.clock()
        try:
            return function(*args)
        finally:
            self.observe(name, self.clock() - started)

    def snapshot(self):
        channels = {}
        with self.lock:
            for name, values in self.samples.items():
                ordered = sorted(values)
                count = len(ordered)
                channels[name] = dict(zip(TIMING_STATISTICS, (
                    count, math.fsum(ordered), ordered[0] if count else None,
                    ordered[math.ceil(count * .5) - 1] if count else None,
                    ordered[math.ceil(count * .95) - 1] if count else None,
                    ordered[-1] if count else None,
                )))
        return {
            "schemaVersion": 1, "clock": "time.monotonic", "percentiles": "nearest_rank",
            "maximumSamplesPerChannel": self.maximum, "channels": channels,
        }


def verified_timing(report, expected):
    timing, calls = report["timing"], report["nativeCalls"]
    if (set(timing) != {"schemaVersion", "clock", "percentiles", "maximumSamplesPerChannel", "channels"}
            or type(timing["schemaVersion"]) is not int or timing["schemaVersion"] != 1
            or timing["clock"] != "time.monotonic" or timing["percentiles"] != "nearest_rank"
            or type(timing["maximumSamplesPerChannel"]) is not int
            or timing["maximumSamplesPerChannel"] != expected["records"] + 1
            or set(timing["channels"]) != set(TIMING_CHANNELS)):
        raise ValueError("exact_bounded_replay_timing_schema_required")
    counts = {
        "nativeCreateWall": 1, "nativeLibraryVerificationWall": 1, "nativeCloseWall": 1,
        "nativeImuWall": calls["imu"], "nativeFrameWall": calls["frame"],
        "nativePreprocess": calls["frame"], "nativeOdometry": calls["frame"],
        "producerReadWall": expected["records"] + 1,
        **dict.fromkeys(("producerDecodeWall", "producerOfferWall", "producerDispatchLateness",
                        "producerAdmissionLateness"), expected["records"]),
    }
    channels = {}
    for name in TIMING_CHANNELS:
        channel = timing["channels"][name]
        if (set(channel) != set(TIMING_STATISTICS) or type(channel["count"]) is not int
                or not 0 < channel["count"] == counts[name] <= timing["maximumSamplesPerChannel"]
                or any(type(channel[key]) not in (int, float) or not math.isfinite(channel[key])
                       or channel[key] < 0 for key in TIMING_STATISTICS[1:])
                or not channel["minimumSeconds"] <= channel["p50Seconds"] <= channel["p95Seconds"]
                <= channel["maximumSeconds"] <= channel["totalSeconds"]):
            raise ValueError("complete_finite_replay_timing_accounting_required")
        channels[name] = {key: channel[key] for key in TIMING_STATISTICS}
    return {**{key: timing[key] for key in (
        "schemaVersion", "clock", "percentiles", "maximumSamplesPerChannel")}, "channels": channels}


def observed_worker_timing(report):
    values = {
        "maximumQueueAgeSeconds": report["worker"]["maximumObservedQueueAgeSeconds"],
        "maximumScanWaitSeconds": report["worker"]["stream"]["maximumObservedScanWaitSeconds"],
        "childWallSeconds": report["totalWallSeconds"],
        "childProcessCpuSeconds": report["processCpuSeconds"],
    }
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
           for value in values.values()):
        raise ValueError("finite_nonnegative_worker_timing_required")
    return values


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def hashes():
    from verify_slam_capture import sha256
    return {name: sha256(ROOT / name) for name in SOURCE_NAMES}


def child_environment(work):
    work = Path(work).resolve(strict=True)
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
        "LD_LIBRARY_PATH": os.pathsep.join((
            str(work / "prefix/lib"), str(work / "sysroot/usr/lib/x86_64-linux-gnu"))),
        "OMP_NUM_THREADS": "2", "OMP_DYNAMIC": "FALSE",
        "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }


def replay_rate(value):
    if type(value) is not float or value not in (.5, 1.):
        raise ValueError("explicit_half_or_one_times_diagnostic_replay_rate_required")
    return value


def inputs(args):
    from build_slam_cpu_trial import PINS
    from glim_target_controlled_mapping import configuration_files, verify_control
    from run_glim_offline_trial import explicit_diagnostic_profile
    from slam_callback_journal import inspect
    from verify_slam_capture import sha256

    replay_rate(args.rate)
    if (sys.platform != "linux" or platform.machine() != "x86_64"
            or os.geteuid() == 0 or sys.prefix == sys.base_prefix
            or metadata.version("rosbags") != "0.11.9" or metadata.version("numpy") != "2.5.4"):
        raise ValueError("isolated_ordinary_user_pinned_x86_replay_environment_required")
    core, build = (load(path / "build-report.json") for path in (args.work, args.adapter))
    library = args.adapter / "build/liblingxi_glim_offline.so"
    if (core["result"] != "core_libraries_built_not_target_acceptance"
            or core["sourceCommits"] != PINS or core["hostMachine"] != "x86_64"
            or build["result"] != "adapter_built_not_algorithm_acceptance" or build["sourceCommits"] != PINS
            or build["coreBuildReportSha256"] != sha256(args.work / "build-report.json")
            or build["librarySha256"] != sha256(library)
            or build["adapterSourceSha256"] != sha256(ROOT / "tools/glim-replay/offline.cpp")
            or build["adapterCmakeSha256"] != sha256(ROOT / "tools/glim-replay/CMakeLists.txt")):
        raise ValueError("frozen_native_core_and_adapter_build_required")
    verify_control(args.bundle)
    reference = load(args.reference / "report.json")
    journal = args.reference / "private-callbacks.cdrjournal"
    if (reference["result"] != "same_input_session_closed_not_calibration_or_navigation_acceptance"
            or reference["mode"] != "live" or reference["timingHypothesis"] != args.hypothesis
            or reference["replayManifestSha256"] != sha256(args.bundle / "replay-manifest.json")
            or reference["effectiveConfiguration"] != configuration_files(args.bundle / "config")
            or reference["filesSha256"]["private-callbacks.cdrjournal"] != sha256(journal)
            or inspect(journal) != reference["journal"] or not reference["journal"]["records"]):
        raise ValueError("complete_unchanged_callback_journal_and_configuration_required")
    profile = load(args.bundle / "private-profile.json")
    explicit_diagnostic_profile(profile, load(args.bundle / "private-sensor-diagnostic.json"), args.hypothesis)
    frozen = load(args.bundle / "replay-manifest.json")
    for name in ("private-profile.json", "private-sensor-diagnostic.json", "control-manifest.json"):
        if frozen["files"][name]["sha256"] != sha256(args.bundle / name):
            raise ValueError("unchanged_recorded_sensor_profile_required")
    libraries = {path.name: sha256(path) for path in (args.work / "prefix/lib").glob("*.so*")
                 if path.is_file()}
    libraries["liblingxi_glim_offline.so"] = sha256(library)
    baseline = load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
    if any(libraries.get(name) != digest for name, digest in baseline["actualLoadedLibrariesSha256"].items()):
        raise ValueError("same_previously_observed_native_libraries_required")
    return {
        "sourceSha256": hashes(), "coreBuildReportSha256": sha256(args.work / "build-report.json"),
        "adapterBuildReportSha256": sha256(args.adapter / "build-report.json"), "sourceCommits": PINS,
        "referenceReportSha256": sha256(args.reference / "report.json"),
        "replayManifestSha256": sha256(args.bundle / "replay-manifest.json"),
        "profileSha256": sha256(args.bundle / "private-profile.json"),
        "diagnosticSha256": sha256(args.bundle / "private-sensor-diagnostic.json"),
        "configurationSha256": reference["effectiveConfiguration"], "nativeLibrariesSha256": libraries,
        "journalSha256": sha256(journal), "journal": reference["journal"],
    }


class OwnedNative:
    """Measurement wrapper only; no changes to preprocessing or native lifetime."""

    def __init__(self, factory, record, loaded, *, timing=None):
        self.record, self.closed, self.timing = record, False, timing
        record("create")
        self.native = self.measured("nativeCreateWall", factory)
        try:
            self.measured("nativeLibraryVerificationWall", loaded)
        except BaseException:
            self.close()
            raise

    def imu(self, *args):
        self.record("imu")
        return self.measured("nativeImuWall", self.native.imu, *args)

    def frame(self, *args):
        self.record("frame")
        result = self.measured("nativeFrameWall", self.native.frame, *args)
        if self.timing is not None:
            self.timing.observe("nativePreprocess", float(result[1][0]) / 1000.)
            self.timing.observe("nativeOdometry", float(result[1][1]) / 1000.)
        return result

    def measured(self, name, function, *args):
        return function(*args) if self.timing is None else self.timing.call(name, function, *args)

    def close(self):
        if not self.closed:
            self.closed = True
            self.record("close")
            return self.measured("nativeCloseWall", self.native.close)


class StopPipe:
    def __init__(self):
        self.buffer = bytearray()
        self.received = False

    def feed(self, payload):
        if not payload:
            raise RuntimeError("supervisor_stop_pipe_closed")
        self.buffer.extend(payload)
        if len(self.buffer) > 5 or not b"stop\n".startswith(self.buffer):
            raise ValueError("exact_private_stop_request_required")
        self.received = self.buffer == b"stop\n"
        return self.received


def complete_child(report, expected):
    worker, calls = report["worker"], report["nativeCalls"]
    counts, stream = worker["counts"], worker["stream"]
    if (not report["stopReceived"] or not report["journalExhausted"]
            or worker["state"] != "stopped" or worker["failure"] or worker["threadAlive"]
            or counts["completedMessages"] != expected["records"]
            or counts["acceptedImu"] != expected["counts"]["imu"]
            or counts["acceptedClouds"] != expected["counts"]["cloud"]
            or counts["rejectedMessages"] or counts["discardedMessages"]
            or any(worker[key] for key in ("queuedMessages", "queuedPayloadBytes", "inFlightMessages"))
            or stream is None or stream["state"] != "finished" or stream["failure"]
            or calls["create"] != 1 or calls["close"] != 1
            or calls["imu"] != stream["counts"]["insertedImu"]
            or calls["frame"] != stream["counts"]["processedScans"]
            or stream["counts"]["estimatedPoses"] < 2
            or not report["nativeOwnerIsSingleNonMainThread"]):
        raise ValueError("complete_native_worker_accounting_and_shutdown_required")
    if (stream["counts"]["receivedImu"] != expected["counts"]["imu"]
            or stream["counts"]["receivedClouds"] != expected["counts"]["cloud"]
            or sum(stream["counts"][key] for key in (
                "processedScans", "skippedBeforeImu", "skippedAfterImuAtEndOfInput"))
            != expected["counts"]["cloud"]):
        raise ValueError("all_recorded_sensor_packets_accounted_for_required")
    verified_timing(report, expected)
    observed_worker_timing(report)


def child(args, sink):
    import faulthandler
    print("supervised_replay_phase: importing_recorded_dependencies", file=sys.stderr, flush=True)
    faulthandler.dump_traceback_later(10., repeat=True, file=sys.stderr)
    import numpy as np
    from rosbags.typesys import Stores, get_typestore
    from lingxi_slam.messages import SensorProfile
    from lingxi_slam.worker import SensorWorker
    from run_glim_offline_trial import Native
    from run_glim_stream_trial import actual_libraries
    from slam_callback_journal import records

    emitter = HealthEmitter(sink, session_id(os.environ.get("LINGXI_SLAM_SESSION")))
    print("supervised_replay_phase: verifying_frozen_inputs", file=sys.stderr, flush=True)
    integrity = inputs(args)
    timing = ReplayTiming(integrity["journal"]["records"])
    faulthandler.cancel_dump_traceback_later()
    print("supervised_replay_phase: starting_native_worker", file=sys.stderr, flush=True)
    expected_environment = child_environment(args.work)
    if any(os.environ.get(key) != value for key, value in expected_environment.items()):
        raise ValueError("explicit_verified_child_library_and_thread_environment_required")
    types = get_typestore(Stores.ROS2_HUMBLE)
    # Compile both CDR decoders before the worker's real-time input watchdog starts.
    for kind in ("sensor_msgs/msg/Imu", "sensor_msgs/msg/PointCloud2"):
        types.get_msgdef(kind)
    calls = dict.fromkeys(("create", "imu", "frame", "close"), 0)
    owners, main_thread, loaded = set(), threading.get_ident(), {}
    report = {"result": "running", "session": emitter.session, "pid": os.getpid(),
              "machine": platform.machine(), "versions": {"numpy": np.__version__, "rosbags": metadata.version("rosbags")},
              "inputIntegrity": integrity, "replayRate": args.rate,
              "journalExhausted": False, "stopReceived": False}

    def record(name):
        owner = threading.get_ident()
        if owner == main_thread or owners and owner not in owners:
            raise RuntimeError("single_non_main_native_owner_required")
        owners.add(owner)
        calls[name] += 1

    def verify_loaded():
        loaded.update(actual_libraries(args.work, args.adapter, integrity["nativeLibrariesSha256"]))
        baseline = load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
        if loaded != baseline["actualLoadedLibrariesSha256"]:
            raise ValueError("complete_previously_observed_loaded_library_set_required")

    def factory():
        return OwnedNative(
            lambda: Native(args.adapter / "build/liblingxi_glim_offline.so", args.bundle / "config",
                           args.work / "prefix/lib/libodometry_estimation_cpu.so"), record,
            verify_loaded, timing=timing,
        )

    profile = SensorProfile("lidar_chest_front", "lidar_imu_chest_front",
                            "frame_relative_nanoseconds_hypothesis")
    worker = SensorWorker(factory, profile, args.hypothesis)
    stop, failure = StopPipe(), None
    started, next_health, epoch = time.monotonic(), 0., None
    cpu_started = time.process_time()
    iterator = records(args.reference / "private-callbacks.cdrjournal")
    pending = timing.call("producerReadWall", next, iterator, None)
    first_elapsed = pending.elapsed_ns
    worker.start()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(0, selectors.EVENT_READ)
            while True:
                now, state = time.monotonic(), worker.snapshot()
                if state["state"] == "failed":
                    raise RuntimeError("recorded_worker_failed")
                if now >= next_health:
                    emitter.emit(state)
                    next_health = now + .05
                if selector.select(0):
                    if stop.feed(os.read(0, 6)):
                        report["stopReceived"] = True
                        break
                if state["state"] == "running":
                    if epoch is None:
                        epoch = now
                    due = None if pending is None else epoch + (pending.elapsed_ns - first_elapsed) / 1e9 / args.rate
                    if due is not None and now >= due:
                        timing.observe("producerDispatchLateness", time.monotonic() - due)
                        msgtype = "sensor_msgs/msg/" + ("Imu" if pending.kind == "imu" else "PointCloud2")
                        message = timing.call("producerDecodeWall", types.deserialize_cdr, pending.payload, msgtype)
                        offer = worker.offer_imu if pending.kind == "imu" else worker.offer_cloud
                        accepted = timing.call("producerOfferWall", offer, message)
                        timing.observe("producerAdmissionLateness", time.monotonic() - due)
                        if not accepted:
                            raise RuntimeError("recorded_callback_rejected")
                        pending = timing.call("producerReadWall", next, iterator, None)
                        report["journalExhausted"] = pending is None
                        continue
                selector.select(.002)
    except BaseException as error:
        failure = type(error).__name__
    finally:
        iterator.close()
        report["worker"] = worker.stop()
        report.update(nativeCalls=calls, actualLoadedLibrariesSha256=loaded,
                      nativeOwnerIsSingleNonMainThread=len(owners) == 1 and main_thread not in owners,
                      totalWallSeconds=time.monotonic() - started,
                      processCpuSeconds=time.process_time() - cpu_started, timing=timing.snapshot())
        try:
            if failure is not None:
                raise RuntimeError("recorded_replay_interrupted")
            complete_child(report, integrity["journal"])
            if inputs(args) != integrity:
                raise ValueError("frozen_replay_inputs_changed")
            report["result"] = CHILD_RESULT
        except BaseException as error:
            report.update(result="failed_or_interrupted", errorType=failure or type(error).__name__)
        private_json(args.output / "child-report.json", report)
        terminal = report["worker"]
        if report["result"] != CHILD_RESULT:
            terminal = {**terminal, "state": "failed", "failure": "native_replay_composition_failed"}
        emitter.emit(terminal)
    return 0 if report["result"] == CHILD_RESULT else 1


def coordinate(owner, expected_messages, deadline_seconds, *, clock=time.monotonic, sleep=time.sleep):
    """EOF means all callbacks completed, not just submitted or producer EOF."""
    deadline, reached = clock() + deadline_seconds, False
    owner.start()
    try:
        while clock() < deadline:
            state = owner.snapshot()
            if state["state"] == "failed":
                break
            if state["completedMessages"] == expected_messages:
                reached = True
                break
            if state["completedMessages"] > expected_messages:
                raise ValueError("recorded_completion_counter_exceeded_input")
            sleep(.01)
    finally:
        terminal = owner.stop()
    if (not reached or terminal["state"] != "stopped" or terminal["exitCode"] != 0
            or terminal["processAlive"] or terminal["monitorAlive"] or terminal["forcedTermination"]
            or not terminal["cleanupComplete"] or terminal["completedMessages"] != expected_messages):
        raise RuntimeError("complete_native_process_stop_not_confirmed")
    return terminal


def stage_private_inputs(args):
    """Prepare byte-identical local-disk inputs before any process deadlines start."""
    from verify_slam_capture import sha256

    runtime = args.output / "runtime-source"
    names = set(SOURCE_NAMES)
    names.update(str(path.relative_to(ROOT)).replace(os.sep, "/") for path in (ROOT / "tools").glob("*.py"))
    copied = {}

    def copy(source, target):
        if source.is_symlink() or not source.is_file():
            raise ValueError("direct_frozen_replay_file_required")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if sha256(source) != sha256(target):
            raise ValueError("byte_identical_private_staging_required")

    for name in sorted(names):
        copy(ROOT / name, runtime / name)
        copied[name] = sha256(runtime / name)
    bundle, reference = args.output / "private-inputs/bundle", args.output / "private-inputs/reference"
    for directory in ("baseline-config", "config"):
        for source in (args.bundle / directory).iterdir():
            copy(source, bundle / directory / source.name)
    for name in ("replay-manifest.json", "control-manifest.json",
                 "private-profile.json", "private-sensor-diagnostic.json"):
        copy(args.bundle / name, bundle / name)
    for name in ("report.json", "private-callbacks.cdrjournal"):
        copy(args.reference / name, reference / name)
    return argparse.Namespace(**{**vars(args), "bundle": bundle, "reference": reference}), runtime, copied


def run(args):
    from verify_slam_capture import sha256

    integrity = inputs(args)
    allowed = ((ROOT / ".local").resolve(), (Path.home() / ".cache/lingxi-next").resolve())
    if (args.output.exists() or not any(args.output.is_relative_to(root) for root in allowed)
            or any(args.output == path or path in args.output.parents
                   for path in (args.reference, args.bundle, args.work, args.adapter))):
        raise ValueError("new_ignored_private_supervised_output_required")
    args.output.mkdir(mode=0o700, parents=True)
    prepared_at = time.monotonic()
    staged, runtime, copied = stage_private_inputs(args)
    if inputs(staged) != integrity:
        raise ValueError("identical_staged_replay_inputs_required")
    command = [sys.executable, "-B", str(runtime / "tools/run_glim_supervised_replay.py"), "child"]
    for name in ("work", "adapter", "bundle", "reference", "output", "hypothesis", "rate"):
        command.extend(("--" + name, str(getattr(staged, name))))
    owner = ProcessSupervisor(command, cwd=args.output, environment=child_environment(args.work),
                              stderr_path=args.output / "private-child.log", limits=LIMITS)
    report = {"schemaVersion": 1, "result": "running", "inputIntegrity": integrity,
              "limits": asdict(LIMITS), "boundaries": dict.fromkeys(BOUNDARIES, False),
              "privateStagingSecondsNotRealtimePerformance": time.monotonic() - prepared_at,
              "runtimeSourceFilesSha256": copied,
              "replayRate": args.rate, "pacedRecordedInputNotLivePerformance": True}
    try:
        report["process"] = coordinate(owner, integrity["journal"]["records"], 110.)
        result = load(args.output / "child-report.json")
        complete_child(result, integrity["journal"])
        if (result["result"] != CHILD_RESULT or result["session"] != report["process"]["session"]
                or result["replayRate"] != args.rate
                or result["inputIntegrity"] != integrity or inputs(args) != integrity):
            raise ValueError("same_owned_child_and_unchanged_inputs_required")
        report["childReportSha256"] = sha256(args.output / "child-report.json")
        report["result"] = RESULT
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        report["process"] = owner.stop()
        report["privateLogSha256"] = sha256(args.output / "private-child.log") if (
            args.output / "private-child.log").exists() else None
        private_json(args.output / "report.json", report)
    return report


def evidence(trial):
    from verify_slam_capture import sha256

    parent, result = load(trial / "report.json"), load(trial / "child-report.json")
    replay_rate(parent["replayRate"])
    if (parent["result"] != RESULT or result["result"] != CHILD_RESULT
            or parent["childReportSha256"] != sha256(trial / "child-report.json")
            or parent["privateLogSha256"] != sha256(trial / "private-child.log")
            or result["inputIntegrity"] != parent["inputIntegrity"]
            or parent["inputIntegrity"]["sourceSha256"] != hashes()
            or parent["boundaries"] != dict.fromkeys(BOUNDARIES, False)
            or type(result["replayRate"]) is not float or result["replayRate"] != parent["replayRate"]
            or result["session"] != parent["process"]["session"]):
        raise ValueError("verified_complete_supervised_native_evidence_required")
    complete_child(result, parent["inputIntegrity"]["journal"])
    process = parent["process"]
    if (process["state"] != "stopped" or process["exitCode"] != 0 or process["forcedTermination"]
            or process["processAlive"] or process["monitorAlive"] or not process["cleanupComplete"]
            or any(process[key] for key in ("mappingReady", "localizationReady", "navigationReady"))
            or process["completedMessages"] != result["worker"]["counts"]["completedMessages"]
            or process["processedScans"] != result["worker"]["stream"]["counts"]["processedScans"]
            or process["estimatedPoses"] != result["worker"]["stream"]["counts"]["estimatedPoses"]):
        raise ValueError("closed_supervised_native_process_evidence_required")
    # Allowlist aggregate fields; never serialize a private report wholesale.
    return {
        "schemaVersion": 1, "result": RESULT, "date": "2026-10-29",
        "privateParentReportSha256": sha256(trial / "report.json"),
        "privateChildReportSha256": parent["childReportSha256"],
        "sourceSha256": hashes(), "sourceCommits": parent["inputIntegrity"]["sourceCommits"],
        "host": {"machine": result["machine"], "versions": result["versions"]},
        "inputJournal": {**parent["inputIntegrity"]["journal"],
                         "sha256": parent["inputIntegrity"]["journalSha256"]},
        "configurationSha256": parent["inputIntegrity"]["configurationSha256"],
        "actualLoadedLibrariesSha256": result["actualLoadedLibrariesSha256"],
        "process": {key: value for key, value in process.items() if key != "session"},
        "nativeCalls": result["nativeCalls"],
        "replayTiming": verified_timing(result, parent["inputIntegrity"]["journal"]),
        "observedWorkerTiming": observed_worker_timing(result),
        "nativeOwnerIsSingleNonMainThread": result["nativeOwnerIsSingleNonMainThread"],
        "completedWorkerCounts": {key: result["worker"]["counts"][key] for key in (
            "acceptedImu", "acceptedClouds", "completedMessages", "discardedMessages", "rejectedMessages")},
        "streamCounts": {key: result["worker"]["stream"]["counts"][key] for key in (
            "receivedImu", "receivedClouds", "insertedImu", "processedScans", "estimatedPoses",
            "skippedBeforeImu", "skippedAfterImuAtEndOfInput")},
        "limits": parent["limits"],
        "boundaries": parent["boundaries"],
        "replayRate": parent["replayRate"], "pacedRecordedInputNotLivePerformance": True,
        "oneTimesReplayPerformanceValidated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "child"):
        command = commands.add_parser(name)
        for field in ("work", "adapter", "bundle", "reference", "output"):
            command.add_argument("--" + field, type=Path, required=True)
        command.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
        command.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
    export = commands.add_parser("export")
    export.add_argument("--trial", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    previous = os.umask(0o077)
    try:
        if args.command == "child":
            # C/C++ stdout and Python print go to the private stderr log, not IPC.
            with os.fdopen(os.dup(1), "wb", buffering=0) as sink:
                os.dup2(2, 1)
                return child(args, sink)
        if args.command == "export":
            result = evidence(args.trial)
            private_json(args.output, result)
        else:
            result = run(args)
        print(json.dumps({"result": result["result"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
