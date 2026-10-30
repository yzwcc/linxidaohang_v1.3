"""Passive Humble sensors and saved-map tracking on one native owner thread."""
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
import os
import platform as host_platform
import threading

from controlled_localization import native_library_inventory, source_hashes
from localization_control import (
    LocalizationControlServer, LocalizedNative, digest, load_map, make_session)
from mapping_platform import verify_loaded_libraries
from mapping_service import private_json
from live_child import serve, verified_child_session


def verify_completion(report, final, *, frozen, binding, sources, config_hash, base_scale=None):
    """Live shutdown accounts for accepted callbacks, including the discarded tail."""
    identity = asdict(binding)
    worker, state = report["worker"], final["state"]
    counts, stream = worker["counts"], worker["stream"]
    native = report["nativeCalls"]
    integrity = report["inputIntegrity"]
    numeric = ("acceptedImu", "acceptedClouds", "completedMessages",
               "discardedMessages", "rejectedMessages")
    stream_counts = stream["counts"]
    if base_scale is not None:
        base, inputs = state.get("basePose", {}), report.get("basePoseInput", {})
        if (base.get("status") != "unavailable" or base.get("mapFromBase") is not None
                or base.get("lastFrame") != 0 or base.get("scanHeaderNs") is not None
                or base.get("effectiveScanNs") is not None or base.get("metresPerMapUnit") != base_scale
                or base.get("coordinateFrame") != "lingxi_saved_map" or base.get("bodyFrame") != "base_link"
                or any(base.get(name) is not False for name in ("physicalScaleValidated", "navigationReady"))
                or inputs.get("closed") is not True or inputs.get("pendingScanTimes") != 0
                or inputs.get("retainedDynamicTransforms") != 0 or inputs.get("metresPerMapUnit") != base_scale
                or any(inputs.get(name) is not False for name in ("physicalScaleValidated", "navigationReady"))):
            raise ValueError("closed_base_pose_inputs_and_revoked_base_pose_required")
    elif "basePose" in state or "basePoseInput" in report:
        raise ValueError("explicit_base_pose_option_required")
    if (binding.input_mode != "live" or report["result"] != "live_localization_runtime_closed"
            or report["session"] != binding.session or report["inputMode"] != "live"
            or report["stopReceived"] is not True or report["failures"] != []
            or report["controlThreadAlive"] is not False
            or worker["state"] != "stopped" or worker["failure"] is not None
            or worker["threadAlive"] is not False
            or any(type(counts[key]) is not int or counts[key] < 0 for key in numeric)
            or counts["acceptedClouds"] < 1 or counts["acceptedImu"] < 1
            or counts["completedMessages"] < 1 or counts["rejectedMessages"] != 0
            or counts["acceptedClouds"] + counts["acceptedImu"]
                != counts["completedMessages"] + counts["discardedMessages"]
            or any(worker[key] != 0 for key in (
                "queuedMessages", "queuedClouds", "queuedPayloadBytes", "inFlightMessages"))
            or type(stream) is not dict or stream["state"] != "finished" or stream["failure"] is not None
            or any(type(stream_counts[key]) is not int or stream_counts[key] < 1
                   for key in ("estimatedPoses", "insertedImu", "processedScans"))
            or any(type(native[key]) is not int for key in ("create", "close", "imu", "frame"))
            or native["create"] != 1 or native["close"] != 1
            or native["imu"] != stream["counts"]["insertedImu"]
            or native["frame"] != stream["counts"]["processedScans"]
            or report["nativeOwnerIsSingleNonMainThread"] is not True
            or report["passive"]["stopReason"] != "requested_stop"
            or any(integrity.get(key) != value for key, value in frozen.items())
            or integrity["localizationBinding"] != identity
            or integrity["localizationSourceSha256"] != sources
            or integrity["localizationConfigSha256"] != config_hash
            or state["binding"] != identity or state["state"] != "stopped"
            or state["mapFromSensor"] is not None or state["referenceFrame"] is not None
            or any(state[key] is not True for key in ("cleanupComplete", "workerClosed"))
            or any(state[key] is not False for key in (
                "workerReady", "requestPending", "localizationReady", "navigationReady"))
            or final["robotContacted"] is not True
            or any(final[key] is not False for key in (
                "motionCommandsSent", "mapProfileAssociationVerified"))
            or report["robotContacted"] is not True
            or any(report[key] is not False for key in (
                "motionCommandsSent", "localizationReady", "navigationReady",
                "mapProfileAssociationVerified"))):
        raise ValueError("complete_same_session_live_localization_cleanup_required")


def child(args, sink, platform):
    import small_gicp
    from lingxi_slam.messages import SensorProfile
    from lingxi_slam.passive_ros import Topics, run_passive
    from lingxi_slam.point_output import NativePointOutput, PointOutputNative
    from lingxi_slam.worker import SensorWorker
    from slam_bag_audit import CLOUD, IMU

    if not platform.arm64 or platform.mode != "live":
        raise ValueError("verified_arm64_live_localization_platform_required")
    child_session = verified_child_session(platform.args)
    binding, points = load_map(
        args.saved_map, args.map_id, args.receipt_sha256, platform.profile, args.profile_sha256,
        session=child_session, input_mode="live", sensor_provenance=platform.sensor_provenance())
    options = platform.value.get("basePose")
    session = make_session(binding, points, base_metres_per_map_unit=(
        options["metresPerMapUnit"] if options is not None else None))
    base_owner = None
    if options is not None:
        from live_base_pose import LiveBasePose
        base_owner = LiveBasePose(session, SensorProfile(
            "lidar_chest_front", "lidar_imu_chest_front", "frame_relative_nanoseconds_hypothesis"),
            platform.hypothesis)
    sources, config_hash = source_hashes(), digest(args.config.read_bytes())
    gicp = Path(small_gicp.__file__).resolve(strict=True)
    gicp_hash = digest(gicp.read_bytes())
    frozen = {**platform.frozen, "localizationBinding": asdict(binding),
              "localizationSourceSha256": sources, "localizationConfigSha256": config_hash,
              "smallGicpExtensionSha256": gicp_hash}
    calls = dict.fromkeys(("create", "imu", "frame", "close"), 0)
    owners, main_thread, libraries = set(), threading.get_ident(), {}

    def record(name):
        owner = threading.get_ident()
        if owner == main_thread or owners and owner not in owners:
            raise RuntimeError("single_non_main_live_localization_owner_required")
        owners.add(owner)
        calls[name] += 1

    def factory():
        def native(accept):
            output = NativePointOutput(platform.point_library)
            wrapped = PointOutputNative(lambda: platform.native(record), output, accept)
            try:
                platform.loaded(points=True)
                verify_loaded_libraries({gicp: gicp_hash}, Path("/proc/self/maps").read_text())
                libraries.update(native_library_inventory(platform))
                libraries[gicp.name] = gicp_hash
                return wrapped
            except BaseException:
                wrapped.close()
                raise
        return LocalizedNative(session, native)

    def revoke():
        # Only the mailbox changes here; native operations stay on the sensor worker.
        session.stop_admission()

    worker = SensorWorker(
        factory, SensorProfile("lidar_chest_front", "lidar_imu_chest_front",
                               "frame_relative_nanoseconds_hypothesis"), platform.hypothesis,
        on_frame=base_owner.frame if base_owner is not None else None)
    report = {"result": "failed", "session": binding.session, "inputMode": "live",
              "robotContacted": True, "motionCommandsSent": False,
              "localizationReady": False, "navigationReady": False,
              "mapProfileAssociationVerified": False, "inputIntegrity": frozen,
              "machine": host_platform.machine(),
              "versions": {**{name: version(name) for name in ("numpy", "scipy", "small-gicp")},
                           "python": host_platform.python_version(), "input": "installed_humble_rclpy"}}
    try:
        with LocalizationControlServer(args.output / "localization.sock", session) as server:
            server.start()
            def runner(*args, **kwargs):
                return run_passive(*args, **kwargs, **({
                    "setup": base_owner.setup, "on_sensor": base_owner.sensor} if base_owner is not None else {}))
            live = serve(worker, Topics(CLOUD, IMU), sink, runner=runner,
                         session=binding.session, seconds=platform.value["maxSeconds"], on_stopping=revoke)
            report.update(live)
    except Exception as error:
        report["errorType"] = type(error).__name__
    finally:
        # Also covers socket-start failure before serve takes ownership.
        try:
            revoke()
        except Exception as error:
            report.update(result="failed", errorType=type(error).__name__)
        try:
            worker.stop()
        except Exception as error:
            report.update(result="failed", errorType=type(error).__name__)
        if base_owner is not None:
            base_owner.close()
            report["basePoseInput"] = base_owner.summary()
        final = {"state": session.state(), "robotContacted": True, "motionCommandsSent": False,
                 "mapProfileAssociationVerified": False}
        report.update(nativeCalls=calls, nativeOwnerIsSingleNonMainThread=len(owners) == 1,
                      actualLoadedLibrariesSha256=libraries)
        if report["result"] == "live_sensor_session_closed":
            report["result"] = "live_localization_runtime_closed"
        private_json(args.output / "localization-final.json", final)
        try:
            reloaded, _ = load_map(
                args.saved_map, args.map_id, args.receipt_sha256, platform.profile,
                args.profile_sha256, session=binding.session, input_mode="live",
                sensor_provenance=platform.sensor_provenance())
            if (platform.verify() != platform.frozen or source_hashes() != sources or reloaded != binding
                    or digest(args.config.read_bytes()) != config_hash or digest(gicp.read_bytes()) != gicp_hash):
                raise ValueError("unchanged_live_localization_inputs_required")
            verify_completion(report, final, frozen=platform.frozen, binding=binding,
                              sources=sources, config_hash=config_hash, base_scale=session.base_scale)
        except Exception as error:
            report.update(result="failed", errorType=type(error).__name__)
        private_json(args.output / "child-report.json", report)
    return 0 if report["result"] == "live_localization_runtime_closed" else 1
