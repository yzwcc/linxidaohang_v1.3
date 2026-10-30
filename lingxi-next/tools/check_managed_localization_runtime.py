"""Check real recorded native localization through the owned, paired HTTPS API."""
import argparse
import http.client
import json
import os
from pathlib import Path
import secrets
import selectors
import ssl
import subprocess
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "tools")]
from controlled_localization import FIELDS, source_hashes, verified_configuration
from localization_control import digest, direct, strict_json
from mapping_service import private_json


def listening(process):
    data = bytearray()
    deadline = time.monotonic() + 45.
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while process.poll() is None and time.monotonic() < deadline:
            if not selector.select(.05):
                continue
            block = os.read(process.stdout.fileno(), 4097)
            if not block:
                break
            data.extend(block)
            if len(data) > 4096:
                raise ValueError("bounded_owner_startup_status_required")
            if b"\n" in data:
                value = strict_json(data)
                if (type(value) is not dict or type(value.get("port")) is not int
                        or not 1 <= value["port"] <= 65535 or value != {
                            "service": "lingxi-next", "event": "listening", "port": value["port"]}):
                    raise ValueError("exact_owned_api_startup_status_required")
                return value
    raise RuntimeError("owned_localization_https_not_started")


def check(args):
    verified_configuration(args.config)
    direct(args.output.parent, directory=True)
    direct(args.seed_sample)
    if (not args.output.is_absolute() or args.output.resolve() != args.output
            or args.output.exists() or args.output.is_symlink()):
        raise ValueError("new_private_managed_localization_check_output_required")
    sample_bytes = args.seed_sample.read_bytes()
    if len(sample_bytes) > 4 * 1024 * 1024 or digest(sample_bytes) != args.seed_sha256:
        raise ValueError("bounded_preserved_seed_sample_required")
    with np.load(args.seed_sample, allow_pickle=False) as sample:
        seed = sample["world_lidar_pose"].tolist()
    args.output.mkdir(mode=0o700)
    key, cert = args.output / "key.pem", args.output / "cert.pem"
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=Isolated recorded localization check",
        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, timeout=15,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    code = secrets.token_hex(24)
    environment = dict(os.environ)
    environment.update(LINGXI_HOST="127.0.0.1", LINGXI_PORT="0", LINGXI_TLS_KEY=str(key),
                       LINGXI_TLS_CERT=str(cert), LINGXI_PAIRING_CODE_SHA256=digest(code.encode()))
    command = [sys.executable, "-B", str(ROOT / "robot-runtime/python/managed_localization.py")]
    for field in (*FIELDS, "node", "node-sha256", "api-bundle", "api-sha256"):
        value = args.output / "run" if field == "output" else getattr(args, field.replace("-", "_"))
        command.extend(("--" + field, str(value)))
    command.extend(("--seconds", "90"))
    report = {"result": "failed", "robotContacted": False, "motionCommandsSent": False,
              "localizationReady": False, "navigationReady": False,
              "mapProfileAssociationVerified": False,
              "seedSource": "preserved_recorded_pose_at_explicit_current_reference_not_truth",
              "seedRequests": 0, "pauseConfirmed": False, "stopConfirmed": False,
              "staleGenerationRejected": False, "stoppedQueries": 0}
    process = connection = None
    try:
        descriptor = os.open(args.output / "private-launcher.log",
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as log:
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=log, close_fds=True,
                                       start_new_session=True)
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
                raise RuntimeError("expected_bounded_localization_https_response_required")
            return strict_json(data)

        request("slam/localization", expected=401)
        token = request("session/pair", body={"pairingCode": code}, method="POST", expected=201)["token"]
        phase, deadline = "initial", time.monotonic() + 95.
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise TimeoutError("managed_localization_check_deadline")
            try:
                state = request("slam/localization")
            except (ConnectionError, http.client.HTTPException):
                if phase == "stopped":
                    break
                raise
            if state["localizationReady"] or state["navigationReady"]:
                raise RuntimeError("unexpected_localization_readiness")
            fields = {"binding": state["binding"], "expectedGeneration": state["generation"]}
            if phase == "initial" and state["referenceFrame"] is not None:
                seed_request = {**fields, "mapFromSensor": seed, "referenceFrame": state["referenceFrame"]}
                accepted = request("slam/localization/seed", body=seed_request, method="POST", expected=202)
                if accepted["state"] != "seed_requested" or accepted["mapFromSensor"] is not None:
                    raise RuntimeError("explicit_https_seed_admission_required")
                report.update(seedRequests=1, seedReferenceFrame=state["referenceFrame"])
                phase = "tracking"
            elif phase == "tracking" and state["state"] == "tracking":
                paused = request("slam/localization/pause", body=fields, method="POST", expected=202)
                if paused["state"] != "paused" or paused["mapFromSensor"] is not None:
                    raise RuntimeError("immediate_https_pause_required")
                request("slam/localization/seed", body=seed_request, method="POST", expected=409)
                report.update(pauseConfirmed=True, staleGenerationRejected=True,
                              trackingFrameObserved=state["lastFrame"])
                phase = "paused"
            elif phase == "paused":
                if state["state"] != "paused" or state["mapFromSensor"] is not None:
                    raise RuntimeError("paused_https_pose_revived")
                stopped = request("slam/localization/stop", body=fields, method="POST", expected=202)
                if stopped["state"] != "stopped" or stopped["mapFromSensor"] is not None:
                    raise RuntimeError("immediate_https_stop_required")
                report["stopConfirmed"], phase = True, "stopped"
            elif phase == "stopped":
                if state["state"] != "stopped" or state["mapFromSensor"] is not None:
                    raise RuntimeError("stopped_https_pose_revived")
                report["stoppedQueries"] += 1
            time.sleep(.025)
        if process.wait(timeout=15) != 0 or phase != "stopped" or report["stoppedQueries"] < 1:
            raise RuntimeError("complete_owned_localization_check_required")
        output = args.output / "run"
        owner = strict_json((output / "owner-report.json").read_bytes())
        child = strict_json((output / "native/child-report.json").read_bytes())
        if owner["result"] != "completed" or not owner["inputComplete"] or not owner["inputsRechecked"]:
            raise RuntimeError("verified_complete_localization_owner_required")
        report.update(result="managed_recorded_localization_https_checked",
            completedMessages=owner["nativeProcess"]["completedMessages"],
            processedScans=owner["nativeProcess"]["processedScans"],
            estimatedPoses=owner["nativeProcess"]["estimatedPoses"],
            nativeCalls=child["nativeCalls"], singleNativeWorker=child["nativeOwnerIsSingleNonMainThread"],
            apiProcess=owner["apiProcess"], nativeProcess={key: owner["nativeProcess"][key] for key in (
                "state", "exitCode", "forcedTermination", "cleanupComplete", "processAlive", "monitorAlive")},
            sourceSha256={**source_hashes(), **{name: digest((ROOT / name).read_bytes()) for name in (
                "robot-runtime/python/managed_localization.py", "robot-runtime/python/localization_owner.py",
                "tools/check_managed_localization_runtime.py")}},
            apiBundleSha256=args.api_sha256, nodeSha256=args.node_sha256,
            inputJournalSha256=child["inputIntegrity"]["journalSha256"],
            receiptSha256=args.receipt_sha256, profileSha256=args.profile_sha256,
            seedSampleSha256=args.seed_sha256,
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
    for field in (*FIELDS, "node", "node-sha256", "api-bundle", "api-sha256",
                  "seed-sample", "seed-sha256"):
        parser.add_argument("--" + field, required=True, type=Path if field in (
            "config", "saved-map", "output", "node", "api-bundle", "seed-sample") else str)
    previous = os.umask(0o077)
    try:
        report = check(parser.parse_args())
        print(json.dumps({"result": report["result"], "completedMessages": report["completedMessages"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
