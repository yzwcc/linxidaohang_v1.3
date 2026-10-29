"""Opt-in recorded-input validation of the reusable mapping worker; no ROS or control."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "tools")]


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, allow_nan=False, indent=2)
        stream.write("\n")


def reload_saved(args):
    from run_glim_mapping_trial import MappingLibrary, compare_points, inventory_map, verify_extension
    library = verify_extension(args.mapping_adapter, args.work)
    before = inventory_map(args.saved / "map")
    stats = (ctypes.c_double * 4)()
    native = MappingLibrary(library)
    native.call("reload", os.fsencode(args.bundle / "config"), os.fsencode(args.saved / "map"),
                os.fsencode(args.saved / "private-reloaded-points.f64"), stats)
    if (any(value <= 0 or value != int(value) for value in stats)
            or any(int(stats[index]) != before["num_submaps"] for index in (0, 2, 3))):
        raise ValueError("complete_saved_graph_reload_required")
    comparison = compare_points(args.saved / "private-saved-points.f64",
                                args.saved / "private-reloaded-points.f64", int(stats[1]))
    if inventory_map(args.saved / "map") != before:
        raise ValueError("saved_map_changed_during_reload")
    private_json(args.saved / "reload-report.json", {
        "pid": os.getpid(), "loadedSubmaps": int(stats[0]),
        "loadedGraphFactors": int(stats[2]), "loadedGraphValues": int(stats[3]),
        "comparison": comparison, "mapInventory": before,
    })


def run(args):
    from rosbags.typesys import Stores, get_typestore
    from lingxi_slam.mapping_worker import MappingWorker
    from lingxi_slam.messages import SensorProfile
    from run_glim_mapping_trial import MappingLibrary, inventory_map, verify_extension
    from run_glim_offline_trial import Native
    from run_glim_stream_trial import actual_libraries
    from run_glim_supervised_replay import OwnedNative, ReplayTiming, child_environment, inputs
    from slam_callback_journal import records
    from verify_slam_capture import sha256

    frozen = inputs(args)
    expected_environment = child_environment(args.work)
    if any(os.environ.get(key) != value for key, value in expected_environment.items()):
        raise ValueError("unchanged_reviewed_native_environment_required")
    mapping_library = verify_extension(args.mapping_adapter, args.work)
    source_names = ("robot-slam/lingxi_slam/mapping_session.py",
                    "robot-slam/lingxi_slam/mapping_worker.py", "robot-runtime/python/replay_mapping.py")
    sources = {name: sha256(ROOT / name) for name in source_names}
    original_inputs = [args.work, args.adapter, args.mapping_adapter, args.bundle, args.reference]
    output = args.output.resolve()
    if any(output == entry.resolve() or output.is_relative_to(entry.resolve()) for entry in original_inputs):
        raise ValueError("new_output_outside_frozen_inputs_required")
    output.mkdir(mode=0o700)
    types = get_typestore(Stores.ROS2_HUMBLE)
    for name in ("sensor_msgs/msg/Imu", "sensor_msgs/msg/PointCloud2"):
        types.get_msgdef(name)
    calls = dict.fromkeys(("create", "imu", "frame", "close"), 0)
    timing = ReplayTiming(frozen["journal"]["records"])
    owners, loaded = set(), {}
    main_thread = threading.get_ident()

    def record(name):
        owner = threading.get_ident()
        if owner == main_thread or owners and owner not in owners:
            raise RuntimeError("one_non_main_native_owner_required")
        owners.add(owner)
        calls[name] += 1

    def verify_loaded():
        loaded.update(actual_libraries(args.work, args.adapter, frozen["nativeLibrariesSha256"]))

    def mapping_factory():
        native = MappingLibrary(mapping_library)
        loaded.update(actual_libraries(args.work, args.adapter, frozen["nativeLibrariesSha256"]))
        mapped = {Path(line.split(None, 5)[5]).resolve() for line in
                  Path("/proc/self/maps").read_text().splitlines() if len(line.split(None, 5)) == 6}
        if mapping_library.resolve() not in mapped or sha256(mapping_library) != mapping_digest:
            raise ValueError("actual_verified_mapping_extension_required")
        return native

    def factory():
        return OwnedNative(
            lambda: Native(args.adapter / "build/liblingxi_glim_offline.so", args.bundle / "config",
                           args.work / "prefix/lib/libodometry_estimation_cpu.so"),
            record, verify_loaded, timing=timing)

    def reload_map(saved):
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "reload",
                   "--work", str(args.work), "--mapping-adapter", str(args.mapping_adapter),
                   "--bundle", str(args.bundle), "--saved", str(saved)]
        descriptor = os.open(output / "private-reload.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as log:
            result = subprocess.run(command, stdout=log, stderr=log, env=os.environ.copy(),
                                    timeout=60, check=False)
        if result.returncode:
            raise RuntimeError("fresh_process_map_reload_failed")
        return json.loads((saved / "reload-report.json").read_text())

    mapping_digest = sha256(mapping_library)
    controller = MappingWorker(
        factory, mapping_factory,
        SensorProfile("lidar_chest_front", "lidar_imu_chest_front", "frame_relative_nanoseconds_hypothesis"),
        args.hypothesis, output / "saved-map", name="Recorded mapping trial", session=uuid.uuid4().hex,
        input_mode="recorded", inspect_map=inventory_map, reload_map=reload_map)
    report = {"result": "failed_or_interrupted", "inputMode": "recorded",
              "saveRequested": args.save, "sourceSha256": sources,
              "mappingLibrarySha256": mapping_digest, "inputIntegrity": frozen,
              "robotContacted": False, "rosNodeStarted": False, "motionCommandsSent": False,
              "targetArm64Validated": False, "navigationValidated": False}
    saved_stdout, saved_stderr = os.dup(1), os.dup(2)
    log_fd = os.open(output / "private-native.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    old_umask = os.umask(0o077)
    iterator = None
    try:
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        iterator = records(args.reference / "private-callbacks.cdrjournal")
        pending = timing.call("producerReadWall", next, iterator, None)
        if pending is None:
            raise ValueError("nonempty_recorded_callback_input_required")
        controller.start()
        deadline = time.monotonic() + 10
        while controller.worker.snapshot()["state"] == "starting" and time.monotonic() < deadline:
            time.sleep(.005)
        if controller.worker.snapshot()["state"] != "running":
            raise RuntimeError("mapping_worker_did_not_start")
        epoch, origin = time.monotonic(), pending.elapsed_ns
        while pending is not None:
            entry = pending
            due = epoch + (entry.elapsed_ns - origin) / 1e9 / args.rate
            while time.monotonic() < due:
                if controller.worker.snapshot()["state"] != "running":
                    raise RuntimeError("mapping_worker_failed_during_recorded_input")
                time.sleep(min(.002, max(0., due - time.monotonic())))
            timing.observe("producerDispatchLateness", time.monotonic() - due)
            message = timing.call("producerDecodeWall", types.deserialize_cdr,
                                  entry.payload, "sensor_msgs/msg/" + ("Imu" if entry.kind == "imu" else "PointCloud2"))
            offer = controller.offer_imu if entry.kind == "imu" else controller.offer_cloud
            if not timing.call("producerOfferWall", offer, message):
                raise RuntimeError("mapping_recorded_input_rejected")
            timing.observe("producerAdmissionLateness", time.monotonic() - due)
            pending = timing.call("producerReadWall", next, iterator, None)
        deadline = time.monotonic() + 1
        while (controller.worker.snapshot()["counts"]["completedMessages"] < frozen["journal"]["records"]
               and time.monotonic() < deadline):
            if controller.worker.snapshot()["state"] == "failed":
                raise RuntimeError("mapping_worker_failed_before_save")
            time.sleep(.002)
        before_stop = controller.worker.snapshot()
        if before_stop["counts"]["completedMessages"] != frozen["journal"]["records"]:
            raise ValueError("all_recorded_callbacks_must_complete_before_save")
        report["terminal"] = controller.stop(save=args.save)
        report["worker"] = controller.worker.snapshot()
        report["nativeCalls"], report["actualLoadedLibrariesSha256"] = calls, loaded
        report["nativeOwnerIsSingleNonMainThread"] = len(owners) == 1
        if (report["terminal"]["phase"] != ("saved" if args.save else "stopped")
                or report["worker"]["threadAlive"] or report["worker"]["failure"]
                or calls["create"] != 1 or calls["close"] != 1 or len(owners) != 1
                or report["worker"]["counts"]["discardedMessages"]
                or report["worker"]["counts"]["rejectedMessages"]
                or inputs(args) != frozen or sha256(mapping_library) != mapping_digest
                or {name: sha256(ROOT / name) for name in source_names} != sources):
            raise ValueError("complete_unchanged_recorded_mapping_lifecycle_required")
        receipt = controller.native_session.receipt
        if args.save:
            if receipt["counts"]["savedFrames"] != report["worker"]["stream"]["counts"]["estimatedPoses"]:
                raise ValueError("every_estimated_frame_must_be_saved_once")
            report["savedMap"] = receipt
        elif (output / "saved-map/map").exists() or (output / "saved-map/map-ready.json").exists():
            raise ValueError("plain_stop_must_not_save_a_map")
        report["result"] = "recorded_native_mapping_saved_and_reloaded" if args.save else "recorded_native_mapping_discarded"
    except BaseException as error:
        report["errorType"] = type(error).__name__
        raise
    finally:
        if iterator is not None:
            iterator.close()
        try:
            if controller.worker.snapshot()["threadAlive"]:
                controller.cancel_save()
                controller.stop()
            report["finalWorker"] = controller.worker.snapshot()
            report["nativeCalls"] = calls
            report["timing"] = timing.snapshot()
        finally:
            os.dup2(saved_stdout, 1)
            os.dup2(saved_stderr, 2)
            os.close(saved_stdout)
            os.close(saved_stderr)
            os.close(log_fd)
            os.umask(old_umask)
            private_json(output / "mapping-worker-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("run")
    for name in ("work", "adapter", "mapping-adapter", "bundle", "reference", "output"):
        replay.add_argument("--" + name, type=Path, required=True)
    replay.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
    replay.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
    replay.add_argument("--save", action="store_true")
    loader = sub.add_parser("reload")
    for name in ("work", "mapping-adapter", "bundle", "saved"):
        loader.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == "reload":
        reload_saved(args)
    else:
        result = run(args)
        print(json.dumps({"result": result["result"], "nativeCalls": result["nativeCalls"]}))


if __name__ == "__main__":
    main()
