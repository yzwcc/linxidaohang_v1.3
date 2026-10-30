"""Bounded passive live check reusing preserved ARM64 libraries and Python."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

import run_glim_point_target as remote
from run_localization_arm64_trial import (
    FILES, command, read_remote_report, source_files)
from verify_slam_capture import sha256

ROOT = Path(__file__).resolve().parents[1]
RESULT = "arm64_live_unseeded_localization_https_collected_not_navigation_acceptance"


def prepare(plan_file):
    sys.path.insert(0, str(ROOT / "robot-runtime/python"))
    from mapping_platform import MappingPlatform, configuration
    from localization_control import load_map
    from mapping_service import private_json
    from prepare_glim_point_consumer import verify_bundle
    plan = json.loads(plan_file.read_bytes())
    consumer = Path(plan["consumer"])
    manifest = verify_bundle(consumer, plan["consumerManifestSha256"])
    if manifest["files"]["node/bin/node"]["sha256"] != plan["nodeSha256"]:
        raise ValueError("same_preserved_arm64_node_required")
    for name, entry in plan["uploadedFiles"].items():
        file = ROOT / name
        if (file.is_symlink() or not file.resolve(strict=True).is_relative_to(ROOT)
                or file.stat().st_size != entry["bytes"] or sha256(file) != entry["sha256"]):
            raise ValueError("same_live_trial_source_bytes_required")
    (ROOT / "node_modules").symlink_to(consumer / "node_modules", target_is_directory=True)
    platform = MappingPlatform(configuration(ROOT / "private-config.json")[0])
    load_map(Path(plan["savedMap"]), plan["mapId"], plan["receiptSha256"],
             platform.profile, plan["profileSha256"], session="0" * 32, input_mode="live")
    import small_gicp
    from importlib.metadata import version
    if version("small-gicp") != "1.0.1":
        raise ValueError("preserved_small_gicp_build_required")
    report = {"result": "live_localization_inputs_prepared", "frozenInputs": platform.frozen,
              "smallGicpExtensionSha256": sha256(small_gicp.__file__)}
    private_json(ROOT / "private-prepare-report.json", report)
    return report


def collect(args):
    output = args.output.resolve()
    if (output.exists() or not output.is_relative_to((ROOT / ".local").resolve())
            or output == ROOT / ".local" or args.user == "root"
            or not re.fullmatch(r"ll-[a-z0-9-]{1,20}", args.run_name)):
        raise ValueError("new_private_ordinary_user_live_trial_required")
    sources = source_files()
    for name in ("tools/run_localization_arm64_trial.py",
                 "tools/run_live_localization_arm64_trial.py",
                 "tools/check_managed_live_localization.py"):
        sources[name] = ROOT / name
    sources["api.mjs"] = args.api_bundle
    files = {name: {"sha256": sha256(file), "bytes": file.stat().st_size}
             for name, file in sources.items()}
    output.mkdir(mode=0o700, parents=True)
    report = {"result": "running", "robotContacted": False, "hostIdentityVerified": False,
              "motionCommandsSent": False, "localizationReady": False, "navigationReady": False,
              "mapProfileAssociationVerified": False, "filesSha256": {}, "uploadedFiles": files}
    client = None
    try:
        client = remote.connect(args.host, args.user, args.known_identity)
        report.update(robotContacted=True, hostIdentityVerified=True)
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(30.)
            home = PurePosixPath(sftp.normalize("."))
            baseline, live, source = (remote.target_directory(str(home), getattr(args, name))
                                      for name in ("baseline_directory", "live_directory", "directory"))
            config = read_remote_report(sftp, baseline / "private-config.json")
            plan = read_remote_report(sftp, baseline / "private-plan.json")
            config.update(inputMode="live", root=str(live), maxSeconds=90.)
            config.pop("reference")
            config.pop("rate")
            result = home / ".cache/lingxi-next" / args.run_name
            if (len({baseline, live, source, PurePosixPath(plan["consumer"])}) != 4
                    or len(os.fsencode(result / "run/native/localization.sock")) >= 100):
                raise ValueError("separate_bounded_live_trial_paths_required")
            uid = sftp.stat(str(home)).st_uid
            for folder in (baseline, live, PurePosixPath(plan["consumer"]), result.parent):
                remote.private_directory(sftp, folder, uid)
            remote.require_absent(sftp, source)
            remote.require_absent(sftp, result)
            sftp.mkdir(str(source), mode=0o700)
            parents = {parent for name in sources for parent in (source / name).parents
                       if parent != source and source in parent.parents}
            for parent in sorted(parents, key=lambda value: (len(value.parts), str(value))):
                sftp.mkdir(str(parent), mode=0o700)
            for name, file in sources.items():
                if file.is_symlink() or sha256(file) != files[name]["sha256"]:
                    raise ValueError("unchanged_direct_live_trial_source_required")
                sftp.put(str(file), str(source / name), confirm=True)
                sftp.chmod(str(source / name), 0o600)
                if remote.remote_digest(sftp, source / name, maximum=64 * 1024**2) != files[name]:
                    raise ValueError("identical_uploaded_live_trial_bytes_required")
            plan.update(uploadedFiles=files, savedMap=str(baseline / "maps/saved-map"))
            for name, value in (("private-config.json", config), ("private-plan.json", plan)):
                with sftp.open(str(source / name), "wx") as stream:
                    stream.write(json.dumps(value, indent=2) + "\n")
                sftp.chmod(str(source / name), 0o600)
            environment = {
                "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "AMENT_PREFIX_PATH": "/opt/ros/humble",
                "PYTHONPATH": "/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
                "LD_LIBRARY_PATH": ":".join((str(live / "lib"), config["coreRoot"] + "/lib",
                    "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
                "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
            }
            python = baseline / "env/bin/python"
            consumer = PurePosixPath(plan["consumer"])
            print(json.dumps({"phase": "live_sources_uploaded", "files": len(files)}), flush=True)
            for label, argv, seconds in (
                ("prepare", [python, "-B", source / "tools/run_live_localization_arm64_trial.py",
                    "prepare", "--plan", source / "private-plan.json"], 60),
                ("check", [python, "-B", source / "tools/check_managed_live_localization.py",
                    "--config", source / "private-config.json", "--saved-map", plan["savedMap"],
                    "--map-id", plan["mapId"], "--receipt-sha256", plan["receiptSha256"],
                    "--profile-sha256", plan["profileSha256"], "--node", consumer / "node/bin/node",
                    "--node-sha256", plan["nodeSha256"], "--api-bundle", source / "api.mjs",
                    "--api-sha256", files["api.mjs"]["sha256"], "--output", result,
                    "--seconds", "15"], 100),
            ):
                status, text = remote.execute(client, command(argv, environment, seconds))
                log = output / ("private-" + label + "-command.log")
                log.write_bytes(text)
                report["filesSha256"][log.name] = sha256(log)
                report[label + "ExitCode"] = status
                if status:
                    report["result"] = "failed"
                    break
                if label == "prepare":
                    report["prepare"] = read_remote_report(sftp, source / "private-prepare-report.json")
                print(json.dumps({"phase": label + "_completed"}), flush=True)
            for name in FILES:
                try:
                    expected = remote.remote_digest(sftp, result / name)
                except FileNotFoundError:
                    continue
                file = output / name
                file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                sftp.get(str(result / name), str(file))
                if sha256(file) != expected["sha256"]:
                    raise ValueError("identical_live_trial_reports_required")
                report["filesSha256"][name] = expected["sha256"]
            if report["result"] == "failed":
                raise RuntimeError("live_localization_check_failed_see_private_reports")
            check = json.loads((output / "check-report.json").read_bytes())
            if (check["result"] != "managed_live_unseeded_localization_https_checked"
                    or not all(check[key] is True for key in (
                        "pauseConfirmed", "stopConfirmed", "staleGenerationRejected", "singleNativeWorker"))
                    or check["seedRequests"] != 0 or check["seedSource"] is not None
                    or len(check["referenceFramesObserved"]) < 5 or check["stoppedQueries"] < 5
                    or any(check[key] is not False for key in (
                        "motionCommandsSent", "localizationReady", "navigationReady",
                        "mapProfileAssociationVerified"))
                    or any(sha256(file) != files[name]["sha256"] for name, file in sources.items())):
                raise ValueError("complete_same_source_unseeded_live_check_required")
            report.update(result=RESULT, checkSummary=check)
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        report["connectionClosed"] = True
        (output / "collection-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare").add_argument("--plan", type=Path, required=True)
    run = commands.add_parser("collect")
    for name in ("host", "user", "known-identity", "directory", "baseline-directory",
                 "live-directory", "run-name"):
        run.add_argument("--" + name, required=True)
    for name in ("api-bundle", "output"):
        run.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = prepare(args.plan) if args.command == "prepare" else collect(args)
        print(json.dumps({"result": result["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
