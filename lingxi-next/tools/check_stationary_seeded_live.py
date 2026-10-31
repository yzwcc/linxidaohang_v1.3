"""Opt-in, stationary chest-LiDAR tracking check; never commands robot motion."""
import argparse
import http.client
import json
import math
import os
from pathlib import Path
import platform
import secrets
import ssl
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
from check_shared_slam_live_package import clean_owner, listening, mapping_progress, unseeded_state
from check_slam_workspace_target import saved_receipt, sha256, wait_for

RESULT = "arm64_stationary_seeded_live_checked_not_physical_accuracy"


def seed_request(preview, state, receipt, trajectory_sha256):
    """Bind the new map's verified last LiDAR pose to the current live reference."""
    unseeded_state(state)
    binding, refs = state["binding"], preview.get("seedReferences", {})
    if (binding.get("input_mode") != "live" or preview.get("mapId") != binding.get("map_id")
            or preview.get("inputMode") != "live" or receipt.get("inputMode") != "live"
            or preview.get("receiptSha256") != binding.get("receipt_sha256")
            or preview.get("pointsSha256") != binding.get("points_sha256")
            or refs.get("profileSha256") != binding.get("profile_sha256")
            or refs.get("trajectorySha256") != trajectory_sha256
            or refs.get("source") != "glim-final-lidar-trajectory"
            or refs.get("sourceFrame") != "lidar_chest_front"
            or refs.get("coordinateUnits") != "native-raw"
            or type(refs.get("frames")) is not int or refs["frames"] < 1
            or refs["frames"] != receipt.get("counts", {}).get("savedFrames")
            or refs.get("last", {}).get("frameIndex") != refs.get("frames")
            or type(state.get("referenceFrame")) is not int or state["referenceFrame"] < 1):
        raise ValueError("same_new_live_map_trajectory_and_reference_required")
    import numpy as np
    from lingxi_slam.seeded_localization import rigid_pose
    matrix = rigid_pose(np.asarray(refs["last"]["mapFromSensor"], dtype=float))
    return {"binding": binding, "expectedGeneration": state["generation"],
        "sourceFrame": "lidar_chest_front", "referenceFrame": state["referenceFrame"],
        "mapFromSensor": matrix.tolist()}


def tracking_sample(state, binding, generation, previous_frame):
    if (state.get("binding") != binding or state.get("generation") != generation
            or state.get("state") != "tracking" or state.get("requestPending") is not False
            or state.get("workerReady") is not True or state.get("workerClosed") is not False
            or state.get("localizationReady") is not False or state.get("navigationReady") is not False
            or type(state.get("lastFrame")) is not int or state["lastFrame"] < previous_frame
            or type(state.get("consecutiveMatches")) is not int or state["consecutiveMatches"] < 3):
        raise RuntimeError("same_generation_continuous_diagnostic_tracking_required")
    import numpy as np
    from lingxi_slam.seeded_localization import rigid_pose
    return rigid_pose(np.asarray(state["mapFromSensor"], dtype=float))


def spread(poses):
    import numpy as np
    if len(poses) < 2:
        raise ValueError("distinct_tracking_frames_required")
    origin = poses[0]
    translations = [float(np.linalg.norm(pose[:3, 3] - origin[:3, 3])) for pose in poses]
    angles = [math.degrees(math.acos(float(np.clip(
        (np.trace(origin[:3, :3].T @ pose[:3, :3]) - 1) / 2, -1, 1)))) for pose in poses]
    return {"maxTranslationFromFirstRaw": max(translations),
            "maxRotationFromFirstDegrees": max(angles), "physicalAccuracyMeasured": False}


def check(args):
    import live_bundle
    from controlled_localization import verified_configuration
    from mapping_platform import MappingPlatform
    from mapping_service import private_json

    if not args.stationary_confirmed:
        raise ValueError("explicit_stationary_and_unchanged_chest_confirmation_required")
    config, _ = verified_configuration(args.config, allow_live=True)
    if (sys.platform != "linux" or platform.machine() != "aarch64" or os.geteuid() == 0
            or config["inputMode"] != "live" or not 1 <= args.port <= 65535 or args.port == 3000
            or not 5 <= args.seconds <= 20
            or any(not path.is_absolute() or path.resolve() != path for path in
                   (args.bundle, args.config, args.output))
            or args.output.exists() or args.output.is_symlink()
            or any(a == b or a in b.parents or b in a.parents for a, b in
                   ((args.output, args.bundle), (args.output, args.config)))
            or len(os.fsencode(args.output / "r" / ("a" * 32) / "localization.sock")) >= 100):
        raise ValueError("new_short_private_stationary_live_trial_required")
    live_bundle.verify_bundle(args.bundle, args.manifest_sha256,
        project=args.bundle / "source", profile=live_bundle.SLAM)
    native = MappingPlatform(config)
    frozen, config_hash = native.verify(), sha256(args.config)
    args.output.mkdir(mode=0o700)
    maps = args.output / "maps"
    maps.mkdir(mode=0o700)
    key, cert = args.output / "key.pem", args.output / "cert.pem"
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=Stationary tracking check",
        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, timeout=20,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    code = secrets.token_hex(24)
    environment = {**os.environ, "LINGXI_HOST": "127.0.0.1", "LINGXI_PORT": str(args.port),
        "LINGXI_TLS_KEY": str(key), "LINGXI_TLS_CERT": str(cert),
        "LINGXI_PAIRING_CODE_SHA256": sha256_bytes(code.encode())}
    owner_output = args.output / "o"
    report = {"result": "failed", "robotContacted": True, "operatorConfirmedStationary": True,
        "motionCommandsSent": False, "formalServicesChanged": False,
        "localizationReady": False, "navigationReady": False, "physicalAccuracyMeasured": False,
        "manifestSha256": args.manifest_sha256, "configSha256": config_hash, "seedRequests": 0}
    process = connection = None
    token = None

    def request(route, *, method="GET", body=None, expected=200):
        headers = {"authorization": "Bearer " + token} if token else {}
        if body is not None:
            headers["content-type"] = "application/json"
        connection.request(method, "/api/v1/" + route,
            None if body is None else json.dumps(body, allow_nan=False), headers)
        response = connection.getresponse()
        data = response.read(4 * 1024**2 + 1)
        if response.status != expected or len(data) > 4 * 1024**2:
            raise RuntimeError("expected_bounded_stationary_response_required:" + route)
        return json.loads(data) if data else None

    try:
        command = [sys.executable, "-B", str(args.bundle / "source/robot-runtime/python/launch_slam_bundle.py"),
            "--bundle", str(args.bundle), "--manifest-sha256", args.manifest_sha256,
            "--config", str(args.config), "--maps", str(maps), "--output", str(owner_output), "--seconds", "180"]
        with (args.output / "private-launcher.log").open("xb") as log:
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=log, close_fds=True, start_new_session=True)
        listening(process, args.port)
        connection = http.client.HTTPSConnection("127.0.0.1", args.port,
            context=ssl.create_default_context(cafile=str(cert)), timeout=100.)
        request("state", expected=401)
        token = request("session/pair", method="POST", body={"pairingCode": code}, expected=201)["token"]
        request("mapping/start", method="POST", body={}, expected=202)
        state = wait_for(lambda: (value if (value := request("state")).get("mappingSession") else None))
        session, beginning = state["mappingSession"], time.monotonic()
        state = wait_for(lambda: (value if mapping_progress(value := request("state"), session,
            time.monotonic() - beginning) else None), seconds=40)
        saved = request("mapping/stop-save", method="POST", body={"session": session,
            "name": "Stationary seeded live check"})["savedSlamMap"]
        preview = request("slam/maps/" + saved["id"] + "/points")
        receipt_file, receipt = saved_receipt(maps, saved["id"], preview["receiptSha256"])
        if receipt.get("schemaVersion") != 2 or receipt.get("freshProcessReloadPassed") is not True:
            raise RuntimeError("freshly_saved_live_map_required")
        report.update(receiptSha256=sha256(receipt_file), savedCounts=receipt["counts"])
        runtime = request("slam/localization/runtime")
        request("slam/localization/start", method="POST", expected=202, body={
            "serviceId": runtime["serviceId"], "expectedGeneration": runtime["generation"],
            "mapId": saved["id"], "receiptSha256": preview["receiptSha256"]})
        runtime = wait_for(lambda: (value if (value := request("slam/localization/runtime"))["phase"] == "running" else None))
        state = wait_for(lambda: (value if (value := request("slam/localization"))["referenceFrame"] is not None else None))
        seed = seed_request(preview, state, receipt, sha256(receipt_file.parent / "map/traj_lidar.txt"))
        report.update(seedRequests=1, seedSource="new_stationary_map_last_lidar_pose",
            trajectorySha256=preview["seedReferences"]["trajectorySha256"])
        request("slam/localization/seed", method="POST", body=seed, expected=202)
        started = time.monotonic()

        def acquiring():
            value = request("slam/localization")
            if value["state"] in ("lost", "failed", "stopped", "paused"):
                report["rejectedState"] = value
                raise RuntimeError("seeded_live_acquisition_rejected:" + value["reason"])
            return value if value["state"] == "tracking" else None

        state = wait_for(acquiring, seconds=20)
        report["firstTrackingSecondsAfterSubmission"] = time.monotonic() - started
        binding, generation, previous = state["binding"], state["generation"], 0
        if binding != seed["binding"] or generation != seed["expectedGeneration"] + 1:
            raise RuntimeError("same_acknowledged_seed_generation_required")
        poses, samples, beginning = [], [], time.monotonic()
        while True:
            pose = tracking_sample(state, binding, generation, previous)
            if state["lastFrame"] > previous:
                poses.append(pose)
                samples.append({"elapsedSeconds": time.monotonic() - beginning, "state": state})
                previous = state["lastFrame"]
            if time.monotonic() - beginning >= args.seconds:
                break
            time.sleep(.1)
            state = request("slam/localization")
        if len(poses) < 20:
            raise RuntimeError("twenty_distinct_live_tracking_frames_required")
        private_json(args.output / "private-tracking-samples.json", samples)
        report["tracking"] = {"distinctFrames": len(poses), "firstFrame": samples[0]["state"]["lastFrame"],
            "lastFrame": previous, "observedSeconds": samples[-1]["elapsedSeconds"], **spread(poses)}
        paused = request("slam/localization/pause", method="POST", expected=202,
            body={"binding": binding, "expectedGeneration": generation})
        if paused["state"] != "paused" or paused["mapFromSensor"] is not None:
            raise RuntimeError("immediate_seeded_pause_revocation_required")
        request("slam/localization/seed", method="POST", body=seed, expected=409)
        stopped = request("slam/localization/stop", method="POST", expected=202,
            body={"binding": binding, "expectedGeneration": paused["generation"]})
        if stopped["state"] != "stopped" or stopped["mapFromSensor"] is not None:
            raise RuntimeError("immediate_seeded_stop_revocation_required")
        for _ in range(10):
            value = request("slam/localization")
            if value["state"] != "stopped" or value["mapFromSensor"] is not None:
                raise RuntimeError("seeded_stopped_session_revived")
            time.sleep(.05)
        request("slam/localization/shutdown", method="POST", expected=202, body={
            "serviceId": runtime["serviceId"], "expectedGeneration": runtime["generation"], "runId": runtime["runId"]})
        terminal = wait_for(lambda: (value if (value := request("slam/localization/runtime"))["cleanupComplete"] else None))
        if terminal["phase"] != "stopped":
            raise RuntimeError("clean_stationary_localization_shutdown_required")
        child_file = owner_output / "r" / runtime["runId"] / "child-report.json"
        child = json.loads(child_file.read_bytes())
        if child["result"] != "live_localization_runtime_closed" or child["failures"]:
            raise RuntimeError("complete_stationary_native_report_required")
        report.update(pauseConfirmed=True, stopConfirmed=True, staleSeedRejected=True, stoppedQueries=10,
            childReportSha256=sha256(child_file), localizationCounts=child["worker"]["stream"]["counts"],
            nativeCalls=child["nativeCalls"], basePoseInput=child.get("basePoseInput"))
        if native.verify() != frozen or sha256(args.config) != config_hash:
            raise RuntimeError("unchanged_stationary_trial_inputs_required")
        live_bundle.verify_bundle(args.bundle, args.manifest_sha256,
            project=args.bundle / "source", profile=live_bundle.SLAM)
        report["result"] = RESULT
    except BaseException as error:
        report.update(errorType=type(error).__name__, error=str(error))
        raise
    finally:
        try:
            if connection:
                connection.close()
            if process:
                if process.poll() is None:
                    process.terminate()
                try:
                    status = process.wait(timeout=125)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    raise RuntimeError("stationary_launcher_forced_termination")
                finally:
                    process.stdout.close()
                if status != 0:
                    raise RuntimeError("clean_stationary_launcher_exit_required")
                owner_file = owner_output / "owner-report.json"
                owner = json.loads(owner_file.read_bytes())
                clean_owner(owner)
                report.update(allProcessesCleaned=True, ownerReportSha256=sha256(owner_file))
        except BaseException as error:
            report.update(result="failed", cleanupErrorType=type(error).__name__)
            raise
        finally:
            private_json(args.output / "check-report.json", report)
    return report


def sha256_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "config", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=10.)
    parser.add_argument("--stationary-confirmed", action="store_true")
    os.umask(0o077)
    print(json.dumps({"result": check(parser.parse_args())["result"]}), flush=True)


if __name__ == "__main__":
    main()
