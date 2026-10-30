"""Collect v2 recorded map save/reload on the same known ARM64 robot, without motion."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

import run_glim_point_target as remote
from run_localization_arm64_trial import command, read_remote_report, source_files
from run_mapping_control_trial import SOURCE_NAMES
from verify_slam_capture import sha256

ROOT = Path(__file__).resolve().parents[1]
RESULT = "arm64_recorded_v2_mapping_collected_not_localization_or_navigation_acceptance"


def validate_result(report, uploaded, probe_sha256):
    if (type(report) is not dict or type(report.get("sourceSha256")) is not dict
            or type(report.get("sensorProvenance")) is not dict):
        raise ValueError("complete_same_source_arm64_mapping_result_required")
    if (report.get("result") != "recorded_native_service_api_save_discard_verified"
            or report.get("machine") != "aarch64" or report.get("inputMode") != "recorded"
            or report.get("serviceExitCode") != 0 or report.get("socketRemoved") is not True
            or report.get("probeSha256") != probe_sha256
            or not re.fullmatch(r"[a-f0-9]{32}", report.get("savedMapDirectory", ""))
            or any(name not in uploaded or digest != uploaded[name]["sha256"]
                   for name, digest in report.get("sourceSha256", {}).items())
            or set(report["sourceSha256"]) != set(SOURCE_NAMES)
            or report.get("sensorProvenance", {}).get("receiptSchemaVersion") != 2
            or report["sensorProvenance"].get("freshReloadMatches") is not True
            or report["sensorProvenance"].get("currentConfigurationMatches") is not True
            or report["sensorProvenance"].get("missingOrMismatchedRejected") != 4
            or report.get("motionCommandsSent") is not False):
        raise ValueError("complete_same_source_arm64_mapping_result_required")


def collect(args):
    output = args.output.resolve()
    if (args.user == "root" or output.exists() or output == ROOT / ".local"
            or not output.is_relative_to((ROOT / ".local").resolve())
            or not re.fullmatch(r"mv2-[a-z0-9-]{1,20}", args.run_name)
            or not args.probe.is_file() or args.probe.is_symlink()):
        raise ValueError("new_private_arm64_mapping_collection_required")
    sources = source_files()
    sources.update({name: ROOT / name for name in (*SOURCE_NAMES, "tools/run_mapping_arm64_trial.py")})
    files = {name: {"sha256": sha256(file), "bytes": file.stat().st_size}
             for name, file in sources.items()}
    probe_sha256 = sha256(args.probe)
    output.mkdir(mode=0o700, parents=True)
    report = {"result": "running", "hostIdentityVerified": False, "robotContacted": False,
              "motionCommandsSent": False, "uploadedFiles": files, "filesSha256": {},
              "coordinatorSha256": sha256(__file__), "connectionClosed": False}
    client = None

    def checkpoint():
        (output / "collection-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    try:
        checkpoint()
        client = remote.connect(args.host, args.user, args.known_identity)
        report.update(hostIdentityVerified=True, robotContacted=True)
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(30.)
            home = PurePosixPath(sftp.normalize("."))
            source, baseline = (remote.target_directory(str(home), name)
                                for name in (args.directory, args.baseline_directory))
            result = home / ".cache/lingxi-next" / args.run_name
            if source == baseline or len(os.fsencode(result / "control.sock")) >= 100:
                raise ValueError("separate_short_mapping_trial_paths_required")
            uid = sftp.stat(str(home)).st_uid
            if uid <= 0:
                raise ValueError("ordinary_mapping_target_owner_required")
            for folder in (baseline, result.parent):
                remote.private_directory(sftp, folder, uid)
            for folder in (source, result):
                remote.require_absent(sftp, folder)
            config = read_remote_report(sftp, baseline / "private-config.json")
            plan = read_remote_report(sftp, baseline / "private-plan.json")
            if (config.get("platform") != "arm64-humble" or config.get("inputMode") != "recorded"
                    or config.get("rate") != 1. or plan.get("source") != str(baseline)):
                raise ValueError("explicit_preserved_recorded_arm64_configuration_required")
            consumer = PurePosixPath(plan["consumer"])
            if consumer != remote.target_directory(str(home), consumer.name):
                raise ValueError("home_scoped_verified_consumer_required")
            remote.private_directory(sftp, consumer, uid)
            sftp.mkdir(str(source), mode=0o700)
            created = {source}
            for name, file in sources.items():
                destination = source / name
                for parent in reversed(destination.parents):
                    if parent != source and source not in parent.parents:
                        continue
                    if parent not in created:
                        sftp.mkdir(str(parent), mode=0o700)
                        created.add(parent)
                sftp.put(str(file), str(destination), confirm=True)
                sftp.chmod(str(destination), 0o600)
                if remote.remote_digest(sftp, destination) != files[name]:
                    raise ValueError("identical_mapping_source_upload_required")
            with sftp.open(str(source / "private-config.json"), "wx") as stream:
                stream.write(json.dumps(config))
            sftp.chmod(str(source / "private-config.json"), 0o600)
            sftp.put(str(args.probe), str(source / "probe.cjs"), confirm=True)
            sftp.chmod(str(source / "probe.cjs"), 0o600)
            if remote.remote_digest(sftp, source / "probe.cjs")["sha256"] != probe_sha256:
                raise ValueError("identical_mapping_probe_upload_required")
            print(json.dumps({"phase": "mapping_sources_verified", "files": len(files)}), flush=True)
            status, text = remote.execute(client, command([
                "/usr/bin/python3", "-B", source / "tools/run_mapping_control_trial.py",
                "--config", source / "private-config.json", "--output", result,
                "--node", consumer / "node/bin/node", "--node-sha256", plan["nodeSha256"],
                "--probe", source / "probe.cjs", "--evidence", source / "aggregate.json",
            ], {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}, 360))
            (output / "private-command.log").write_bytes(text)
            report["filesSha256"]["private-command.log"] = sha256(output / "private-command.log")
            report["exitCode"] = status
            for name in ("private-trial-report.json", "private-api-report.json", "private-service.log", "private-probe.log"):
                try:
                    expected = remote.remote_digest(sftp, result / name)
                except FileNotFoundError:
                    continue
                sftp.get(str(result / name), str(output / name))
                if sha256(output / name) != expected["sha256"]:
                    raise ValueError("identical_mapping_result_download_required")
                report["filesSha256"][name] = expected["sha256"]
            try:
                children = sftp.listdir_attr(str(result / "maps"))
            except FileNotFoundError:
                children = []
            if len(children) > 128:
                raise ValueError("bounded_mapping_trial_children_required")
            for entry in children:
                if (not stat.S_ISREG(entry.st_mode) or not re.fullmatch(
                        r"[a-f0-9]{32}\.private(?:-report\.json|(?:-reload)?\.log)", entry.filename)):
                    continue
                expected = remote.remote_digest(sftp, result / "maps" / entry.filename)
                destination = output / ("child-" + entry.filename)
                sftp.get(str(result / "maps" / entry.filename), str(destination))
                if sha256(destination) != expected["sha256"]:
                    raise ValueError("identical_mapping_child_diagnostics_required")
                report["filesSha256"][destination.name] = expected["sha256"]
            if status:
                raise RuntimeError("arm64_mapping_trial_failed_see_private_reports")
            trial = json.loads((output / "private-trial-report.json").read_text())
            validate_result(trial, files, probe_sha256)
            saved = result / "maps" / trial["savedMapDirectory"]
            report["savedMap"] = str(saved)
            for name in ("map-ready.json", "reload-report.json"):
                value = read_remote_report(sftp, saved / name)
                (output / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            receipt = json.loads((output / "map-ready.json").read_text())
            reload = json.loads((output / "reload-report.json").read_text())
            if (receipt.get("schemaVersion") != 2 or receipt.get("sensorProvenance") != reload.get("sensorProvenance")
                    or receipt.get("counts", {}).get("points") != trial["sensorProvenance"]["localizationPointsLoaded"]):
                raise ValueError("same_native_map_reload_and_provenance_required")
            aggregate = read_remote_report(sftp, source / "aggregate.json")
            if (aggregate["machine"] != "aarch64"
                    or aggregate["privateTrialReportSha256"] != sha256(output / "private-trial-report.json")
                    or any(sha256(file) != files[name]["sha256"] for name, file in sources.items())
                    or sha256(args.probe) != probe_sha256):
                raise ValueError("unchanged_arm64_mapping_collection_inputs_required")
            aggregate["boundaries"]["robotContacted"] = True
            aggregate["hostIdentityVerified"] = True
            aggregate["result"] = RESULT
            (output / "aggregate.json").write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
            report.update(result=RESULT, summary=aggregate)
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        report["connectionClosed"] = True
        checkpoint()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity", "directory", "baseline-directory", "run-name"):
        parser.add_argument("--" + name, required=True)
    for name in ("probe", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": collect(parser.parse_args())["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
