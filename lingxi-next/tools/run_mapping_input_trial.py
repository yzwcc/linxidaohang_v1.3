"""Recorded callbacks through a synthetic ROS boundary and real GLIM mapping/points.

This never imports installed ROS, joins DDS, contacts a robot or labels data live.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import ModuleType
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "robot-runtime/python")]
from mapping_service import configuration, private_json

SOURCES = (
    "robot-slam/lingxi_slam/sensor_admission.py", "robot-slam/lingxi_slam/mapping_input.py",
    "robot-slam/lingxi_slam/passive_ros.py", "robot-slam/lingxi_slam/mapping_worker.py",
    "robot-slam/lingxi_slam/mapping_session.py", "robot-slam/lingxi_slam/point_output.py",
    "robot-slam/lingxi_slam/point_data.py", "robot-slam/lingxi_slam/worker.py",
    "robot-slam/lingxi_slam/stream.py", "robot-slam/lingxi_slam/messages.py",
    "robot-runtime/python/replay_mapping.py", "tools/run_mapping_input_trial.py",
)


class RecordedSubscriptions:
    """Only a test boundary: paced preserved CDR callbacks, never a ROS participant."""

    def __init__(self, worker, iterator, types, stop, rate):
        self.worker, self.iterator, self.types, self.stop, self.rate = worker, iterator, types, stop, rate
        self.callbacks, self.calls, self.delivered = {}, [], 0
        self.pending, self.epoch = next(iterator, None), None
        if self.pending is None:
            raise ValueError("nonempty_recorded_input_required")
        self.origin = self.pending.elapsed_ns

    def bindings(self, topics):
        owner = self

        class Context:
            def ok(self):
                return True

            def try_shutdown(self):
                owner.calls.append("context_shutdown")

        class Node:
            def create_subscription(self, message_type, topic, callback, qos):
                owner.callbacks["cloud" if topic == topics.cloud else "imu"] = callback

            def destroy_node(self):
                owner.calls.append("node_destroy")

        class Executor:
            def __init__(self, *, context):
                pass

            def add_node(self, node):
                pass

            def shutdown(self, *, timeout_sec):
                owner.calls.append("executor_shutdown")
                return True

            def spin_once(self, *, timeout_sec):
                if owner.worker.snapshot()["state"] != "running":
                    time.sleep(min(.002, timeout_sec))
                    return
                if owner.pending is None:
                    owner.stop.set()
                    return
                if owner.epoch is None:
                    owner.epoch = time.monotonic()
                entry = owner.pending
                due = owner.epoch + (entry.elapsed_ns - owner.origin) / 1e9 / owner.rate
                remaining = due - time.monotonic()
                if remaining > 0:
                    time.sleep(min(.002, remaining, timeout_sec))
                    return
                kind = "Imu" if entry.kind == "imu" else "PointCloud2"
                message = owner.types.deserialize_cdr(entry.payload, "sensor_msgs/msg/" + kind)
                if not owner.callbacks[entry.kind](message):
                    raise RuntimeError("recorded_fixture_callback_rejected")
                owner.delivered += 1
                owner.pending = next(owner.iterator, None)

        def module(name, **entries):
            result = ModuleType(name)
            result.__dict__.update(entries)
            return result

        return {
            "rclpy": module("rclpy", init=lambda **kwargs: None, create_node=lambda *args, **kwargs: Node()),
            "rclpy.context": module("rclpy.context", Context=Context),
            "rclpy.executors": module("rclpy.executors", SingleThreadedExecutor=Executor),
            "rclpy.qos": module("rclpy.qos", QoSProfile=lambda **kwargs: kwargs,
                               HistoryPolicy=module("history", KEEP_LAST=1),
                               ReliabilityPolicy=module("reliability", BEST_EFFORT=1),
                               DurabilityPolicy=module("durability", VOLATILE=1)),
            "sensor_msgs": module("sensor_msgs"),
            "sensor_msgs.msg": module("sensor_msgs.msg", Imu=type("RecordedImu", (), {}),
                                      PointCloud2=type("RecordedCloud", (), {})),
        }


def run(args):
    from rosbags.typesys import Stores, get_typestore
    from lingxi_slam.mapping_input import MappingInput
    from lingxi_slam.mapping_worker import MappingWorker
    from lingxi_slam.messages import SensorProfile
    from lingxi_slam.passive_ros import Topics, run_passive
    from lingxi_slam.point_data import PointEncoder, decode
    from lingxi_slam.point_output import NativePointOutput, PointOutputNative
    from run_glim_mapping_trial import MappingLibrary, inventory_map, verify_extension
    from run_glim_offline_trial import Native
    from run_glim_point_output_trial import (
        LIBRARY, PacketSummary, integrity, loaded_extension, private_output,
    )
    from run_glim_stream_trial import actual_libraries
    from run_glim_supervised_replay import OwnedNative
    from slam_callback_journal import records
    from verify_slam_capture import sha256

    config, environment = configuration(args.config)
    if any(os.environ.get(key) != value for key, value in environment.items()):
        raise ValueError("unchanged_reviewed_native_environment_required")
    prepared = argparse.Namespace(**{name: Path(config[key]) for name, key in (
        ("work", "work"), ("adapter", "adapter"), ("mapping_adapter", "mappingAdapter"),
        ("bundle", "bundle"), ("reference", "reference"))},
        points=args.points, hypothesis=config["hypothesis"], rate=float(config["rate"]))
    frozen = integrity(prepared)
    mapping_library = verify_extension(prepared.mapping_adapter, prepared.work)
    mapping_digest = sha256(mapping_library)
    sources = {name: sha256(ROOT / name) for name in SOURCES}
    output = private_output(args.output)
    output.mkdir(mode=0o700)
    session, summary = uuid.uuid4().hex, PacketSummary()
    calls = dict.fromkeys(("create", "imu", "frame", "close"), 0)
    owners, loaded, encoder = set(), {}, None
    main_thread = threading.get_ident()

    def record(name):
        owner = threading.get_ident()
        if owner == main_thread or owners and owner not in owners:
            raise RuntimeError("one_non_main_mapping_point_owner_required")
        owners.add(owner)
        calls[name] += 1

    def verify_loaded():
        loaded.update(actual_libraries(prepared.work, prepared.adapter, frozen["nativeLibrariesSha256"]))
        loaded.update(loaded_extension(prepared, frozen["pointExtensionLibrarySha256"]))

    def accept(packet):
        encoded = encoder.encode(packet)
        decoded = decode(encoded, session, "recorded")
        if decoded.packet.native_frame_id != packet.native_frame_id:
            raise RuntimeError("matching_recorded_point_encoding_required")
        summary.accept(packet)

    class ObservedPoints(PointOutputNative):
        def close(self):
            was_closed = self.closed
            try:
                return super().close()
            finally:
                if not was_closed:
                    summary.finish(self.output)

    def factory():
        nonlocal encoder
        summary.begin()
        encoder = PointEncoder(session, "recorded")
        tap = NativePointOutput(args.points / "build" / LIBRARY)
        return OwnedNative(lambda: ObservedPoints(
            lambda: Native(prepared.adapter / "build/liblingxi_glim_offline.so",
                           prepared.bundle / "config",
                           prepared.work / "prefix/lib/libodometry_estimation_cpu.so"),
            tap, accept), record, verify_loaded)

    def reload_map(saved):
        with (output / "private-reload.log").open("xb") as log:
            result = subprocess.run([
                sys.executable, "-B", str(ROOT / "robot-runtime/python/replay_mapping.py"), "reload",
                "--work", str(prepared.work), "--mapping-adapter", str(prepared.mapping_adapter),
                "--bundle", str(prepared.bundle), "--saved", str(saved),
            ], stdout=log, stderr=log, env=os.environ.copy(), timeout=60, check=False)
        if result.returncode:
            raise RuntimeError("independent_mapping_point_reload_failed")
        return json.loads((saved / "reload-report.json").read_text())

    controller = MappingWorker(
        factory, lambda: MappingLibrary(mapping_library),
        SensorProfile("lidar_chest_front", "lidar_imu_chest_front", "frame_relative_nanoseconds_hypothesis"),
        prepared.hypothesis, output / "saved-map", name="Recorded mapping points",
        session=session, input_mode="recorded", inspect_map=inventory_map, reload_map=reload_map)
    mapping = MappingInput(controller)
    types = get_typestore(Stores.ROS2_HUMBLE)
    for name in ("Imu", "PointCloud2"):
        types.get_msgdef("sensor_msgs/msg/" + name)
    stop = threading.Event()
    report = {"result": "failed", "inputMode": "recorded", "saveRequested": args.save,
              "sourceSha256": sources, "mappingLibrarySha256": mapping_digest,
              "rate": prepared.rate, "actualRosUsed": False, "robotContacted": False}
    saved_stdout, saved_stderr = os.dup(1), os.dup(2)
    descriptor = os.open(output / "private-native.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    iterator = None
    try:
        os.dup2(descriptor, 1)
        os.dup2(descriptor, 2)
        iterator = records(prepared.reference / "private-callbacks.cdrjournal")
        fixture = RecordedSubscriptions(controller.worker, iterator, types, stop, prepared.rate)
        topics = Topics("/recorded_fixture/cloud", "/recorded_fixture/imu")
        if any(name == "rclpy" or name.startswith("rclpy.") for name in sys.modules):
            raise RuntimeError("fresh_non_ros_diagnostic_process_required")
        with patch.dict(sys.modules, fixture.bindings(topics)), patch.dict(os.environ, {"ROS_DISTRO": "humble"}):
            passive = run_passive(
                controller.worker, topics, 120., stop_event=stop, admission=mapping.admission,
                on_input_closed=lambda reason: mapping.finish(reason, save=args.save))
        worker = controller.worker.snapshot()
        terminal = controller.snapshot()
        points = summary.report(worker["stream"]["counts"]["estimatedPoses"])
        if (passive["stopReason"] != "requested_stop" or worker["threadAlive"] or worker["failure"]
                or fixture.delivered != frozen["journal"]["records"]
                or worker["counts"]["completedMessages"] != fixture.delivered
                or worker["counts"]["discardedMessages"] or worker["counts"]["rejectedMessages"]
                or terminal["phase"] != ("saved" if args.save else "stopped")
                or calls["create"] != 1 or calls["close"] != 1 or len(owners) != 1
                or integrity(prepared) != frozen or sha256(mapping_library) != mapping_digest
                or sources != {name: sha256(ROOT / name) for name in SOURCES}):
            raise RuntimeError("complete_same_engine_mapping_points_required")
        receipt = controller.native_session.receipt
        if args.save and receipt["counts"]["savedFrames"] != worker["stream"]["counts"]["estimatedPoses"]:
            raise RuntimeError("every_estimated_pose_saved_once_required")
        report.update(result="recorded_passive_boundary_mapping_points_verified",
                      worker=worker, terminal=terminal, points=points, nativeCalls=calls,
                      oneNonMainNativeOwner=True, syntheticRosCleanup=fixture.calls,
                      actualLoadedLibrariesSha256=loaded)
        if receipt is not None:
            report["savedCounts"] = receipt["counts"]
            report["roundTrip"] = json.loads(
                (output / "saved-map/reload-report.json").read_text())["comparison"]
    except BaseException as error:
        report["errorType"] = type(error).__name__
        raise
    finally:
        mapping.admission.close()
        controller.cancel_save()
        controller.stop()
        if iterator is not None:
            iterator.close()
        report["finalWorker"] = controller.worker.snapshot()
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        for fd in (saved_stdout, saved_stderr, descriptor):
            os.close(fd)
        private_json(output / "private-trial-report.json", report)
    return report


def public_evidence(report, output):
    from verify_slam_capture import sha256
    worker = report["finalWorker"]
    return {
        "schemaVersion": 1, "date": "2026-10-29", "result": report["result"],
        "inputMode": "recorded", "machine": "x86_64", "rate": report["rate"],
        "saveRequested": report["saveRequested"], "sourceSha256": report["sourceSha256"],
        "privateTrialReportSha256": sha256(output / "private-trial-report.json"),
        "completedMessages": worker["counts"]["completedMessages"],
        "processedScans": worker["stream"]["counts"]["processedScans"],
        "estimatedPoses": worker["stream"]["counts"]["estimatedPoses"],
        "points": report["points"], "nativeCalls": report["nativeCalls"],
        "oneNonMainNativeOwner": report["oneNonMainNativeOwner"],
        "syntheticRosCleanup": report["syntheticRosCleanup"],
        "savedCounts": report.get("savedCounts"), "roundTrip": report.get("roundTrip"),
        "workerThreadAlive": worker["threadAlive"],
        "boundaries": dict.fromkeys((
            "robotContacted", "actualRosUsed", "liveSensorsSubscribed", "motionCommandsSent",
            "targetArm64Validated", "geometricAccuracyValidated", "localizationReady",
            "navigationReady", "persistentApiPointSwitchingValidated", "deploymentApproved",
        ), False),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "points", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    _, environment = configuration(args.config)
    if not args.child:
        return subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                               *sys.argv[1:], "--child"], env=environment, check=False, timeout=180).returncode
    previous = os.umask(0o077)
    try:
        report = run(args)
        if args.evidence is not None:
            private_json(args.evidence, public_evidence(report, args.output))
        print(json.dumps({"result": report["result"], "saved": args.save,
                          "nativeCalls": report["nativeCalls"], "packets": report["points"]["packets"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
