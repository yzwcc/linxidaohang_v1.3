"""Opt-in ARM64/Humble recorded native points to an isolated paired API consumer."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import glim_point_target_probe as probe
import glim_supervised_target as target
import prepare_glim_point_consumer as consumer_bundle
import run_glim_point_pipe_trial as pipe
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
shared, points = target.shared, pipe.points
RESULT = "arm64_recorded_point_channel_closed_not_live_ui_or_navigation_acceptance"
EXTRA_SOURCES = (
    "tools/glim_point_supervised_target.py", "tools/prepare_glim_point_consumer.py",
    "docs/slam-point-arm64-target-evidence.json", "docs/slam-native-point-pipe-evidence.json",
)


def source_hashes():
    names = set((*target.SOURCES, *points.SOURCE_NAMES, *pipe.source_hashes(), *EXTRA_SOURCES))
    return {name: sha256(ROOT / name) for name in sorted(names)}


def integrity(args):
    frozen = target.inputs(args)
    point_manifest = probe.verify_bundle(args.point_bundle, args.point_manifest_sha256)
    evidence = shared.load(ROOT / "docs/slam-point-arm64-target-evidence.json")
    runtime = consumer_bundle.verify_bundle(args.consumer_root, args.consumer_manifest_sha256)
    if (evidence["result"] != probe.RESULT or evidence["bundleManifestSha256"] != args.point_manifest_sha256
            or evidence["coreManifestSha256"] != frozen["coreBundleManifestSha256"]
            or evidence["actualLoadedLibrariesSha256"][probe.LIBRARY]
            != point_manifest["files"]["lib/" + probe.LIBRARY]["sha256"]
            or any(frozen["verifiedCoreLibrariesSha256"].get(name) != value
                   for name, value in point_manifest["expectedCoreLibrariesSha256"].items())
            or runtime["sourceSha256"] != pipe.source_hashes()
            or runtime["referenceEvidenceSha256"] != sha256(ROOT / "docs/slam-native-point-pipe-evidence.json")):
        raise ValueError("same_verified_arm64_extension_core_and_recorded_consumer_required")
    return {
        **frozen, "pointSourceSha256": source_hashes(),
        "pointManifestSha256": args.point_manifest_sha256,
        "pointLoadingEvidenceSha256": sha256(ROOT / "docs/slam-point-arm64-target-evidence.json"),
        "pointExtensionLibrarySha256": point_manifest["files"]["lib/" + probe.LIBRARY]["sha256"],
        "consumerManifestSha256": args.consumer_manifest_sha256,
        "consumerBundleSha256": runtime["consumerBundleSha256"],
        "nodeBinarySha256": runtime["files"]["node/bin/node"]["sha256"],
    }


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

    channel = None

    def factory():
        nonlocal channel
        summary.begin()
        channel = pipe.connected_output(args, shared.session_id(os.environ.get("LINGXI_SLAM_SESSION")))
        output = None
        try:
            output = NativePointOutput(args.point_bundle / "lib" / probe.LIBRARY)

            def accept(packet):
                summary.accept(packet)
                channel(packet)

            return ObservedNative(lambda: Native(
                args.root / "lib/liblingxi_glim_offline.so", args.root / "config",
                args.core_root / "lib/libodometry_estimation_cpu.so"), output, accept)
        except BaseException:
            if output is not None and not output.closed:
                output.close()
            channel.close()
            raise
    return factory


def child(args, sink):
    import numpy as np
    from rclpy.serialization import deserialize_message
    from rclpy.type_support import check_for_type_support
    from sensor_msgs.msg import Imu, PointCloud2
    from glim_target_worker import observed_libraries
    from slam_target_probe import verify_bundle

    frozen = integrity(args)
    for message_type in (Imu, PointCloud2):
        check_for_type_support(message_type)
    manifest = shared.load(args.root / "replay-manifest.json")
    core, point_manifest = verify_bundle(args.core_root), probe.verify_bundle(args.point_bundle, args.point_manifest_sha256)
    summary, transport, extension = points.PacketSummary(), {}, {}

    def loaded():
        libraries = observed_libraries(args.root, args.core_root, manifest, core)
        if libraries != frozen["referenceLibrariesSha256"]:
            raise ValueError("unchanged_actual_target_native_libraries_required")
        observed = probe.mapped_libraries(
            args.point_bundle, args.core_root, point_manifest, Path("/proc/self/maps").read_text())
        extension[probe.LIBRARY] = observed[probe.LIBRARY]
        return libraries

    code = shared.recorded_child(
        args, sink, integrity=frozen, native_factory=native_factory(args, summary, transport),
        verify_loaded=loaded,
        decode=lambda kind, payload: deserialize_message(payload, Imu if kind == "imu" else PointCloud2),
        verify_inputs=lambda: integrity(args),
        versions={"numpy": np.__version__, "decoder": "installed_humble_rclpy", "python": platform.python_version()},
    )
    if code:
        return code
    report = shared.load(args.output / "child-report.json")
    expected = report["worker"]["stream"]["counts"]["estimatedPoses"]
    result = points.verified_points(summary.report(expected), expected)
    if transport != {"sent": result["packets"], "replaced": 0, "failed": False, "threadAlive": False}:
        raise ValueError("complete_target_point_delivery_and_closed_writer_required")
    result.update(actualLoadedExtensionSha256=extension, writer=transport, inputMode="recorded")
    shared.private_json(args.output / "point-report.json", result)
    return 0


def stage(args):
    staged, runtime, copied = target.stage(args)
    for name, expected in source_hashes().items():
        destination = runtime / name
        if not destination.exists():
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        if destination.is_symlink() or sha256(destination) != expected:
            raise ValueError("same_staged_arm64_point_sources_required")
        copied[name] = expected
    return staged, runtime, copied


def run(args):
    if getattr(args, "operator_confirmed", False) is not True:
        raise ValueError("fresh_stationary_supervised_estop_confirmation_required")
    output = points.private_output(args.output)
    if any(output == path or output in path.parents or path in output.parents for path in (
            args.root, args.core_root, args.reference, args.point_bundle, args.consumer_root)):
        raise ValueError("new_target_point_output_must_not_overlap_inputs")
    frozen = integrity(args)
    output.mkdir(mode=0o700, parents=True)
    owner, consumer = None, None
    report = {
        "schemaVersion": 1, "result": "running", "inputIntegrity": frozen,
        "limits": asdict(shared.LIMITS), "inputMode": "recorded", "replayRate": args.rate,
        "healthChannelContainsPoints": False, "pacedRecordedInputNotLivePerformance": True,
        "boundaries": dict.fromkeys(shared.BOUNDARIES, False),
    }
    try:
        staged, runtime, copied = stage(args)
        report["runtimeSourceFilesSha256"] = copied
        if integrity(staged) != frozen:
            raise ValueError("same_staged_arm64_recorded_point_inputs_required")
        point_socket = output / "point.sock"
        if len(os.fsencode(point_socket)) >= 100:
            raise ValueError("bounded_private_unix_socket_path_required")
        command = ["/usr/bin/nice", "-n", "10", sys.executable, "-B",
                   str(runtime / "tools/glim_point_supervised_target.py"), "child"]
        for name in ("root", "core_root", "reference", "output", "point_bundle", "consumer_root",
                     "point_manifest_sha256", "consumer_manifest_sha256", "hypothesis", "rate"):
            command.extend(("--" + name.replace("_", "-"), str(getattr(staged, name))))
        command.extend(("--point-socket", str(point_socket)))
        owner = shared.ProcessSupervisor(
            command, cwd=output, environment=target.child_environment(args.root, args.core_root),
            stderr_path=output / "private-child.log", limits=shared.LIMITS)
        with (output / "private-consumer.log").open("xb") as log:
            consumer = subprocess.Popen([
                "/usr/bin/nice", "-n", "10", str(args.consumer_root / "node/bin/node"),
                str(args.consumer_root / "consumer.mjs"), str(output), str(point_socket), owner.session,
            ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log, cwd=args.consumer_root,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, start_new_session=True)
        pipe.wait_ready(consumer)
        report["process"] = shared.coordinate(owner, frozen["journal"]["records"], 110.)
        if consumer.wait(timeout=5.) != 0:
            raise RuntimeError("target_recorded_consumer_failed")
        child_report = shared.load(output / "child-report.json")
        native, received = (shared.load(output / name) for name in ("point-report.json", "consumer-report.json"))
        shared.complete_child(child_report, frozen["journal"])
        points.verified_points(native, child_report["worker"]["stream"]["counts"]["estimatedPoses"])
        pipe.verify_consumer(received, native)
        if (child_report["machine"] != "aarch64" or child_report["session"] != owner.session
                or child_report["inputIntegrity"] != frozen or child_report["result"] != shared.CHILD_RESULT
                or child_report["versions"]["decoder"] != "installed_humble_rclpy"
                or received["host"] != {"machine": "arm64", "node": "v" + consumer_bundle.VERSION}
                or native["actualLoadedExtensionSha256"] != {probe.LIBRARY: frozen["pointExtensionLibrarySha256"]}
                or integrity(args) != frozen):
            raise ValueError("complete_same_session_arm64_point_composition_required")
        report["result"] = RESULT
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        try:
            report["process"] = None if owner is None else owner.stop()
        finally:
            report["consumerProcess"] = pipe.stop_consumer(consumer)
            report["privateReportsSha256"] = {
                name: sha256(output / name) for name in (
                    "child-report.json", "point-report.json", "consumer-report.json",
                    "private-child.log", "private-consumer.log") if (output / name).is_file()}
            shared.private_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "child"))
    for name in ("root", "core-root", "reference", "output", "point-bundle", "consumer-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("point-manifest-sha256", "consumer-manifest-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--point-socket", type=Path)
    parser.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
    parser.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
    parser.add_argument("--operator-confirmed", action="store_true")
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
        print(json.dumps({"result": run(args)["result"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
