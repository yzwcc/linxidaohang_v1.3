"""Offline recorded GLIM -> private Unix stream -> paired API isolation trial.

The unchanged ProcessSupervisor owns the native child and health stdout.
The separate Node diagnostic consumer never presents recorded data as live.
No ROS initialization, robot connection, deployment or actuation.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import time

import run_glim_point_output_trial as points
from verify_slam_capture import sha256


ROOT = points.ROOT
replay = points.replay
RESULT = "recorded_native_point_pipe_and_api_guard_closed_not_live_or_target_acceptance"
TRANSPORT_NAMES = (
    "tools/run_glim_point_pipe_trial.py", "tools/slam-point-api-consumer.ts",
    "robot-slam/lingxi_slam/point_data.py", "robot-slam/lingxi_slam/point_pipe.py",
    "robot-api/src/slam-points.ts", "package-lock.json",
)


def source_hashes():
    names = set(TRANSPORT_NAMES)
    names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "robot-api/src").rglob("*.ts"))
    names.add("shared/contracts.ts")
    return {name: sha256(ROOT / name) for name in sorted(names)}


def integrity(args):
    return {
        **points.integrity(args), "transportSourceSha256": source_hashes(),
        "consumerBundleSha256": sha256(args.consumer), "nodeBinarySha256": sha256(args.node),
    }


def connected_output(args, session):
    from lingxi_slam.point_data import PointPipeOutput
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(1.)
        connection.connect(str(args.point_socket))
        fd = connection.detach()
    finally:
        connection.close()
    return PointPipeOutput(session, "recorded", fd)


def native_factory(args, summary, transport):
    from lingxi_slam.point_output import NativePointOutput, PointOutputNative
    from run_glim_offline_trial import Native

    class ObservedNative(PointOutputNative):
        def close(self):
            if self.closed:
                return None
            try:
                return super().close()
            finally:
                try:
                    summary.finish(self.output)
                finally:
                    channel.close()
                    transport.update(sent=channel.writer.sent, replaced=channel.writer.replaced,
                                     failed=channel.writer.failed, threadAlive=channel.writer.thread.is_alive())

    def factory():
        nonlocal channel
        summary.begin()
        channel = connected_output(args, replay.session_id(os.environ.get("LINGXI_SLAM_SESSION")))
        output = None
        try:
            output = NativePointOutput(args.points / "build" / points.LIBRARY)

            def accept(packet):
                summary.accept(packet)
                channel(packet)

            return ObservedNative(lambda: Native(
                args.adapter / "build/liblingxi_glim_offline.so", args.bundle / "config",
                args.work / "prefix/lib/libodometry_estimation_cpu.so"), output, accept)
        except BaseException:
            if output is not None and not output.closed:
                output.close()
            channel.close()
            raise
    channel = None
    return factory


def child(args, sink):
    import numpy as np
    from importlib import metadata
    from rosbags.typesys import Stores, get_typestore
    from run_glim_stream_trial import actual_libraries

    frozen = integrity(args)
    if any(os.environ.get(key) != value for key, value in replay.child_environment(args.work).items()):
        raise ValueError("unchanged_explicit_child_environment_required")
    types = get_typestore(Stores.ROS2_HUMBLE)
    for name in ("Imu", "PointCloud2"):
        types.get_msgdef("sensor_msgs/msg/" + name)
    summary, transport, loaded = points.PacketSummary(), {}, {}

    def verify_loaded():
        observed = actual_libraries(args.work, args.adapter, frozen["nativeLibrariesSha256"])
        baseline = replay.load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
        if observed != baseline["actualLoadedLibrariesSha256"]:
            raise ValueError("same_frozen_point_pipe_native_libraries_required")
        loaded.update(points.loaded_extension(args, frozen["pointExtensionLibrarySha256"]))
        return observed

    code = replay.recorded_child(
        args, sink, integrity=frozen, native_factory=native_factory(args, summary, transport),
        verify_loaded=verify_loaded,
        decode=lambda kind, data: types.deserialize_cdr(
            data, "sensor_msgs/msg/" + ("Imu" if kind == "imu" else "PointCloud2")),
        verify_inputs=lambda: integrity(args),
        versions={"numpy": np.__version__, "rosbags": metadata.version("rosbags")},
    )
    if code:
        return code
    report = replay.load(args.output / "child-report.json")
    result = summary.report(report["worker"]["stream"]["counts"]["estimatedPoses"])
    points.verified_points(result, report["worker"]["stream"]["counts"]["estimatedPoses"])
    if transport != {"sent": result["packets"], "replaced": 0, "failed": False, "threadAlive": False}:
        raise ValueError("all_recorded_points_sent_and_writer_closed_required")
    result.update(actualLoadedExtensionSha256=loaded, writer=transport, inputMode="recorded")
    replay.private_json(args.output / "point-report.json", result)
    return 0


def wait_ready(process, seconds=15.):
    deadline, data = time.monotonic() + seconds, bytearray()
    os.set_blocking(process.stdout.fileno(), False)
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if selector.select(.05):
                chunk = os.read(process.stdout.fileno(), 128)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > 6 or not b"ready\n".startswith(data):
                    raise ValueError("exact_private_consumer_ready_line_required")
                if data == b"ready\n":
                    return
            if process.poll() is not None:
                break
    raise RuntimeError("point_consumer_not_ready")


def stop_consumer(process):
    forced = False
    if process is not None and process.poll() is None:
        forced = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2.)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2.)
    if process is not None:
        process.stdout.close()
    return {"exitCode": None if process is None else process.returncode,
            "forcedTermination": forced, "processAlive": process is not None and process.poll() is None}


def verify_consumer(consumer, output):
    count = output["packets"]
    expected = {
        "records": count, "copiedPoints": output["copiedPoints"], "retired": 0,
        "firstNativeFrameId": 1, "lastNativeFrameId": count,
        "allPacketBytesSha256": output["allPacketBytesSha256"], "inputMode": "recorded",
    }
    if (consumer["result"] != "recorded_native_points_received_api_live_guard_checked"
            or consumer["points"] != expected or consumer["inputMode"] != "recorded"
            or consumer["transport"] != "private_same_host_unix_stream"
            or consumer["api"] != {
                "pairedWebSocketChecked": True, "recordedInputRejectedAsLive": True,
                "beforeAndAfterEofUnavailable": True, "readinessUnchanged": True,
                "robotCommandsSent": False, "tcpListenerStarted": False,
            }):
        raise ValueError("matching_complete_recorded_point_transport_and_api_guard_required")


def run(args):
    frozen, output = integrity(args), points.private_output(args.output)
    if any(output == path or path in output.parents
           for path in (args.work, args.adapter, args.points, args.bundle, args.reference)):
        raise ValueError("separate_private_recorded_point_pipe_output_required")
    output.mkdir(mode=0o700, parents=True)
    staged, runtime, _ = replay.stage_private_inputs(args)
    for name in dict.fromkeys((*points.SOURCE_NAMES, *source_hashes())):
        destination = runtime / name
        if not destination.exists():
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        if sha256(destination) != sha256(ROOT / name):
            raise ValueError("identical_staged_point_pipe_source_required")
    point_socket = output / "point.sock"
    if len(os.fsencode(point_socket)) >= 100:
        raise ValueError("bounded_private_unix_socket_path_required")
    command = [sys.executable, "-B", str(runtime / "tools/run_glim_point_pipe_trial.py"), "child"]
    for name in ("work", "adapter", "points", "bundle", "reference", "output", "hypothesis",
                 "rate", "node", "consumer"):
        command.extend(("--" + name, str(getattr(staged, name))))
    command.extend(("--point-socket", str(point_socket)))
    owner = replay.ProcessSupervisor(command, cwd=output, environment=replay.child_environment(args.work),
                                     stderr_path=output / "private-child.log", limits=replay.LIMITS)
    report = {
        "schemaVersion": 1, "result": "running", "inputIntegrity": frozen,
        "boundaries": dict.fromkeys(replay.BOUNDARIES, False), "limits": asdict(replay.LIMITS),
        "healthChannelContainsPoints": False, "replayRate": args.rate, "inputMode": "recorded",
    }
    consumer = None
    try:
        with (output / "private-consumer.log").open("xb") as log:
            consumer = subprocess.Popen(
                [str(args.node), str(args.consumer), str(output), str(point_socket), owner.session],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log, cwd=ROOT,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, start_new_session=True,
            )
        wait_ready(consumer)
        report["process"] = replay.coordinate(owner, frozen["journal"]["records"], 110.)
        if consumer.wait(timeout=5.) != 0:
            raise RuntimeError("recorded_point_consumer_failed")
        child_report = replay.load(output / "child-report.json")
        replay.complete_child(child_report, frozen["journal"])
        native = replay.load(output / "point-report.json")
        received = replay.load(output / "consumer-report.json")
        points.verified_points(native, child_report["worker"]["stream"]["counts"]["estimatedPoses"])
        verify_consumer(received, native)
        if (child_report["session"] != owner.session or child_report["inputIntegrity"] != frozen
                or child_report["result"] != replay.CHILD_RESULT or integrity(args) != frozen):
            raise ValueError("same_owned_point_child_and_unchanged_inputs_required")
        report.update(result=RESULT, privateReportsSha256={
            name: sha256(output / name) for name in ("child-report.json", "point-report.json", "consumer-report.json")
        })
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        report["process"] = owner.stop()
        report["consumerProcess"] = stop_consumer(consumer)
        replay.private_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "child"))
    for name in ("work", "adapter", "points", "bundle", "reference", "output", "node", "consumer"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--point-socket", type=Path)
    parser.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
    parser.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    previous = os.umask(0o077)
    try:
        if args.command == "child":
            if args.point_socket is None:
                raise ValueError("explicit_private_point_socket_required")
            with os.fdopen(os.dup(1), "wb", buffering=0) as sink:
                os.dup2(2, 1)
                return child(args, sink)
        result = run(args)
        print(json.dumps({"result": result["result"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
