"""Opt-in passive live commissioning of one extracted shared ARM64 SLAM package."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import secrets
import selectors
import ssl
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
from check_slam_workspace_target import saved_receipt, sha256, wait_for

RESULT = "arm64_live_shared_package_checked_not_physical_accuracy"
MAX_RESPONSE = 4 * 1024**2


def clean_owner(report):
    if (report.get("result") != "stopped" or report.get("remainingChildren") != 0
            or report.get("adoptedChildren", {}).get("cleanupComplete") is not True
            or report["adoptedChildren"].get("forcedTermination") is not False):
        raise RuntimeError("clean_shared_owner_required")
    for name in ("serviceProcess", "apiProcess"):
        child = report.get(name) or {}
        if (type(child.get("exitCode")) is not int or child["exitCode"] != 0
                or any(child.get(key) is not False for key in
                       ("forcedTermination", "processAlive", "groupAlive"))):
            raise RuntimeError("clean_shared_child_required")


def mapping_progress(state, session, elapsed):
    if state.get("mappingSession") != session or state.get("mappingStatus") == "failed":
        raise RuntimeError("same_healthy_shared_mapping_session_required")
    progress = state.get("mappingProgress") or {}
    return (state.get("mappingStatus") == "running" and elapsed >= 10.
            and type(progress.get("processedScans")) is int and progress["processedScans"] >= 120
            and type(progress.get("estimatedPoses")) is int and progress["estimatedPoses"] >= 60)


def unseeded_state(state):
    if (state.get("state") != "unseeded" or state.get("mapFromSensor") is not None
            or state.get("localizationReady") is not False or state.get("navigationReady") is not False):
        raise RuntimeError("unseeded_live_pose_must_remain_unavailable")


def listening(process, port):
    pending, deadline = bytearray(), time.monotonic() + 45.
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while process.poll() is None and time.monotonic() < deadline:
            if not selector.select(.05):
                continue
            block = os.read(process.stdout.fileno(), 4097)
            if not block:
                break
            pending.extend(block)
            if len(pending) > 4096:
                raise ValueError("bounded_shared_owner_startup_required")
            if b"\n" in pending:
                if json.loads(pending) != {"service": "lingxi-next", "event": "listening", "port": port}:
                    raise ValueError("exact_shared_owner_startup_required")
                return
    raise RuntimeError("shared_owner_not_listening")


def check(args):
    import live_bundle
    from controlled_localization import source_hashes, verified_configuration
    from check_arm64_python_runtime import check as check_python
    from mapping_platform import MappingPlatform
    from mapping_service import private_json

    config, _ = verified_configuration(args.config, allow_live=True)
    if (sys.platform != "linux" or platform.machine() != "aarch64" or os.geteuid() == 0
            or config["inputMode"] != "live" or not 1 <= args.port <= 65535 or args.port == 3000
            or any(not path.is_absolute() or path.resolve() != path for path in
                   (args.bundle, args.config, args.output))
            or args.output.exists() or args.output.is_symlink()
            or any(a == b or a in b.parents or b in a.parents for a, b in
                   ((args.output, args.bundle), (args.output, args.config)))
            or len(os.fsencode(args.output / "maps" / ("a" * 32 + ".points.sock"))) >= 100
            or len(os.fsencode(args.output / "b/r" / ("a" * 32) / "localization.sock")) >= 100):
        raise ValueError("new_short_private_arm64_live_shared_trial_required")
    numerical = check_python()
    live_bundle.verify_bundle(args.bundle, args.manifest_sha256,
                              project=args.bundle / "source", profile=live_bundle.SLAM)
    native = MappingPlatform(config)
    frozen, config_hash = native.verify(), sha256(args.config)
    args.output.mkdir(mode=0o700)
    maps = args.output / "maps"
    maps.mkdir(mode=0o700)
    key, cert = args.output / "key.pem", args.output / "cert.pem"
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=Live shared package check",
        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, timeout=20,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    code = secrets.token_hex(24)
    environment = {**os.environ, "LINGXI_HOST": "127.0.0.1", "LINGXI_PORT": str(args.port),
        "LINGXI_TLS_KEY": str(key), "LINGXI_TLS_CERT": str(cert),
        "LINGXI_PAIRING_CODE_SHA256": hashlib.sha256(code.encode()).hexdigest()}
    report = {"schemaVersion": 1, "result": "failed", "manifestSha256": args.manifest_sha256,
        "configSha256": config_hash, "checkerSha256": sha256(Path(__file__)), "numerical": numerical,
        "motionCommandsSent": False, "formalServicesChanged": False, "vendorPackagesChanged": False,
        "physicalCalibrationAccepted": False, "localizationReady": False, "navigationReady": False,
        "productAccepted": False, "seedRequests": 0, "ownerReports": []}
    process = connection = None
    token, current = None, None

    def request(route, *, body=None, method="GET", expected=200, asset=False):
        headers = {"authorization": "Bearer " + token} if token else {}
        if body is not None:
            headers["content-type"] = "application/json"
        connection.request(method, route if asset else "/api/v1/" + route,
            None if body is None else json.dumps(body, allow_nan=False), headers)
        response = connection.getresponse()
        data = response.read(MAX_RESPONSE + 1)
        if response.status != expected or len(data) > MAX_RESPONSE:
            raise RuntimeError("expected_bounded_shared_response_required:" + route)
        return data if asset else json.loads(data) if data else None

    def start(name):
        nonlocal process, connection, token, current
        current = args.output / name
        command = [sys.executable, "-B", str(args.bundle / "source/robot-runtime/python/launch_slam_bundle.py"),
            "--bundle", str(args.bundle), "--manifest-sha256", args.manifest_sha256,
            "--config", str(args.config), "--maps", str(maps), "--output", str(current), "--seconds", "240"]
        with (args.output / ("private-" + name + ".log")).open("xb") as log:
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=log, close_fds=True, start_new_session=True)
        listening(process, args.port)
        connection = http.client.HTTPSConnection("127.0.0.1", args.port,
            context=ssl.create_default_context(cafile=str(cert)), timeout=130.)
        token = None
        request("state", expected=401)
        token = request("session/pair", method="POST", body={"pairingCode": code}, expected=201)["token"]
        if request("state").get("slamWorkspace") is not True:
            raise RuntimeError("shared_workspace_required")

    def stop():
        nonlocal process, connection
        if connection is not None:
            connection.close()
            connection = None
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                status = process.wait(timeout=125)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError("shared_launcher_forced_termination")
            finally:
                process.stdout.close()
            if status != 0:
                raise RuntimeError("clean_shared_launcher_exit_required")
            owner_file = current / "owner-report.json"
            owner = json.loads(owner_file.read_bytes())
            clean_owner(owner)
            report["ownerReports"].append({"sha256": sha256(owner_file), "report": owner})
            process = None

    try:
        start("a")
        from live_bundle import AssetReferences
        assets = AssetReferences()
        assets.feed(request("/", asset=True).decode())
        if not assets.references:
            raise RuntimeError("packaged_asset_references_required")
        for asset in assets.references:
            if not asset.startswith("/assets/") or len(request(asset, asset=True)) < 100:
                raise RuntimeError("packaged_local_assets_required")
        report["staticAssetsChecked"] = len(assets.references)
        request("mapping/start", method="POST", body={}, expected=202)
        state = wait_for(lambda: (value if (value := request("state")).get("mappingSession") else None))
        session, beginning = state["mappingSession"], time.monotonic()
        state = wait_for(lambda: (value if mapping_progress(value := request("state"), session,
            time.monotonic() - beginning) else None), seconds=40)
        report["mappingProgress"] = state["mappingProgress"]
        saved = request("mapping/stop-save", method="POST", body={"session": session,
            "name": "Live shared package check"})["savedSlamMap"]
        map_id = saved["id"]
        preview = request("slam/maps/" + map_id + "/points")
        receipt_file, receipt = saved_receipt(maps, map_id, preview["receiptSha256"])
        if (receipt.get("schemaVersion") != 2 or receipt.get("inputMode") != "live"
                or receipt.get("freshProcessReloadPassed") is not True or receipt["counts"]["points"] < 1):
            raise RuntimeError("fresh_process_reloaded_live_map_required")
        report.update(savedCounts=receipt["counts"], receiptSha256=sha256(receipt_file))
        stop()
        start("b")
        if [value["id"] for value in request("slam/maps")["maps"]] != [map_id]:
            raise RuntimeError("restart_preserved_map_catalog_required")
        report["restartPreservedMap"] = True
        if request("slam/maps/" + map_id + "/points")["receiptSha256"] != report["receiptSha256"]:
            raise RuntimeError("restart_preserved_receipt_required")
        request("mapping/start", method="POST", body={}, expected=202)
        state = wait_for(lambda: (value if (value := request("state"))["mappingStatus"] == "running" else None))
        runtime = request("slam/localization/runtime")
        request("slam/localization/start", method="POST", expected=409, body={
            "serviceId": runtime["serviceId"], "expectedGeneration": runtime["generation"],
            "mapId": map_id, "receiptSha256": report["receiptSha256"]})
        request("mapping/stop-discard", method="POST", body={"session": state["mappingSession"]})
        runtime = request("slam/localization/runtime")
        request("slam/localization/start", method="POST", expected=202, body={
            "serviceId": runtime["serviceId"], "expectedGeneration": runtime["generation"],
            "mapId": map_id, "receiptSha256": report["receiptSha256"]})
        runtime = wait_for(lambda: (value if (value := request("slam/localization/runtime"))["phase"] == "running" else None))
        request("mapping/start", method="POST", body={}, expected=409)
        references = []
        deadline = time.monotonic() + 20.
        while len(references) < 5 and time.monotonic() < deadline:
            state = request("slam/localization")
            unseeded_state(state)
            frame = state.get("referenceFrame")
            if type(frame) is int and (not references or frame > references[-1]):
                references.append(frame)
            time.sleep(.05)
        if len(references) < 5:
            raise RuntimeError("five_live_shared_reference_frames_required")
        fields = {"binding": state["binding"], "expectedGeneration": state["generation"]}
        paused = request("slam/localization/pause", method="POST", body=fields, expected=202)
        if paused["state"] != "paused" or paused["mapFromSensor"] is not None:
            raise RuntimeError("immediate_shared_pause_required")
        request("slam/localization/stop", method="POST", body=fields, expected=409)
        stopped = request("slam/localization/stop", method="POST", expected=202,
            body={"binding": paused["binding"], "expectedGeneration": paused["generation"]})
        if stopped["state"] != "stopped" or stopped["mapFromSensor"] is not None:
            raise RuntimeError("immediate_shared_stop_required")
        for _ in range(10):
            if request("slam/localization")["state"] != "stopped":
                raise RuntimeError("stopped_shared_session_revived")
            time.sleep(.05)
        request("slam/localization/shutdown", method="POST", expected=202, body={
            "serviceId": runtime["serviceId"], "expectedGeneration": runtime["generation"], "runId": runtime["runId"]})
        terminal = wait_for(lambda: (value if (value := request("slam/localization/runtime"))["cleanupComplete"] else None))
        if terminal["phase"] != "stopped":
            raise RuntimeError("clean_shared_localization_shutdown_required")
        child_output = current / "r" / runtime["runId"]
        child = json.loads((child_output / "child-report.json").read_bytes())
        final = json.loads((child_output / "localization-final.json").read_bytes())
        from localization_control import load_map
        from live_localization import verify_completion
        from check_managed_live_localization import verify_base_input
        binding, _ = load_map(receipt_file.parent, map_id, report["receiptSha256"], native.profile,
            sha256(native.profile), session=state["binding"]["session"], input_mode="live",
            sensor_provenance=native.sensor_provenance())
        verify_completion(child, final, frozen=frozen, binding=binding, sources=source_hashes(),
            config_hash=config_hash, base_scale=config.get("basePose", {}).get("metresPerMapUnit"))
        if "basePose" in config:
            verify_base_input(child["basePoseInput"])
        report["localization"] = {"referenceFramesObserved": references, "pauseConfirmed": True,
            "stopConfirmed": True, "staleGenerationRejected": True, "stoppedQueries": 10,
            "counts": child["worker"]["stream"]["counts"], "nativeCalls": child["nativeCalls"],
            "childReportSha256": sha256(child_output / "child-report.json"),
            "basePoseInput": child.get("basePoseInput"), "runtime": terminal}
        request("mapping/start", method="POST", body={}, expected=202)
        state = wait_for(lambda: (value if (value := request("state"))["mappingStatus"] == "running" else None))
        request("mapping/stop-discard", method="POST", body={"session": state["mappingSession"]})
        stop()
        if native.verify() != frozen or sha256(args.config) != config_hash:
            raise RuntimeError("unchanged_shared_trial_inputs_required")
        live_bundle.verify_bundle(args.bundle, args.manifest_sha256,
            project=args.bundle / "source", profile=live_bundle.SLAM)
        report.update(result=RESULT, mappingLocalizationExclusionChecked=True,
                      mappingAdmissionRestored=True, allProcessesCleaned=True)
    except BaseException as error:
        report["errorType"] = type(error).__name__
        raise
    finally:
        try:
            stop()
        except BaseException as error:
            report.update(result="failed", cleanupErrorType=type(error).__name__)
            raise
        finally:
            private_json(args.output / "check-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "config", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--port", type=int, required=True)
    os.umask(0o077)
    print(json.dumps({"result": check(parser.parse_args())["result"]}), flush=True)


if __name__ == "__main__":
    main()
