"""Opt-in recorded-input commissioning of an extracted package on the actual ARM64 target."""
import argparse
import hashlib
from html.parser import HTMLParser
import http.client
import json
import math
import os
from pathlib import Path
import platform
import ssl
import subprocess
import sys
import time

SERVICE = "lingxi-next-slam.service"


def sha256(file):
    result = hashlib.sha256()
    with file.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def wait_for(callback, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = callback()
        if value:
            return value
        time.sleep(.1)
    raise TimeoutError("recorded_workspace_observation_deadline")


def saved_receipt(maps, map_id, expected_sha256):
    matches = []
    for index, directory in enumerate(maps.iterdir()):
        if index >= 2048:
            raise ValueError("bounded_recorded_map_catalog_required")
        if directory.is_symlink() or not directory.is_dir():
            continue
        receipt = directory / "map-ready.json"
        if (receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 16384
                or sha256(receipt) != expected_sha256):
            continue
        value = json.loads(receipt.read_text())
        if map_id == "slam-" + str(value.get("session")):
            matches.append((receipt, value))
    if len(matches) != 1:
        raise ValueError("one_identity_and_digest_bound_saved_receipt_required")
    return matches[0]


def recorded_input(config, messages, scans):
    from slam_callback_journal import records
    rate = config.get("rate")
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError("explicit_recorded_playback_rate_required")
    counts, first, last = {"imu": 0, "cloud": 0}, None, None
    for entry in records(Path(config["reference"]) / "private-callbacks.cdrjournal"):
        counts[entry.kind] += 1
        if first is None:
            first = entry.elapsed_ns
        last = entry.elapsed_ns
    if sum(counts.values()) != messages or counts["cloud"] < scans or first is None:
        raise ValueError("same_complete_recorded_input_counts_required")
    return {"seconds": (last - first) / 1e9 / rate, "journal": {"records": messages, "counts": counts}}


def inputs(args):
    if (sys.platform != "linux" or platform.machine() != "aarch64" or os.geteuid() == 0
            or sys.version_info[:2] != (3, 10) or args.root.exists() or args.root.is_symlink()
            or args.output.exists() or args.output.is_symlink()
            or any(not path.is_absolute() or path.resolve() != path for path in (
                args.bundle, args.root, args.output, args.config, args.seed_sample))
            or json.loads(args.config.read_text()).get("inputMode") != "recorded"
            or args.messages <= 0 or args.scans <= 0 or sha256(args.seed_sample) != args.seed_sha256):
        raise ValueError("new_ordinary_arm64_recorded_workspace_trial_required")
    paths = (args.bundle, args.root, args.output)
    if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise ValueError("separate_package_installation_and_trial_output_required")


def check(args):
    inputs(args)
    sys.path[:0] = [str(args.bundle / "source/robot-runtime/python"), str(args.bundle / "source/tools"),
                   str(args.bundle / "source/robot-slam")]
    import live_bundle
    import run_glim_supervised_replay as replay
    import numpy as np
    live_bundle.verify_bundle(args.bundle, args.manifest_sha256,
                              project=args.bundle / "source", profile=live_bundle.SLAM)
    with np.load(args.seed_sample, allow_pickle=False) as sample:
        seed = sample["world_lidar_pose"].tolist()
    recorded = recorded_input(json.loads(args.config.read_text()), args.messages, args.scans)
    args.output.mkdir(mode=0o700)
    report = {"result": "failed", "machine": platform.machine(), "python": platform.python_version(),
              "probeSha256": sha256(Path(__file__)), "manifestSha256": args.manifest_sha256,
              "configSha256": sha256(args.config), "seedSha256": args.seed_sha256,
              "inputMode": "recorded", "motionCommandsSent": False, "sensorsSubscribed": False,
              "recordedInputSeconds": recorded["seconds"],
              "vendorServicesChanged": False, "mappingReady": False, "localizationReady": False,
              "navigationReady": False, "serviceStopped": False}
    attempted, connection = False, None

    def command(arguments, timeout=310):
        with (args.output / "private-commands.log").open("ab") as log:
            subprocess.run([str(value) for value in arguments], stdout=log, stderr=log,
                           timeout=timeout, check=True)

    def systemctl(action):
        command(["/usr/bin/systemctl", "--user", action, SERVICE])

    def connect():
        nonlocal connection, token
        if connection:
            connection.close()
        connection = http.client.HTTPSConnection("127.0.0.1", 3443,
            context=ssl.create_default_context(cafile=str(args.output / "cert.pem")), timeout=130)
        token = None
        request("state", expected=401)
        token = request("session/pair", method="POST", body={
            "pairingCode": (args.root / "pairing-code.txt").read_text().strip()}, expected=201)["token"]

    token = None

    def request(route, method="GET", body=None, expected=200):
        headers = {"authorization": "Bearer " + token} if token else {}
        if body is not None:
            headers["content-type"] = "application/json"
        connection.request(method, "/api/v1/" + route,
            body=None if body is None else json.dumps(body), headers=headers)
        response = connection.getresponse()
        data = response.read(4 * 1024**2 + 1)
        if response.status != expected or len(data) > 4 * 1024**2:
            raise RuntimeError("expected_bounded_workspace_response_required:" + route)
        return json.loads(data) if data else None

    def run_reports():
        values = []
        for folder in sorted((args.root / "runs").iterdir()):
            owner = json.loads((folder / "owner-report.json").read_text())
            if (owner["result"] != "stopped" or owner["remainingChildren"] != 0
                    or not owner["adoptedChildren"]["cleanupComplete"]
                    or owner["adoptedChildren"]["forcedTermination"]):
                raise RuntimeError("complete_workspace_owner_cleanup_required")
            values.append({"sha256": sha256(folder / "owner-report.json"), "report": owner})
        return values

    try:
        load = subprocess.check_output(["/usr/bin/systemctl", "--user", "show", SERVICE,
            "--property=LoadState", "--value"], text=True, timeout=10).strip()
        if load != "not-found":
            raise ValueError("new_unmanaged_workspace_service_required")
        command(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", args.output / "key.pem", "-out", args.output / "cert.pem", "-days", "1",
                 "-subj", "/CN=Recorded ARM64 workspace commissioning", "-addext", "subjectAltName=IP:127.0.0.1"], 20)
        attempted = True
        command(["/usr/bin/python3", "-I", "-B", args.bundle / "source/robot-runtime/python/slam_deploy.py",
            "install", "--root", args.root, "--bundle", args.bundle, "--manifest-sha256", args.manifest_sha256,
            "--config", args.config, "--python", sys.executable, "--tls-key", args.output / "key.pem",
            "--tls-cert", args.output / "cert.pem", "--host", "127.0.0.1", "--port", "3443", "--start"])
        connect()
        state = request("state")
        if not state["slamWorkspace"]:
            raise RuntimeError("shared_workspace_required")
        connection.request("GET", "/")
        response = connection.getresponse()
        index = response.read(1024 * 1024).decode()
        if response.status != 200:
            raise RuntimeError("packaged_index_required")
        scripts = []

        class Index(HTMLParser):
            def handle_starttag(self, tag, attrs):
                value = dict(attrs)
                if tag == "script" and value.get("src"):
                    scripts.append(value["src"])

        Index().feed(index)
        if not scripts:
            raise RuntimeError("packaged_scripts_required")
        for source in scripts:
            if not source.startswith("/assets/"):
                raise ValueError("local_packaged_script_required")
            connection.request("GET", source)
            response = connection.getresponse()
            if response.status != 200 or len(response.read()) < 100:
                raise RuntimeError("packaged_script_response_required")
        report["staticScriptsChecked"] = len(scripts)
        request("mapping/start", method="POST", body={}, expected=202)
        state = wait_for(lambda: (value if (value := request("state")).get("mappingProgress", {}).get(
            "completedMessages") == args.messages else None))
        if state["mappingProgress"]["processedScans"] != args.scans:
            raise RuntimeError("complete_recorded_mapping_scans_required")
        report["mappingProgress"] = state["mappingProgress"]
        saved = request("mapping/stop-save", method="POST", body={
            "session": state["mappingSession"], "name": "Recorded ARM64 workspace check"})["savedSlamMap"]
        map_id = saved["id"]
        preview = request("slam/maps/" + map_id + "/points")
        receipt, value = saved_receipt(args.root / "maps", map_id, preview["receiptSha256"])
        if value["schemaVersion"] != 2 or sha256(receipt) != preview["receiptSha256"]:
            raise RuntimeError("verified_native_v2_saved_map_required")
        report.update(receiptSha256=sha256(receipt), savedCounts=value["counts"], savedMapId=map_id)
        systemctl("stop")
        report["firstOwnerReports"] = run_reports()
        pairing_hash = sha256(args.root / "pairing-code.txt")
        systemctl("start")
        connect()
        if [item["id"] for item in request("slam/maps")["maps"]] != [map_id]:
            raise RuntimeError("persistent_map_catalog_required")
        report["restartPreservedMapAndPairing"] = sha256(args.root / "pairing-code.txt") == pairing_hash
        runtime = request("slam/localization/runtime")
        request("slam/localization/start", method="POST", expected=202, body={
            "serviceId": runtime["serviceId"], "expectedGeneration": runtime["generation"],
            "mapId": map_id, "receiptSha256": preview["receiptSha256"]})
        runtime = wait_for(lambda: (value if (value := request("slam/localization/runtime"))["phase"] == "running" else None))
        replay_deadline = time.monotonic() + recorded["seconds"] + .25
        request("mapping/start", method="POST", body={}, expected=409)
        state = wait_for(lambda: (value if (value := request("slam/localization"))["referenceFrame"] is not None else None))
        old_seed = {"binding": state["binding"], "expectedGeneration": state["generation"],
                    "sourceFrame": state["sourceFrame"], "referenceFrame": state["referenceFrame"], "mapFromSensor": seed}
        request("slam/localization/seed", method="POST", body=old_seed, expected=202)
        state = wait_for(lambda: (value if (value := request("slam/localization"))["state"] == "tracking" else None))
        report["trackingFrameObserved"] = state["lastFrame"]
        state = request("slam/localization/pause", method="POST", expected=202,
            body={"binding": state["binding"], "expectedGeneration": state["generation"]})
        if state["state"] != "paused" or state["mapFromSensor"] is not None:
            raise RuntimeError("immediate_pause_revocation_required")
        request("slam/localization/seed", method="POST", body=old_seed, expected=409)
        # Full-journal diagnostics intentionally classify an early stop as interrupted.
        def playback_elapsed():
            value = request("slam/localization")
            return value if time.monotonic() >= replay_deadline else None
        wait_for(playback_elapsed)
        request("slam/localization/shutdown", method="POST", expected=202, body={
            "serviceId": runtime["serviceId"], "expectedGeneration": runtime["generation"], "runId": runtime["runId"]})
        terminal = wait_for(lambda: (value if (value := request("slam/localization/runtime"))["cleanupComplete"] else None))
        if terminal["phase"] != "stopped":
            raise RuntimeError("clean_localization_service_shutdown_required")
        child_files = list((args.root / "runs").glob("*/r/" + runtime["runId"] + "/child-report.json"))
        if len(child_files) != 1:
            raise RuntimeError("one_recorded_localization_report_required")
        child = json.loads(child_files[0].read_text())
        replay.complete_child(child, recorded["journal"])
        if child["result"] != replay.CHILD_RESULT or child["session"] != state["binding"]["session"]:
            raise RuntimeError("complete_same_session_localization_report_required")
        report.update(localizationReportSha256=sha256(child_files[0]),
            localizationProgress={"completedMessages": child["worker"]["counts"]["completedMessages"],
                "processedScans": child["worker"]["stream"]["counts"]["processedScans"]})
        report.update(pauseConfirmed=True, staleGenerationRejected=True, localizationShutdownConfirmed=True)
        request("mapping/start", method="POST", body={}, expected=202)
        state = wait_for(lambda: (value if (value := request("state"))["mappingStatus"] == "running" else None))
        request("mapping/stop-discard", method="POST", body={"session": state["mappingSession"]})
        systemctl("stop")
        report["ownerReports"] = run_reports()
        report["result"] = "arm64_recorded_shared_package_checked_not_physical_accuracy"
    except BaseException as error:
        report["errorType"] = type(error).__name__
        report["error"] = str(error)
        raise
    finally:
        if connection:
            connection.close()
        try:
            if attempted:
                systemctl("stop")
                active = subprocess.run(["/usr/bin/systemctl", "--user", "is-active", SERVICE],
                                        capture_output=True, text=True, timeout=10, check=False)
                report["serviceStopped"] = active.stdout.strip() == "inactive"
        finally:
            (args.output / "check-report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    if not report["serviceStopped"]:
        raise RuntimeError("final_service_stop_required")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "root", "output", "config", "seed-sample"):
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ("manifest-sha256", "seed-sha256"):
        parser.add_argument("--" + name, required=True)
    for name in ("messages", "scans"):
        parser.add_argument("--" + name, required=True, type=int)
    os.umask(0o077)
    print(json.dumps({"result": check(parser.parse_args())["result"]}), flush=True)


if __name__ == "__main__":
    main()
