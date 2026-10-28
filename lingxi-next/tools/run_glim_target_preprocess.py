"""Twelve fresh ARM64 recorded preprocessing processes over authenticated LAN SSH."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import time

from run_slam_target_trial import connect, target_directory
from verify_slam_capture import sha256


CONDITIONS = tuple((dataset, threads, repeat)
                   for dataset in ("static", "dynamic")
                   for threads in (2, 1) for repeat in range(1, 4))
FILES = ("report.json", "private-index.npz", "private-prepared-xyzt.f64",
         "private-neighbors.i32", "private-metrics.npz", "private-native.log")


def command(remote, core, home, dataset, threads, output):
    if (dataset not in ("static", "dynamic") or threads not in (1, 2)
            or remote.parent != home or core.parent != home or output.parent != remote):
        raise ValueError("explicit_home_scoped_preprocessing_condition_required")
    return shlex.join([
        "timeout", "--signal=INT", "--kill-after=10s", "300s", "nice", "-n", "10",
        "env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(home), "LANG=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1", "AMENT_PREFIX_PATH=/opt/ros/humble",
        "PYTHONPATH=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
        "LD_LIBRARY_PATH=" + ":".join((str(remote / "lib"), str(core / "lib"),
                                      "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
        "OMP_NUM_THREADS=2", "OPENBLAS_NUM_THREADS=2",
        "python3", str(remote / "tools/glim_target_preprocess.py"),
        "--root", str(remote), "--core-root", str(core), "--data-root", str(home),
        "--dataset", dataset, "--threads", str(threads), "--output", str(output),
    ])


def execute(client, text):
    channel = client.get_transport().open_session(timeout=8)
    output, errors = bytearray(), bytearray()
    try:
        channel.exec_command(text)
        deadline = time.monotonic() + 330
        while True:
            for ready, receive, data in (
                (channel.recv_ready, channel.recv, output),
                (channel.recv_stderr_ready, channel.recv_stderr, errors),
            ):
                if ready():
                    block = receive(65536)
                    if len(data) + len(block) > 1024 * 1024:
                        raise RuntimeError("unexpected_preprocessing_command_output_volume")
                    data.extend(block)
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                return channel.recv_exit_status(), bytes(output + errors)
            if time.monotonic() > deadline:
                raise TimeoutError("bounded_preprocessing_process_did_not_return")
            time.sleep(0.1)
    finally:
        channel.close()


def run(args):
    if args.output.exists() or args.user == "root":
        raise ValueError("new_private_result_and_ordinary_user_required")
    bundle = args.bundle.resolve(strict=True)
    manifest = json.loads((bundle / "preprocess-manifest.json").read_text())
    if manifest["purpose"] != "arm64_recorded_native_preprocessing_repeatability":
        raise ValueError("native_preprocessing_bundle_required")
    for name, entry in manifest["files"].items():
        relative = PurePosixPath(name)
        path = bundle / name
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                or path.is_symlink() or sha256(path) != entry["sha256"]
                or path.stat().st_size != entry["bytes"]):
            raise ValueError("unchanged_direct_preprocessing_bundle_required")
    args.output.mkdir(parents=True, mode=0o700)
    result = {
        "purpose": "actual_arm64_twelve_preprocessing_observations",
        "result": "running", "coordinatorSourceSha256": sha256(__file__),
        "bundleManifestSha256": sha256(bundle / "preprocess-manifest.json"), "runs": {},
    }

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
            names = sorted(manifest["files"]) + ["preprocess-manifest.json"]
            parents = set()
            for name in names:
                parent = (remote / name).parent
                while parent != remote:
                    parents.add(parent)
                    parent = parent.parent
            for path in sorted(parents, key=lambda value: (len(value.parts), str(value))):
                sftp.mkdir(str(path), mode=0o700)
            for name in names:
                sftp.put(str(bundle / name), str(remote / name), confirm=True)
                sftp.chmod(str(remote / name), 0o600)
            print(json.dumps({"phase": "preprocessing_bundle_uploaded", "files": len(names)}), flush=True)
            for dataset, threads, repeat in CONDITIONS:
                label = f"{dataset}-threads{threads}-repeat{repeat}"
                destination = remote / ("result-" + label)
                print(json.dumps({"phase": "preprocessing_started", "condition": label}), flush=True)
                status, output = execute(client, command(remote, core, home, dataset, threads, destination))
                local = args.output / label
                local.mkdir(mode=0o700)
                (local / "private-command-output.log").write_bytes(output)
                result["runs"][label] = {"exitCode": status}
                checkpoint()
                if status:
                    for name in ("report.json", "private-native.log"):
                        try:
                            sftp.get(str(destination / name), str(local / name))
                        except FileNotFoundError:
                            pass
                    raise RuntimeError("target_preprocessing_failed_see_private_output")
                print(json.dumps({"phase": "preprocessing_output_download", "condition": label}), flush=True)
                for name in FILES:
                    sftp.get(str(destination / name), str(local / name))
                    (local / name).chmod(0o600)
                report = json.loads((local / "report.json").read_text())
                if (report["result"] != "complete_arm64_native_preprocessing_not_slam_acceptance"
                        or report["hostMachine"] != "aarch64" or report["dataset"] != dataset
                        or report["diagnosticThreads"] != threads
                        or report["bundleManifestSha256"] != result["bundleManifestSha256"]
                        or report["originalCapturesUnchanged"] is not True
                        or not report["boundaries"] or any(value is not False for value in report["boundaries"].values())
                        or set(report["filesSha256"]) != set(FILES) - {"report.json"}):
                    raise ValueError("completed_arm64_preprocessing_contract_required")
                for name, digest in report["filesSha256"].items():
                    if sha256(local / name) != digest:
                        raise ValueError("complete_downloaded_preprocessing_output_required")
                result["runs"][label]["reportSha256"] = sha256(local / "report.json")
                checkpoint()
                print(json.dumps({"phase": "preprocessing_completed", "condition": label,
                                  "completed": len(result["runs"]), "processedScans": report["processedScans"]}), flush=True)
        result["result"] = "all_twelve_arm64_preprocessing_observations_completed_not_slam_acceptance"
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
        result = run(args)
        print(json.dumps({"result": result["result"]}))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
