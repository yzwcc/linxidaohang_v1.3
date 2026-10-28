"""Collect twelve bounded ARM64 full-map controls; no motion or live ROS interfaces."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shlex

from run_glim_target_preprocess import execute
from run_glim_target_replays import download_map
from run_slam_target_trial import connect, target_directory
from verify_slam_capture import sha256


PURPOSE = "arm64_recorded_controlled_full_mapping"
CONDITIONS = tuple((dataset, hypothesis, repeat) for dataset in ("static", "dynamic")
                   for hypothesis in ("header_is_scan_start", "header_is_scan_end") for repeat in (1, 2, 3))
FILES = ("report.json", "private-trajectory.npz", "private-native.log", "reload-report.json",
         "private-reload.log", "private-saved-points.f64", "private-reloaded-points.f64")


def label(dataset, hypothesis, repeat):
    return f"{dataset}-{'start' if hypothesis == 'header_is_scan_start' else 'end'}-repeat{repeat}"


def command(remote, core, home, dataset, hypothesis, output):
    if (dataset not in ("static", "dynamic") or hypothesis not in (
            "header_is_scan_start", "header_is_scan_end") or remote.parent != home
            or core.parent != home or output.parent != remote):
        raise ValueError("explicit_home_scoped_controlled_map_condition_required")
    return shlex.join([
        "timeout", "--signal=INT", "--kill-after=10s", "300s", "nice", "-n", "10",
        "env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(home), "LANG=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1", "AMENT_PREFIX_PATH=/opt/ros/humble",
        "PYTHONPATH=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
        "LD_LIBRARY_PATH=" + ":".join((str(remote / "lib"), str(core / "lib"),
                                      "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
        "OMP_NUM_THREADS=2", "OPENBLAS_NUM_THREADS=2",
        "python3", str(remote / "tools/glim_target_controlled_mapping.py"), "run",
        "--root", str(remote), "--core-root", str(core), "--data-root", str(home),
        "--dataset", dataset, "--hypothesis", hypothesis, "--output", str(output),
    ])


def verify_local_bundle(root):
    manifest = json.loads((root / "replay-manifest.json").read_text())
    if manifest["purpose"] != PURPOSE:
        raise ValueError("controlled_arm64_bundle_required")
    for name, entry in manifest["files"].items():
        relative = PurePosixPath(name)
        path = root / name
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name or path.is_symlink()
                or not path.is_file() or sha256(path) != entry["sha256"]
                or path.stat().st_size != entry["bytes"]):
            raise ValueError("unchanged_direct_controlled_bundle_required")
    return manifest


def run(args):
    if args.output.exists() or args.user == "root":
        raise ValueError("new_private_controlled_results_and_ordinary_user_required")
    bundle = args.bundle.resolve(strict=True)
    manifest = verify_local_bundle(bundle)
    args.output.mkdir(mode=0o700, parents=True)
    result = {"result": "running", "runs": {}, "coordinatorSourceSha256": sha256(__file__),
              "replayManifestSha256": sha256(bundle / "replay-manifest.json")}

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
            parents = {(remote / name).parent for name in names} - {remote}
            for parent in sorted(parents, key=lambda path: (len(path.parts), str(path))):
                sftp.mkdir(str(parent), mode=0o700)
            for name in names:
                sftp.put(str(bundle / name), str(remote / name), confirm=True)
                sftp.chmod(str(remote / name), 0o600)
            print(json.dumps({"phase": "private_controlled_map_bundle_uploaded", "files": len(names)}), flush=True)
            for dataset, hypothesis, repeat in CONDITIONS:
                name = label(dataset, hypothesis, repeat)
                print(json.dumps({"phase": "controlled_target_map_started", "condition": name}), flush=True)
                destination = remote / ("result-" + name)
                status, output = execute(client, command(remote, core, home, dataset, hypothesis, destination))
                local = args.output / name
                local.mkdir(mode=0o700)
                (local / "private-command-output.log").write_bytes(output)
                result["runs"][name] = {"exitCode": status}
                checkpoint()
                if status:
                    for filename in ("controlled-report.json", "report.json", "private-native.log",
                                     "reload-report.json", "private-reload.log"):
                        try:
                            sftp.get(str(destination / filename), str(local / filename))
                        except FileNotFoundError:
                            pass
                    raise RuntimeError("controlled_target_map_failed_see_private_output")
                for filename in (*FILES, "controlled-report.json"):
                    sftp.get(str(destination / filename), str(local / filename))
                    (local / filename).chmod(0o600)
                report = json.loads((local / "controlled-report.json").read_text())
                if (report["result"] != "complete_controlled_arm64_map_saved_and_reloaded_not_geometry_acceptance"
                        or (report["dataset"], report["timingHypothesis"]) != (dataset, hypothesis)
                        or report["target"]["machine"] != "aarch64"
                        or report["replayManifestSha256"] != result["replayManifestSha256"]
                        or not report["boundaries"] or any(value is not False for value in report["boundaries"].values())
                        or set(report["filesSha256"]) != set(FILES)):
                    raise ValueError("completed_controlled_target_map_report_required")
                for filename, digest in report["filesSha256"].items():
                    if sha256(local / filename) != digest:
                        raise ValueError("complete_controlled_target_download_required")
                download_map(sftp, destination / "map", local / "map")
                result["runs"][name]["reportSha256"] = sha256(local / "controlled-report.json")
                checkpoint()
                print(json.dumps({"phase": "controlled_target_map_completed", "condition": name,
                                  "completed": len(result["runs"]), "submaps": report["mapping"]["submaps"],
                                  "points": report["mapping"]["points"]}), flush=True)
        result["result"] = "twelve_actual_arm64_controlled_maps_completed_not_geometry_acceptance"
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
        print(json.dumps({"result": run(args)["result"]}))
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
