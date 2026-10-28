"""Run four new, paced ARM64 sensor-worker processes over identity-checked SSH."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shlex

from run_glim_target_preprocess import execute
from run_slam_target_trial import connect, target_directory
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "arm64_paced_recorded_sensor_worker"
RESULT = "complete_actual_arm64_paced_worker_odometry_not_live_acceptance"
CONDITIONS = tuple((dataset, hypothesis) for dataset in ("static", "dynamic")
                   for hypothesis in ("header_is_scan_start", "header_is_scan_end"))
FILES = ("report.json", "private-trajectory.npz", "private-delivery.npz", "private-native.log")


def verify_local_bundle(root):
    manifest = json.loads((root / "replay-manifest.json").read_text())
    if manifest["purpose"] != PURPOSE:
        raise ValueError("explicit_paced_worker_bundle_required")
    for name, entry in manifest["files"].items():
        relative, path = PurePosixPath(name), root / name
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve())
                or sha256(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]):
            raise ValueError("unchanged_direct_worker_bundle_members_required")
    return manifest


def label(dataset, hypothesis):
    return dataset + ("-start" if hypothesis == "header_is_scan_start" else "-end")


def command(remote, core, home, dataset, hypothesis, output):
    if ((dataset, hypothesis) not in CONDITIONS or remote.parent != home
            or core.parent != home or output.parent != remote):
        raise ValueError("explicit_home_scoped_paced_worker_condition_required")
    return shlex.join([
        "timeout", "--signal=INT", "--kill-after=10s", "300s", "nice", "-n", "10",
        "env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(home), "LANG=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1", "AMENT_PREFIX_PATH=/opt/ros/humble",
        "PYTHONPATH=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
        "LD_LIBRARY_PATH=" + ":".join((str(remote / "lib"), str(core / "lib"),
                                      "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
        "OMP_NUM_THREADS=2", "OPENBLAS_NUM_THREADS=2",
        "python3", str(remote / "tools/glim_target_worker.py"),
        "--root", str(remote), "--core-root", str(core), "--data-root", str(home),
        "--dataset", dataset, "--hypothesis", hypothesis, "--output", str(output),
    ])


def run(args):
    output = args.output.resolve()
    if (output.exists() or not output.is_relative_to((ROOT / ".local").resolve())
            or args.user == "root"):
        raise ValueError("new_private_paced_results_and_ordinary_user_required")
    bundle = args.bundle.resolve(strict=True)
    manifest = verify_local_bundle(bundle)
    output.mkdir(mode=0o700, parents=True)
    collection = {
        "result": "running", "coordinatorSourceSha256": sha256(__file__),
        "replayManifestSha256": sha256(bundle / "replay-manifest.json"), "runs": {},
    }
    client = None

    def checkpoint():
        (output / "collection-report.json").write_text(json.dumps(collection, indent=2) + "\n")

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
            for parent in sorted(parents, key=lambda value: (len(value.parts), str(value))):
                sftp.mkdir(str(parent), mode=0o700)
            for name in names:
                sftp.put(str(bundle / name), str(remote / name), confirm=True)
                sftp.chmod(str(remote / name), 0o600)
            print(json.dumps({"phase": "paced_worker_bundle_uploaded", "files": len(names)}), flush=True)
            for dataset, hypothesis in CONDITIONS:
                name = label(dataset, hypothesis)
                print(json.dumps({"phase": "paced_worker_started", "condition": name}), flush=True)
                destination = remote / ("result-" + name)
                status, text = execute(client, command(remote, core, home, dataset, hypothesis, destination))
                local = output / name
                local.mkdir(mode=0o700)
                (local / "private-command-output.log").write_bytes(text)
                collection["runs"][name] = {"exitCode": status}
                checkpoint()
                if status:
                    for filename in ("report.json", "private-native.log"):
                        try:
                            sftp.get(str(destination / filename), str(local / filename))
                        except FileNotFoundError:
                            pass
                    raise RuntimeError("paced_target_worker_failed_see_private_output")
                for filename in FILES:
                    sftp.get(str(destination / filename), str(local / filename))
                    (local / filename).chmod(0o600)
                report = json.loads((local / "report.json").read_text())
                if (report["result"] != RESULT or report["purpose"] != PURPOSE
                        or (report["dataset"], report["timingHypothesis"]) != (dataset, hypothesis)
                        or report["target"]["machine"] != "aarch64"
                        or report["replayManifestSha256"] != collection["replayManifestSha256"]
                        or report["worker"]["state"] != "stopped" or report["worker"]["threadAlive"]
                        or not report["boundaries"] or any(report["boundaries"].values())
                        or set(report["filesSha256"]) != set(FILES) - {"report.json"}):
                    raise ValueError("complete_actual_target_worker_report_required")
                for filename, digest in report["filesSha256"].items():
                    if sha256(local / filename) != digest:
                        raise ValueError("complete_private_worker_download_required")
                collection["runs"][name]["reportSha256"] = sha256(local / "report.json")
                checkpoint()
                print(json.dumps({"phase": "paced_worker_completed", "condition": name,
                                  "processedScans": report["worker"]["stream"]["counts"]["processedScans"],
                                  "completed": len(collection["runs"])}), flush=True)
        collection["result"] = "four_actual_arm64_paced_workers_completed_not_live_acceptance"
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
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(args)["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
