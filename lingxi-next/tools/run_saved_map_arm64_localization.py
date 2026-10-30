"""Reuse an accepted ARM64 v2 map and existing dependencies for recorded HTTPS localization."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re

import run_glim_point_target as remote
from run_localization_arm64_trial import FILES, command, read_remote_report
from run_mapping_arm64_trial import RESULT as MAPPING_RESULT
from verify_slam_capture import sha256

ROOT = Path(__file__).resolve().parents[1]
RESULT = "arm64_v2_saved_map_recorded_localization_checked_not_physical_accuracy"


def validate_check(check, collection, api_sha256, receipt_sha256):
    uploaded = collection["uploadedFiles"]
    required = {"robot-runtime/python/controlled_localization.py",
                "robot-runtime/python/managed_localization.py",
                "robot-runtime/python/localization_owner.py",
                "tools/check_managed_localization_runtime.py"}
    if (check.get("result") != "managed_recorded_localization_https_checked"
            or check.get("receiptSha256") != receipt_sha256
            or check.get("apiBundleSha256") != api_sha256
            or not required.issubset(check.get("sourceSha256", {}))
            or any(name not in uploaded or digest != uploaded[name]["sha256"]
                   for name, digest in check["sourceSha256"].items())
            or any(check.get(name) is not True for name in (
                "pauseConfirmed", "stopConfirmed", "staleGenerationRejected", "singleNativeWorker"))
            or any(check.get(name) is not False for name in (
                "motionCommandsSent", "localizationReady", "navigationReady", "mapProfileAssociationVerified"))
            or check.get("completedMessages") != collection["summary"]["runs"][0]["completedMessages"]
            or check.get("nativeProcess") != {
                "state": "stopped", "exitCode": 0, "forcedTermination": False,
                "cleanupComplete": True, "processAlive": False, "monitorAlive": False}
            or check.get("apiProcess") != {
                "exitCode": 0, "forcedTermination": False, "processAlive": False, "groupAlive": False}
            or check.get("seedRequests") != 1):
        raise ValueError("complete_same_map_source_and_recorded_localization_required")


def collect(args):
    output = args.output.resolve()
    if (args.user == "root" or output.exists() or output == (ROOT / ".local").resolve()
            or not output.is_relative_to((ROOT / ".local").resolve())
            or not re.fullmatch(r"mlv2-[a-z0-9-]{1,18}", args.run_name)
            or args.api_bundle.is_symlink() or not args.api_bundle.is_file()):
        raise ValueError("new_private_recorded_map_localization_collection_required")
    collection = json.loads(args.mapping_collection.read_text(encoding="utf-8"))
    if (collection.get("result") != MAPPING_RESULT or collection.get("exitCode") != 0
            or collection.get("hostIdentityVerified") is not True
            or collection.get("connectionClosed") is not True):
        raise ValueError("completed_identity_checked_arm64_v2_map_collection_required")
    baseline_evidence = json.loads((ROOT / "docs/slam-managed-localization-evidence.json").read_text())
    api_sha256 = sha256(args.api_bundle)
    output.mkdir(mode=0o700, parents=True)
    report = {"result": "running", "robotContacted": False, "hostIdentityVerified": False,
              "motionCommandsSent": False, "connectionClosed": False, "filesSha256": {},
              "mappingCollectionSha256": sha256(args.mapping_collection),
              "coordinatorSha256": sha256(__file__)}
    client = None
    try:
        client = remote.connect(args.host, args.user, args.known_identity)
        report.update(robotContacted=True, hostIdentityVerified=True)
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(30.)
            home = PurePosixPath(sftp.normalize("."))
            source, baseline = (remote.target_directory(str(home), name) for name in (
                args.directory, args.baseline_directory))
            saved = PurePosixPath(collection["savedMap"])
            trial = saved.parents[1]
            result = home / ".cache/lingxi-next" / args.run_name
            if (not re.fullmatch(r"[a-f0-9]{32}", saved.name) or saved.parent.name != "maps"
                    or trial.parent != result.parent or not re.fullmatch(r"mv2-[a-z0-9-]+", trial.name)
                    or source == baseline or len(os.fsencode(result / "run/native/control.sock")) >= 100):
                raise ValueError("separate_short_home_scoped_saved_map_check_required")
            uid = sftp.stat(str(home)).st_uid
            for folder in (baseline, saved, trial, result.parent):
                remote.private_directory(sftp, folder, uid)
            for folder in (source, result):
                remote.require_absent(sftp, folder)
            config = read_remote_report(sftp, trial / "private-config.json")
            plan = read_remote_report(sftp, baseline / "private-plan.json")
            if (config != read_remote_report(sftp, baseline / "private-config.json")
                    or config.get("platform") != "arm64-humble" or config.get("inputMode") != "recorded"
                    or config.get("rate") != 1. or plan.get("source") != str(baseline)):
                raise ValueError("same_preserved_recorded_configuration_required")
            consumer = PurePosixPath(plan["consumer"])
            if consumer != remote.target_directory(str(home), consumer.name):
                raise ValueError("home_scoped_consumer_required")
            remote.private_directory(sftp, consumer, uid)
            receipt = read_remote_report(sftp, saved / "map-ready.json")
            receipt_sha256 = remote.remote_digest(sftp, saved / "map-ready.json")["sha256"]
            if (receipt.get("schemaVersion") != 2
                    or receipt != json.loads((args.mapping_collection.parent / "map-ready.json").read_text())):
                raise ValueError("unchanged_accepted_schema_v2_receipt_required")
            report["receiptSha256"] = receipt_sha256
            seed = baseline / "private-seed.npz"
            if remote.remote_digest(sftp, seed)["sha256"] != baseline_evidence["seedSampleSha256"]:
                raise ValueError("unchanged_preserved_diagnostic_seed_required")
            sftp.mkdir(str(source), mode=0o700)
            sftp.put(str(args.api_bundle), str(source / "api.mjs"), confirm=True)
            sftp.chmod(str(source / "api.mjs"), 0o600)
            sftp.symlink(str(consumer / "node_modules"), str(source / "node_modules"))
            if remote.remote_digest(sftp, source / "api.mjs")["sha256"] != api_sha256:
                raise ValueError("same_current_localization_api_bundle_required")
            environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                "AMENT_PREFIX_PATH": "/opt/ros/humble",
                "PYTHONPATH": "/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
                "LD_LIBRARY_PATH": ":".join((config["root"] + "/lib", config["coreRoot"] + "/lib",
                    "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
                "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
            status, text = remote.execute(client, command([
                baseline / "env/bin/python", "-B",
                trial / "runtime-source/tools/check_managed_localization_runtime.py",
                "--config", trial / "private-config.json", "--saved-map", saved,
                "--map-id", "slam-" + receipt["session"], "--receipt-sha256", receipt_sha256,
                "--profile-sha256", receipt["sensorProvenance"]["profileSha256"],
                "--seed-sample", seed, "--seed-sha256", baseline_evidence["seedSampleSha256"],
                "--node", consumer / "node/bin/node", "--node-sha256", plan["nodeSha256"],
                "--api-bundle", source / "api.mjs", "--api-sha256", api_sha256,
                "--output", result,
            ], environment, 150))
            (output / "private-command.log").write_bytes(text)
            report["exitCode"] = status
            for name in FILES:
                try:
                    expected = remote.remote_digest(sftp, result / name)
                except FileNotFoundError:
                    continue
                destination = output / name
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                sftp.get(str(result / name), str(destination))
                if sha256(destination) != expected["sha256"]:
                    raise ValueError("identical_saved_map_localization_download_required")
                report["filesSha256"][name] = expected["sha256"]
            if status:
                raise RuntimeError("saved_map_localization_failed_see_private_reports")
            check = json.loads((output / "check-report.json").read_text())
            validate_check(check, collection, api_sha256, receipt_sha256)
            if (sha256(args.api_bundle) != api_sha256
                    or any(sha256(ROOT / name) != digest for name, digest in check["sourceSha256"].items())):
                raise ValueError("unchanged_current_localization_inputs_required")
            report.update(result=RESULT, check=check)
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        report["connectionClosed"] = True
        (output / "collection-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity", "directory", "baseline-directory", "run-name"):
        parser.add_argument("--" + name, required=True)
    for name in ("mapping-collection", "api-bundle", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": collect(parser.parse_args())["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
