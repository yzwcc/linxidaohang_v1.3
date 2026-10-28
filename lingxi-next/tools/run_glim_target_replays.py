"""Run four bounded, private ARM64 bag replays over authenticated LAN SSH."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import time

from run_slam_target_trial import connect, target_directory
from slam_target_probe import sha256


RUNS = (
    ("staticStart", "static", "header_is_scan_start"),
    ("staticEnd", "static", "header_is_scan_end"),
    ("dynamicStart", "dynamic", "header_is_scan_start"),
    ("dynamicEnd", "dynamic", "header_is_scan_end"),
)


def replay_command(remote, core, home, dataset, hypothesis, destination):
    if (dataset not in ("static", "dynamic")
            or hypothesis not in ("header_is_scan_start", "header_is_scan_end")
            or destination.parent != remote or core.parent != home or remote.parent != home):
        raise ValueError("explicit_home_scoped_recorded_replay_required")
    return shlex.join([
        "timeout", "--signal=INT", "--kill-after=10s", "480s", "nice", "-n", "10",
        "env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(home), "LANG=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1",
        "AMENT_PREFIX_PATH=/opt/ros/humble",
        "PYTHONPATH=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
        "LD_LIBRARY_PATH=" + ":".join((str(remote / "lib"), str(core / "lib"),
                                      "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
        "OMP_NUM_THREADS=2", "OPENBLAS_NUM_THREADS=2",
        "python3", str(remote / "tools/glim_target_replay.py"),
        "--root", str(remote), "--core-root", str(core), "--data-root", str(home),
        "--dataset", dataset, "--hypothesis", hypothesis, "--output", str(destination),
    ])


def run(args):
    if args.output.exists() or args.user == "root":
        raise ValueError("new_private_results_and_ordinary_user_required")
    bundle = args.bundle.resolve(strict=True)
    manifest = json.loads((bundle / "replay-manifest.json").read_text())
    if manifest.get("purpose") != "arm64_recorded_glim_odometry_trial":
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
                    channel.exec_command(replay_command(remote, core, home, dataset, hypothesis, destination))
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
                    for name in ("report.json", "private-native.log"):
                        try:
                            sftp.get(str(destination / name), str(local / name))
                            (local / name).chmod(0o600)
                        except FileNotFoundError:
                            pass
                    raise RuntimeError("recorded_target_replay_failed_see_private_output")
                for name in ("report.json", "private-native.log", "private-trajectory.npz"):
                    sftp.get(str(destination / name), str(local / name))
                    (local / name).chmod(0o600)
                report = json.loads((local / "report.json").read_text())
                if (report.get("result") != "all_recorded_scans_accounted_for_on_arm64_not_quality_acceptance"
                        or report["dataset"] != dataset or report["timingHypothesis"] != hypothesis
                        or report["replayManifestSha256"] != result["replayManifestSha256"]
                        or report["privateTrajectorySha256"] != sha256(local / "private-trajectory.npz")
                        or report["privateNativeLogSha256"] != sha256(local / "private-native.log")
                        or any(value is not False for value in report["boundaries"].values())):
                    raise ValueError("completed_target_recorded_trial_contract_mismatch")
                result["runs"][label]["localReportSha256"] = sha256(local / "report.json")
                checkpoint()
                print(json.dumps({"phase": "target_recorded_replay_completed", "run": label,
                                  "processedScans": report["processedScans"],
                                  "estimatedPoses": report["estimatedPoses"]}), flush=True)
        result["result"] = "four_actual_arm64_recorded_trials_completed_not_quality_acceptance"
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
    args = parser.parse_args()
    old_umask = os.umask(0o077)
    try:
        print(json.dumps(run(args), indent=2))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
