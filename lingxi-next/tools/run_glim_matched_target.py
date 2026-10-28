"""One passive live capture and a fresh offline replay; no product or motion changes."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shlex

from run_glim_live_target import command as live_command
from run_glim_target_preprocess import execute
from run_slam_target_trial import connect, target_directory
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "arm64_identical_callback_input_glim_diagnostic"
RESULT = "same_input_session_closed_not_calibration_or_navigation_acceptance"


def command(remote, core, home, mode, seconds):
    if mode not in ("live", "replay"):
        raise ValueError("explicit_matched_mode_required")
    values = shlex.split(live_command(remote, core, home, "header_is_scan_start",
                                     remote / ("result-" + mode), seconds))
    values[values.index(str(remote / "tools/glim_live_session.py"))] = str(remote / "tools/glim_matched_session.py")
    if mode == "replay":
        values = [value for value in values if not value.startswith((
            "ROS_DISTRO=", "ROS_DOMAIN_ID=", "RMW_IMPLEMENTATION=", "FASTRTPS_DEFAULT_PROFILES_FILE="))]
        values.extend(("--reference", str(remote / "result-live")))
    values.extend(("--mode", mode))
    return shlex.join(values)


def run(args):
    output, bundle = args.output.resolve(), args.bundle.resolve(strict=True)
    if output.exists() or not output.is_relative_to((ROOT / ".local").resolve()) or args.user == "root":
        raise ValueError("new_private_matched_results_required")
    home = PurePosixPath("/home/validation")
    command(home / "trial", home / "core", home, "live", args.seconds)
    # The same manifest schema/integrity check, with this diagnostic's purpose.
    manifest = json.loads((bundle / "replay-manifest.json").read_text())
    if manifest["purpose"] != PURPOSE or manifest["sameCallbackInputDiagnostic"] is not True:
        raise ValueError("explicit_matched_input_bundle_required")
    for name, entry in manifest["files"].items():
        relative, path = PurePosixPath(name), bundle / name
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(bundle)
                or sha256(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]):
            raise ValueError("unchanged_matched_bundle_members_required")
    output.mkdir(mode=0o700, parents=True)
    collection = {"result": "running", "coordinatorSha256": sha256(__file__),
                  "replayManifestSha256": sha256(bundle / "replay-manifest.json"), "runs": {}}
    client = None
    def checkpoint():
        (output / "collection-report.json").write_text(json.dumps(collection, indent=2) + "\n")
    try:
        checkpoint()
        client = connect(args.host, args.user, args.known_identity)
        with client.open_sftp() as sftp:
            home = PurePosixPath(sftp.normalize("."))
            remote, core = (target_directory(str(home), name) for name in (args.directory, args.core_directory))
            sftp.mkdir(str(remote), mode=0o700)
            names = sorted(manifest["files"]) + ["replay-manifest.json"]
            parents = set()
            for name in names:
                parent = (remote / name).parent
                while parent != remote:
                    parents.add(parent)
                    parent = parent.parent
            for parent in sorted(parents, key=lambda value: (len(value.parts), str(value))):
                sftp.mkdir(str(parent), mode=0o700)
            for name in names:
                sftp.put(str(bundle / name), str(remote / name), confirm=True)
                sftp.chmod(str(remote / name), 0o600)
            for mode in ("live", "replay"):
                print(json.dumps({"phase": "matched_" + mode + "_started"}), flush=True)
                status, text = execute(client, command(remote, core, home, mode, args.seconds))
                local = output / mode
                local.mkdir(mode=0o700)
                (local / "private-command-output.log").write_bytes(text)
                collection["runs"][mode] = {"exitCode": status}
                for name in ("report.json", "private-native.log", "private-results.npz",
                             *(("private-callbacks.cdrjournal",) if mode == "live" else ())):
                    try:
                        sftp.get(str(remote / ("result-" + mode) / name), str(local / name))
                    except FileNotFoundError:
                        if status == 0:
                            raise
                checkpoint()
                if status:
                    raise RuntimeError("matched_session_failed_see_private_results")
                report = json.loads((local / "report.json").read_text())
                if (report["result"] != RESULT or report["purpose"] != PURPOSE
                        or report["replayManifestSha256"] != collection["replayManifestSha256"]
                        or report["target"]["machine"] != "aarch64" or any(report["boundaries"].values())
                        or any(sha256(local / name) != digest for name, digest in report["filesSha256"].items())):
                    raise ValueError("complete_verified_matched_target_files_required")
                collection["runs"][mode]["reportSha256"] = sha256(local / "report.json")
                checkpoint()
                print(json.dumps({"phase": "matched_" + mode + "_closed"}), flush=True)
        collection["result"] = "live_and_identical_prefix_replay_closed_not_product_acceptance"
    except BaseException as error:
        collection.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        checkpoint()
    return collection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity", "directory", "core-directory"):
        parser.add_argument("--" + name, required=True)
    for name in ("bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(parser.parse_args())["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
