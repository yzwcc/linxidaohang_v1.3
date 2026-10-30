"""Exercise the recorded runtime's actual process and private control socket."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "tools")]
from localization_control import digest, direct, strict_json
from mapping_service import private_json
import controlled_localization as runtime


def request(endpoint, value):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(.5)
        client.connect(str(endpoint))
        client.sendall((json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n").encode())
        with client.makefile("rb") as stream:
            response = stream.readline(8193)
        if len(response) > 8192 or not response.endswith(b"\n"):
            raise ValueError("bounded_localization_response_required")
        return strict_json(response)


def check(args):
    runtime.verified_configuration(args.config)
    direct(args.output.parent, directory=True)
    direct(args.seed_sample)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("new_localization_check_output_required")
    sample_bytes = args.seed_sample.read_bytes()
    if len(sample_bytes) > 4 * 1024 * 1024 or digest(sample_bytes) != args.seed_sha256:
        raise ValueError("expected_bounded_recorded_seed_sample_required")
    with np.load(args.seed_sample, allow_pickle=False) as sample:
        seed, reference_frame = sample["world_lidar_pose"].tolist(), int(sample["frame_id"])
    args.output.mkdir(mode=0o700)
    run_output = args.output / "run"
    command = [sys.executable, "-B",
               str(ROOT / "robot-runtime/python/controlled_localization.py"), "run"]
    for field in runtime.FIELDS:
        value = run_output if field == "output" else getattr(args, field.replace("-", "_"))
        command.extend(("--" + field, str(value)))
    command.extend(("--seconds", "90"))
    log_file = args.output / "private-parent.log"
    descriptor = os.open(log_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    process = None
    report = {"result": "failed", "seedRequests": 0, "staleGenerationRejected": False,
              "pauseConfirmed": False, "stopConfirmed": False, "stoppedQueries": 0,
              "robotContacted": False, "motionCommandsSent": False,
              "localizationReady": False, "navigationReady": False,
              "mapProfileAssociationVerified": False,
              "seedSource": "explicit_preserved_recorded_pose_not_independent_truth"}
    try:
        with os.fdopen(descriptor, "wb") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       close_fds=True, start_new_session=True)
        endpoint, deadline = run_output / "localization.sock", time.monotonic() + 105.
        phase, pause_at, second_reference, stale_seed = "initial", None, None, None
        while process.poll() is None:
            if time.monotonic() >= deadline:
                raise RuntimeError("bounded_localization_check_deadline")
            try:
                response = request(endpoint, {"action": "state"})
            except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError, BrokenPipeError):
                time.sleep(.005)
                continue
            if not response["ok"]:
                raise RuntimeError("localization_state_unavailable")
            state = response["value"]
            if state["localizationReady"] or state["navigationReady"]:
                raise RuntimeError("unexpected_localization_readiness")
            command_fields = {"binding": state["binding"], "expectedGeneration": state["generation"]}
            if phase == "initial" and state["referenceFrame"] is not None:
                if state["referenceFrame"] != reference_frame:
                    raise RuntimeError("original_first_seed_reference_missed")
                stale_seed = {"action": "seed", **command_fields, "mapFromSensor": seed,
                              "referenceFrame": reference_frame}
                accepted = request(endpoint, stale_seed)
                if not accepted["ok"] or accepted["value"]["state"] != "seed_requested":
                    raise RuntimeError("explicit_seed_not_accepted")
                report["seedRequests"] += 1
                phase = "first_tracking"
            elif phase == "first_tracking" and state["state"] == "tracking" and state["lastFrame"] >= 6:
                paused = request(endpoint, {"action": "pause", **command_fields})
                if (not paused["ok"] or paused["value"]["state"] != "paused"
                        or paused["value"]["mapFromSensor"] is not None):
                    raise RuntimeError("immediate_pause_not_confirmed")
                stale = request(endpoint, stale_seed)
                if stale.get("ok") is not False or stale.get("code") != "localization_generation_changed":
                    raise RuntimeError("old_generation_not_rejected")
                report.update(pauseConfirmed=True, staleGenerationRejected=True,
                              firstTrackingFrameObserved=state["lastFrame"])
                phase, pause_at = "paused", time.monotonic()
            elif phase == "paused":
                if state["state"] != "paused" or state["mapFromSensor"] is not None:
                    raise RuntimeError("paused_localization_revived")
                if time.monotonic() - pause_at >= .25 and state["referenceFrame"] is not None:
                    second_reference = state["referenceFrame"]
                    accepted = request(endpoint, {"action": "seed", **command_fields,
                        "mapFromSensor": seed, "referenceFrame": second_reference})
                    if not accepted["ok"]:
                        raise RuntimeError("second_explicit_seed_not_accepted")
                    report["seedRequests"] += 1
                    phase = "second_tracking"
            elif (phase == "second_tracking" and state["state"] == "tracking"
                  and state["lastFrame"] >= second_reference + 3):
                stopped = request(endpoint, {"action": "stop", **command_fields})
                if (not stopped["ok"] or stopped["value"]["state"] != "stopped"
                        or stopped["value"]["mapFromSensor"] is not None):
                    raise RuntimeError("immediate_stop_not_confirmed")
                report.update(stopConfirmed=True, secondTrackingFrameObserved=state["lastFrame"])
                phase = "stopped"
            elif phase == "stopped":
                if state["state"] != "stopped" or state["mapFromSensor"] is not None:
                    raise RuntimeError("stopped_localization_revived")
                report["stoppedQueries"] += 1
            time.sleep(.005)
        if process.wait(timeout=1) != 0 or phase != "stopped" or report["stoppedQueries"] < 1:
            raise RuntimeError("complete_controlled_localization_check_required")
        parent = strict_json((run_output / "localization-runtime-report.json").read_bytes())
        child = strict_json((run_output / "child-report.json").read_bytes())
        runtime.replay.complete_child(child, child["inputIntegrity"]["journal"])
        if parent["result"] != "recorded_localization_runtime_closed":
            raise RuntimeError("complete_runtime_report_required")
        report.update(result="recorded_localization_control_checked",
                      completedMessages=parent["process"]["completedMessages"],
                      processedScans=parent["process"]["processedScans"],
                      estimatedPoses=parent["process"]["estimatedPoses"],
                      nativeCalls=child["nativeCalls"],
                      singleNativeWorker=child["nativeOwnerIsSingleNonMainThread"],
                      sourceSha256=runtime.source_hashes(),
                      checkSourceSha256=digest(Path(__file__).read_bytes()),
                      inputJournalSha256=child["inputIntegrity"]["journalSha256"],
                      receiptSha256=args.receipt_sha256, profileSha256=args.profile_sha256,
                      seedSampleSha256=args.seed_sha256,
                      actualLoadedLibrariesSha256=child["actualLoadedLibrariesSha256"],
                      process={key: parent["process"][key] for key in (
                          "state", "exitCode", "forcedTermination", "cleanupComplete",
                          "processAlive", "monitorAlive")},
                      privateReportsSha256={name: digest((run_output / name).read_bytes()) for name in (
                          "localization-runtime-report.json", "child-report.json", "localization-final.json")})
    except BaseException as error:
        report["errorType"] = type(error).__name__
        raise
    finally:
        if process is not None and process.poll() is None:
            # Killing the outer runtime closes its child's private ownership pipe.
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        private_json(args.output / "check-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in (*runtime.FIELDS, "seed-sample", "seed-sha256"):
        parser.add_argument("--" + field, required=True,
            type=Path if field in ("config", "saved-map", "output", "seed-sample") else str)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = check(args)
        print(json.dumps({"result": result["result"], "completedMessages": result["completedMessages"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
