"""Opt-in passive Humble input with health IPC and a separate corrected-point stream."""
import json
import os
from pathlib import Path
import selectors
import socket
import sys
import threading


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "tools")]
from lingxi_slam.process_protocol import HealthEmitter, session_id
from run_glim_supervised_replay import StopPipe


def serve(worker, topics, sink, *, runner, session, control_fd=0, writer_failed=lambda: False):
    """The control thread observes counters only; native methods stay on the worker."""
    emitter = HealthEmitter(sink, session)
    stop, done = threading.Event(), threading.Event()
    command, failures = StopPipe(), []

    def control():
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(control_fd, selectors.EVENT_READ)
                while not done.is_set():
                    snapshot = worker.snapshot()
                    emitter.emit(snapshot)
                    if snapshot["state"] == "failed" or writer_failed():
                        failures.append("live_sensor_or_writer_failed")
                        stop.set()
                        return
                    if selector.select(.05):
                        if command.feed(os.read(control_fd, 6)):
                            stop.set()
                            return
        except Exception:
            failures.append("live_parent_control_failed")
            stop.set()

    thread = threading.Thread(target=control, name="live-point-control", daemon=True)
    report = {"result": "failed", "stopReceived": False}
    try:
        thread.start()
        report["passive"] = runner(worker, topics, 300., stop_event=stop)
    except Exception:
        failures.append("passive_sensor_session_failed")
    finally:
        stop.set()
        done.set()
        if thread.ident is not None:
            thread.join(1.)
        try:
            terminal = worker.stop()
        except Exception:
            failures.append("live_worker_cleanup_failed")
            terminal = worker.snapshot()
        if thread.is_alive():
            failures.append("live_control_thread_not_closed")
        report.update(worker=terminal, stopReceived=command.received, controlThreadAlive=thread.is_alive())
        if (failures or not command.received or terminal["state"] != "stopped"
                or terminal["threadAlive"] or terminal["failure"]):
            terminal = {**terminal, "state": "failed", "failure": "live_point_session_failed"}
        else:
            report["result"] = "live_sensor_session_closed"
        try:
            emitter.emit(terminal)
        except Exception:
            failures.append("live_terminal_health_failed")
            report["result"] = "failed"
        report["failures"] = failures.copy()
    return report


def verified_inputs(args):
    import glim_live_session as live
    import glim_point_target_probe as probe
    from glim_target_replay import verify_replay_bundle
    from slam_target_probe import verify_bundle
    from run_glim_offline_trial import explicit_diagnostic_profile
    from verify_slam_capture import sha256

    session_id(os.environ.get("LINGXI_SLAM_SESSION"))
    live.validate_environment(args.root, args.core_root)
    baseline = json.loads((ROOT / "docs/slam-humble-live-evidence.json").read_text(encoding="utf-8"))
    if (sha256(args.root / "replay-manifest.json") != baseline["replayManifestSha256"]
            or sha256(args.core_root / "bundle-manifest.json") != baseline["coreBundleManifestSha256"]
            or sha256(Path(live.DDS_PROFILE)) != baseline["runs"]["start"]["ddsProfileSha256"]):
        raise ValueError("same_verified_live_inputs_and_dds_profile_required")
    manifest = verify_replay_bundle(args.root, live.PURPOSE)
    core = verify_bundle(args.core_root)
    points = probe.verify_bundle(args.point_bundle, args.point_manifest_sha256)
    loading = json.loads((ROOT / "docs/slam-point-arm64-target-evidence.json").read_text(encoding="utf-8"))
    if (loading["bundleManifestSha256"] != args.point_manifest_sha256
            or loading["coreManifestSha256"] != baseline["coreBundleManifestSha256"]
            or loading["actualLoadedLibrariesSha256"][probe.LIBRARY] != points["files"]["lib/" + probe.LIBRARY]["sha256"]):
        raise ValueError("same_verified_live_point_extension_required")
    live.verify_control(args.root)
    explicit_diagnostic_profile(
        json.loads((args.root / "private-profile.json").read_text()),
        json.loads((args.root / "private-sensor-diagnostic.json").read_text()), args.hypothesis)
    return manifest, core, points


def run(args, sink):
    import glim_live_session as live
    import glim_point_target_probe as probe
    from glim_target_worker import observed_libraries
    from lingxi_slam.messages import SensorProfile
    from lingxi_slam.passive_ros import Topics, run_passive
    from lingxi_slam.point_data import PointPipeOutput
    from lingxi_slam.point_output import NativePointOutput, PointOutputNative
    from lingxi_slam.worker import SensorWorker
    from slam_bag_audit import CLOUD, IMU

    frozen = verified_inputs(args)
    session = session_id(os.environ.get("LINGXI_SLAM_SESSION"))
    if (args.point_socket.parent != Path.cwd() or args.point_socket.parent.stat().st_mode & 0o077
            or args.point_socket.is_symlink() or len(os.fsencode(args.point_socket)) >= 100):
        raise ValueError("private_owned_live_point_socket_required")
    calls = dict.fromkeys(("create", "imu", "frame", "close"), 0)
    owners, main_thread, channels = set(), threading.get_ident(), []

    def record(name):
        owner = threading.get_ident()
        if owner == main_thread or owners and owner not in owners:
            raise RuntimeError("single_non_main_live_native_owner_required")
        owners.add(owner)
        calls[name] += 1

    def loaded():
        actual = observed_libraries(args.root, args.core_root, frozen[0], frozen[1])
        baseline = json.loads((ROOT / "docs/slam-supervised-arm64-evidence.json").read_text(encoding="utf-8"))
        if actual != baseline["actualLoadedLibrariesSha256"]:
            raise ValueError("same_actual_live_native_libraries_required")
        probe.mapped_libraries(args.point_bundle, args.core_root, frozen[2], Path("/proc/self/maps").read_text())

    class OutputNative(PointOutputNative):
        def close(self):
            try:
                return super().close()
            finally:
                channel.close()

    channel = None

    def factory():
        nonlocal channel
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        output = descriptor = None
        try:
            connection.settimeout(2.)
            connection.connect(str(args.point_socket))
            connection.settimeout(None)
            descriptor = connection.detach()
            channel = PointPipeOutput(session, "live", descriptor)
            descriptor = None
            channels.append(channel.writer)
            output = NativePointOutput(args.point_bundle / "lib" / probe.LIBRARY)
            return OutputNative(lambda: live.NativeOwner(factory, args.root, args.core_root, loaded),
                                output, channel)
        except BaseException:
            connection.close()
            try:
                if output is not None and not output.closed:
                    output.close()
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                if channel is not None:
                    channel.close()
            raise

    factory.record = record
    worker = SensorWorker(
        factory, SensorProfile("lidar_chest_front", "lidar_imu_chest_front",
                               "frame_relative_nanoseconds_hypothesis"), args.hypothesis)
    report = serve(worker, Topics(CLOUD, IMU), sink, runner=run_passive, session=session,
                   writer_failed=lambda: any(writer.failed for writer in channels))
    report.update(nativeCalls=calls, nativeOwnerIsSingleNonMainThread=len(owners) == 1,
                  writer=[{"sent": writer.sent, "replaced": writer.replaced, "failed": writer.failed,
                           "threadAlive": writer.thread.is_alive()} for writer in channels],
                  inputMode="live", mappingReady=False, localizationReady=False, navigationReady=False)
    if (report["result"] != "live_sensor_session_closed" or calls["create"] != 1 or calls["close"] != 1
            or len(owners) != 1 or any(writer.thread.is_alive() or writer.failed for writer in channels)
            or verified_inputs(args) != frozen):
        report["result"] = "failed"
    descriptor = os.open("live-child-report.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return 0 if report["result"] == "live_sensor_session_closed" else 1
