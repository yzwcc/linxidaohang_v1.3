"""Check passive live references and terminal revocation; never submit a seed."""
import argparse
import http.client
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "tools")]
from check_managed_localization_runtime import listening
from controlled_localization import FIELDS, source_hashes, verified_configuration
from localization_control import digest, direct, strict_json
from mapping_service import private_json


def verify_base_input(inputs):
    observations = inputs.get("observations", {})
    positive = ("imuMessages", "cloudMessages", "staticTransforms", "dynamicTransforms",
                "timedFrames", "tfLookupsAvailable")
    zero = ("invalidScanTimes", "invalidTransforms", "lookupErrors", "scanTimeUnavailable")
    if (inputs.get("closed") is not True or inputs.get("pendingScanTimes") != 0
            or inputs.get("retainedDynamicTransforms") != 0
            or any(inputs.get(name) is not False for name in ("physicalScaleValidated", "navigationReady"))
            or any(type(observations.get(name)) is not int or observations[name] < 1 for name in positive)
            or any(type(observations.get(name)) is not int or observations[name] != 0 for name in zero)
            or type(observations.get("tfLookupsUnavailable")) is not int
            or observations["tfLookupsUnavailable"] < 0
            or observations["timedFrames"] != observations["tfLookupsAvailable"]
                + observations["tfLookupsUnavailable"]):
        raise ValueError("observed_live_timed_tf_and_closed_inputs_required")


def check(args):
    config, _ = verified_configuration(args.config, allow_live=True)
    if config["inputMode"] != "live" or not 10 <= args.seconds <= 30:
        raise ValueError("bounded_live_localization_check_required")
    direct(args.output.parent, directory=True)
    if (not args.output.is_absolute() or args.output.resolve() != args.output
            or args.output.exists() or args.output.is_symlink()):
        raise ValueError("new_private_live_localization_output_required")
    args.output.mkdir(mode=0o700)
    key, cert = args.output / "key.pem", args.output / "cert.pem"
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=Isolated live localization check",
        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, timeout=15,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    code = secrets.token_hex(24)
    environment = {**os.environ, "LINGXI_HOST": "127.0.0.1", "LINGXI_PORT": "0",
        "LINGXI_TLS_KEY": str(key), "LINGXI_TLS_CERT": str(cert),
        "LINGXI_PAIRING_CODE_SHA256": digest(code.encode())}
    command = [sys.executable, "-B", str(ROOT / "robot-runtime/python/managed_localization.py")]
    for field in (*FIELDS, "node", "node-sha256", "api-bundle", "api-sha256"):
        value = args.output / "run" if field == "output" else getattr(args, field.replace("-", "_"))
        command.extend(("--" + field, str(value)))
    command.extend(("--seconds", str(args.seconds)))
    report = {"result": "failed", "robotContacted": True, "motionCommandsSent": False,
              "localizationReady": False, "navigationReady": False,
              "mapProfileAssociationVerified": False, "seedRequests": 0,
              "seedSource": None, "pauseConfirmed": False, "stopConfirmed": False,
              "staleGenerationRejected": False, "stoppedQueries": 0,
              "referenceFramesObserved": []}
    process = connection = None
    try:
        descriptor = os.open(args.output / "private-launcher.log",
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as log:
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=log, close_fds=True, start_new_session=True)
        port = listening(process)["port"]
        connection = http.client.HTTPSConnection(
            "127.0.0.1", port, context=ssl.create_default_context(cafile=str(cert)), timeout=3.)
        token = None

        def request(route, *, body=None, method="GET", expected=200):
            headers = {"authorization": "Bearer " + token} if token else {}
            if body is not None:
                headers["content-type"] = "application/json"
            connection.request(method, "/api/v1/" + route,
                json.dumps(body, allow_nan=False) if body is not None else None, headers)
            response = connection.getresponse()
            data = response.read(4 * 1024 * 1024 + 1)
            if response.status != expected or len(data) > 4 * 1024 * 1024:
                raise RuntimeError("expected_bounded_live_https_response_required")
            return strict_json(data)

        request("slam/localization", expected=401)
        token = request("session/pair", body={"pairingCode": code}, method="POST", expected=201)["token"]
        phase, deadline = "unseeded", time.monotonic() + args.seconds + 10.
        references = report["referenceFramesObserved"]
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("live_localization_check_deadline")
            try:
                state = request("slam/localization")
            except (ConnectionError, http.client.HTTPException):
                if phase == "stopped":
                    break
                raise
            if (state["binding"]["input_mode"] != "live" or state["mapFromSensor"] is not None
                    or state["localizationReady"] or state["navigationReady"]):
                raise RuntimeError("unseeded_live_pose_or_readiness_forbidden")
            if "basePose" in config:
                base = state.get("basePose", {})
                if (base.get("status") != "unavailable" or base.get("mapFromBase") is not None
                        or base.get("metresPerMapUnit") != config["basePose"]["metresPerMapUnit"]
                        or any(base.get(name) is not False for name in (
                            "physicalScaleValidated", "navigationReady"))):
                    raise RuntimeError("unseeded_base_pose_or_readiness_forbidden")
            fields = {"binding": state["binding"], "expectedGeneration": state["generation"]}
            if phase == "unseeded":
                if state["state"] != "unseeded":
                    raise RuntimeError("explicit_seed_must_remain_required")
                frame = state["referenceFrame"]
                if frame is not None and (not references or frame > references[-1]):
                    references.append(frame)
                if len(references) >= 5:
                    paused = request("slam/localization/pause", body=fields, method="POST", expected=202)
                    if paused["state"] != "paused" or paused["mapFromSensor"] is not None:
                        raise RuntimeError("immediate_live_pause_required")
                    request("slam/localization/stop", body=fields, method="POST", expected=409)
                    report.update(pauseConfirmed=True, staleGenerationRejected=True)
                    phase = "paused"
            elif phase == "paused":
                if state["state"] != "paused":
                    raise RuntimeError("paused_live_session_revived")
                stopped = request("slam/localization/stop", body=fields, method="POST", expected=202)
                if stopped["state"] != "stopped" or stopped["mapFromSensor"] is not None:
                    raise RuntimeError("immediate_live_stop_required")
                report["stopConfirmed"], phase = True, "stopped"
            else:
                if state["state"] != "stopped":
                    raise RuntimeError("stopped_live_session_revived")
                report["stoppedQueries"] += 1
            time.sleep(.05)
        if process.wait(timeout=15) != 0 or phase != "stopped" or report["stoppedQueries"] < 5:
            raise RuntimeError("complete_live_localization_check_required")
        output = args.output / "run"
        owner = strict_json((output / "owner-report.json").read_bytes())
        child = strict_json((output / "native/child-report.json").read_bytes())
        if "basePose" in config:
            inputs = child.get("basePoseInput", {})
            verify_base_input(inputs)
            report.update(basePoseInputChecked=True, basePoseInput=inputs)
        if (owner["result"] != "completed" or owner["inputComplete"]
                or not owner["intervalComplete"] or not owner["inputsRechecked"]):
            raise RuntimeError("verified_live_interval_cleanup_required")
        report.update(result="managed_live_unseeded_localization_https_checked",
            completedMessages=owner["nativeProcess"]["completedMessages"],
            processedScans=owner["nativeProcess"]["processedScans"],
            estimatedPoses=owner["nativeProcess"]["estimatedPoses"],
            nativeCalls=child["nativeCalls"],
            singleNativeWorker=child["nativeOwnerIsSingleNonMainThread"],
            apiProcess=owner["apiProcess"], nativeProcess={key: owner["nativeProcess"][key] for key in (
                "state", "exitCode", "forcedTermination", "cleanupComplete", "processAlive", "monitorAlive")},
            sourceSha256={**source_hashes(), **{name: digest((ROOT / name).read_bytes()) for name in (
                "robot-runtime/python/managed_localization.py", "robot-runtime/python/localization_owner.py",
                "tools/check_managed_live_localization.py")}},
            apiBundleSha256=args.api_sha256, nodeSha256=args.node_sha256,
            receiptSha256=args.receipt_sha256, profileSha256=args.profile_sha256,
            privateReportsSha256={name: digest((output / name).read_bytes()) for name in (
                "owner-report.json", "native/child-report.json", "native/localization-final.json")})
    except BaseException as error:
        report["errorType"] = type(error).__name__
        raise
    finally:
        if connection is not None:
            connection.close()
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            process.stdout.close()
        private_json(args.output / "check-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in (*FIELDS, "node", "node-sha256", "api-bundle", "api-sha256"):
        parser.add_argument("--" + field, required=True, type=Path if field in (
            "config", "saved-map", "output", "node", "api-bundle") else str)
    parser.add_argument("--seconds", type=float, default=15.)
    previous = os.umask(0o077)
    try:
        report = check(parser.parse_args())
        print(json.dumps({"result": report["result"], "completedMessages": report["completedMessages"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
