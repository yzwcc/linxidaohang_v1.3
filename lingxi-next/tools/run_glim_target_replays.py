"""Run four bounded, private ARM64 bag replays over authenticated LAN SSH."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import stat
import time

from run_slam_target_trial import connect, target_directory
from slam_target_probe import sha256


RUNS = (
    ("staticStart", "static", "header_is_scan_start"),
    ("staticEnd", "static", "header_is_scan_end"),
    ("dynamicStart", "dynamic", "header_is_scan_start"),
    ("dynamicEnd", "dynamic", "header_is_scan_end"),
)


def replay_command(remote, core, home, dataset, hypothesis, destination, mapping=False):
    if (dataset not in ("static", "dynamic")
            or hypothesis not in ("header_is_scan_start", "header_is_scan_end")
            or destination.parent != remote or core.parent != home or remote.parent != home):
        raise ValueError("explicit_home_scoped_recorded_replay_required")
    entry = [str(remote / "tools/glim_target_mapping.py"), "run"] if mapping else [
        str(remote / "tools/glim_target_replay.py"),
    ]
    return shlex.join([
        "timeout", "--signal=INT", "--kill-after=10s", "480s", "nice", "-n", "10",
        "env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(home), "LANG=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1",
        "AMENT_PREFIX_PATH=/opt/ros/humble",
        "PYTHONPATH=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
        "LD_LIBRARY_PATH=" + ":".join((str(remote / "lib"), str(core / "lib"),
                                      "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
        "OMP_NUM_THREADS=2", "OPENBLAS_NUM_THREADS=2",
        "python3", *entry,
        "--root", str(remote), "--core-root", str(core), "--data-root", str(home),
        "--dataset", dataset, "--hypothesis", hypothesis, "--output", str(destination),
    ])


def download_map(sftp, remote, local, depth=0):
    if depth > 1:
        raise ValueError("only_expected_flat_numbered_submaps_supported")
    local.mkdir(mode=0o700)
    for entry in sftp.listdir_attr(str(remote)):
        name = entry.filename
        if not name or name in (".", "..") or any(character in name for character in ("/", "\\", "\x00", ":")):
            raise ValueError("direct_map_member_name_required")
        if stat.S_ISDIR(entry.st_mode):
            if depth != 0 or len(name) != 6 or not name.isascii() or not name.isdigit():
                raise ValueError("only_numbered_submap_directories_supported")
            download_map(sftp, remote / name, local / name, depth + 1)
        elif stat.S_ISREG(entry.st_mode):
            sftp.get(str(remote / name), str(local / name))
            (local / name).chmod(0o600)
        else:
            raise ValueError("direct_regular_map_artifacts_required")


def run(args):
    if args.output.exists() or args.user == "root":
        raise ValueError("new_private_results_and_ordinary_user_required")
    bundle = args.bundle.resolve(strict=True)
    mapping = getattr(args, "mapping", False)
    manifest = json.loads((bundle / "replay-manifest.json").read_text())
    purpose = "arm64_recorded_glim_mapping_trial" if mapping else "arm64_recorded_glim_odometry_trial"
    if manifest.get("purpose") != purpose:
        raise ValueError("recorded_replay_bundle_required")
    for name, entry in manifest["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("direct_relative_bundle_files_required")
        path = bundle / name
        if path.is_symlink() or sha256(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]:
            raise ValueError("recorded_replay_bundle_changed")
    args.output.mkdir(parents=True, mode=0o700)
    result = {"purpose": "actual_arm64_recorded_glim_replay_collection",
              "replayManifestSha256": sha256(bundle / "replay-manifest.json"),
              "runs": {}, "result": "running"}
    if mapping:
        result["purpose"] = "actual_arm64_recorded_glim_mapping_collection"
        result["coordinatorSourceSha256"] = sha256(__file__)

    def checkpoint():
        (args.output / "collection-report.json").write_text(json.dumps(result, indent=2) + "\n")

    client = None
    try:
        checkpoint()
        client = connect(args.host, args.user, args.known_identity)
        with client.open_sftp() as sftp:
            home = PurePosixPath(sftp.normalize("."))
            remote = target_directory(str(home), args.directory)
            core = target_directory(str(home), args.core_directory)
            sftp.mkdir(str(remote), mode=0o700)
            names = sorted(manifest["files"]) + ["replay-manifest.json"]
            parents = set()
            for name in names:
                parent = (remote / name).parent
                while parent != remote:
                    parents.add(parent)
                    parent = parent.parent
            for parent in sorted(parents, key=lambda path: (len(path.parts), str(path))):
                sftp.mkdir(str(parent), mode=0o700)
            for name in names:
                sftp.put(str(bundle / name), str(remote / name), confirm=True)
                sftp.chmod(str(remote / name), 0o600)
            print(json.dumps({"phase": "private_replay_bundle_uploaded", "files": len(names)}), flush=True)
            for label, dataset, hypothesis in RUNS:
                print(json.dumps({"phase": "target_recorded_replay_start", "run": label}), flush=True)
                destination = remote / ("result-" + label)
                channel = client.get_transport().open_session(timeout=8)
                output, errors = bytearray(), bytearray()
                try:
                    channel.exec_command(replay_command(remote, core, home, dataset, hypothesis, destination, mapping))
                    deadline = time.monotonic() + 510
                    while True:
                        for ready, receive, data in (
                            (channel.recv_ready, channel.recv, output),
                            (channel.recv_stderr_ready, channel.recv_stderr, errors),
                        ):
                            if ready():
                                block = receive(65536)
                                if len(data) + len(block) > 1024 * 1024:
                                    raise RuntimeError("unexpected_recorded_replay_output_volume")
                                data.extend(block)
                        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                            break
                        if time.monotonic() > deadline:
                            raise TimeoutError("bounded_target_replay_did_not_return")
                        time.sleep(0.1)
                    status = channel.recv_exit_status()
                finally:
                    channel.close()
                local = args.output / label
                local.mkdir(mode=0o700)
                (local / "private-command-output.log").write_bytes(output + errors)
                result["runs"][label] = {"exitCode": status}
                checkpoint()
                if status:
                    failed_files = ("report.json", "private-native.log")
                    if mapping:
                        failed_files += ("mapping-report.json", "reload-report.json", "private-reload.log")
                    for name in failed_files:
                        try:
                            sftp.get(str(destination / name), str(local / name))
                            (local / name).chmod(0o600)
                        except FileNotFoundError:
                            pass
                    raise RuntimeError("recorded_target_replay_failed_see_private_output")
                files = ("report.json", "private-native.log", "private-trajectory.npz")
                if mapping:
                    files += ("mapping-report.json", "reload-report.json", "private-reload.log",
                              "private-saved-points.f64", "private-reloaded-points.f64")
                for name in files:
                    sftp.get(str(destination / name), str(local / name))
                    (local / name).chmod(0o600)
                report = json.loads((local / "report.json").read_text())
                if (report.get("result") != "all_recorded_scans_accounted_for_on_arm64_not_quality_acceptance"
                        or report["dataset"] != dataset or report["timingHypothesis"] != hypothesis
                        or report["replayManifestSha256"] != result["replayManifestSha256"]
                        or report["privateTrajectorySha256"] != sha256(local / "private-trajectory.npz")
                        or report["privateNativeLogSha256"] != sha256(local / "private-native.log")
                        or report["boundaries"].get("globalMappingExecuted") is not mapping
                        or any(value is not False for key, value in report["boundaries"].items()
                               if key != "globalMappingExecuted")):
                    raise ValueError("completed_target_recorded_trial_contract_mismatch")
                if mapping:
                    mapped = json.loads((local / "mapping-report.json").read_text())
                    reloaded = json.loads((local / "reload-report.json").read_text())
                    if (mapped.get("result") != "arm64_nonempty_map_saved_and_fresh_process_reloaded_not_quality_acceptance"
                            or mapped["dataset"] != dataset or mapped["timingHypothesis"] != hypothesis
                            or mapped["replayManifestSha256"] != result["replayManifestSha256"]
                            or mapped["replayPid"] == reloaded["pid"]
                            or not mapped["checks"] or any(value is not True for value in mapped["checks"].values())
                            or any(value is not False for value in mapped["boundaries"].values())
                            or mapped["baseReplayReportSha256"] != sha256(local / "report.json")
                            or mapped["reloadReportSha256"] != sha256(local / "reload-report.json")
                            or mapped["privateReloadLogSha256"] != sha256(local / "private-reload.log")
                            or mapped["privateSavedPointsSha256"] != sha256(local / "private-saved-points.f64")
                            or mapped["privateReloadedPointsSha256"] != sha256(local / "private-reloaded-points.f64")):
                        raise ValueError("completed_target_mapping_trial_contract_mismatch")
                    download_map(sftp, destination / "map", local / "map")
                    result["runs"][label]["localMappingReportSha256"] = sha256(local / "mapping-report.json")
                result["runs"][label]["localReportSha256"] = sha256(local / "report.json")
                checkpoint()
                print(json.dumps({"phase": "target_recorded_replay_completed", "run": label,
                                  "processedScans": report["processedScans"],
                                  "estimatedPoses": report["estimatedPoses"]}), flush=True)
        result["result"] = ("four_actual_arm64_recorded_mapping_trials_completed_not_quality_acceptance"
                            if mapping else "four_actual_arm64_recorded_trials_completed_not_quality_acceptance")
    except BaseException as error:
        result.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        checkpoint()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity", "directory", "core-directory"):
        parser.add_argument("--" + name, required=True)
    for name in ("bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--mapping", action="store_true")
    args = parser.parse_args()
    old_umask = os.umask(0o077)
    try:
        print(json.dumps(run(args), indent=2))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
