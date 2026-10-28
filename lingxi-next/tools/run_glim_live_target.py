"""Two fresh short passive Humble processes; no service, mode or movement changes."""
import argparse
import json
import math
import os
from pathlib import Path, PurePosixPath
import shlex

from run_glim_target_preprocess import execute
from run_slam_target_trial import connect, target_directory
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
PURPOSE = "arm64_actual_humble_passive_odometry"
RESULT = "actual_humble_session_closed_not_mapping_or_navigation_acceptance"
HYPOTHESES = ("header_is_scan_start", "header_is_scan_end")
FILES = ("report.json", "private-native.log", "private-live-observations.npz")


def verify_local_bundle(root):
    manifest = json.loads((root / "replay-manifest.json").read_text())
    if manifest["purpose"] != PURPOSE or manifest["liveDdsTrial"] is not True:
        raise ValueError("explicit_live_bundle_required")
    for name, entry in manifest["files"].items():
        relative, path = PurePosixPath(name), root / name
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve())
                or sha256(path) != entry["sha256"] or path.stat().st_size != entry["bytes"]):
            raise ValueError("unchanged_direct_live_bundle_members_required")
    return manifest


def command(remote, core, home, hypothesis, output, seconds):
    if (type(seconds) not in (int, float) or not math.isfinite(seconds) or not 5 <= seconds <= 60
            or hypothesis not in HYPOTHESES or remote.parent != home or core.parent != home
            or output.parent != remote):
        raise ValueError("explicit_home_scoped_bounded_live_condition_required")
    return shlex.join([
        "timeout", "--signal=INT", "--kill-after=10s", str(math.ceil(seconds) + 30) + "s",
        "nice", "-n", "10", "env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(home), "LANG=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1", "ROS_DISTRO=humble", "ROS_DOMAIN_ID=0",
        "RMW_IMPLEMENTATION=rmw_fastrtps_cpp",
        "FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml",
        "AMENT_PREFIX_PATH=/opt/ros/humble",
        "PYTHONPATH=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
        "LD_LIBRARY_PATH=" + ":".join((str(remote / "lib"), str(core / "lib"),
                                      "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
        "OMP_NUM_THREADS=2", "OPENBLAS_NUM_THREADS=2", "python3",
        str(remote / "tools/glim_live_session.py"), "--root", str(remote), "--core-root", str(core),
        "--hypothesis", hypothesis, "--seconds", str(seconds), "--output", str(output),
    ])


def run(args):
    output = args.output.resolve()
    if (output.exists() or not output.is_relative_to((ROOT / ".local").resolve())
            or args.user == "root"):
        raise ValueError("new_private_live_results_and_ordinary_user_required")
    # Validate all duration/condition options before connecting or uploading.
    fake_home = PurePosixPath("/home/validation")
    command(fake_home / "live", fake_home / "core", fake_home, HYPOTHESES[0],
            fake_home / "live/result", args.seconds)
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
            print(json.dumps({"phase": "live_bundle_uploaded", "files": len(names)}), flush=True)
            for hypothesis in HYPOTHESES:
                name = hypothesis.removeprefix("header_is_scan_")
                destination = remote / ("result-" + name)
                print(json.dumps({"phase": "passive_live_started", "condition": name}), flush=True)
                status, text = execute(client, command(
                    remote, core, home, hypothesis, destination, args.seconds))
                local = output / name
                local.mkdir(mode=0o700)
                (local / "private-command-output.log").write_bytes(text)
                collection["runs"][name] = {"exitCode": status}
                for filename in FILES:
                    try:
                        sftp.get(str(destination / filename), str(local / filename))
                    except FileNotFoundError:
                        if not status:
                            raise
                checkpoint()
                if status:
                    raise RuntimeError("actual_live_session_failed_see_private_report")
                report = json.loads((local / "report.json").read_text())
                if (report["result"] != RESULT or report["purpose"] != PURPOSE
                        or report["timingHypothesis"] != hypothesis or report["target"]["machine"] != "aarch64"
                        or report["replayManifestSha256"] != collection["replayManifestSha256"]
                        or report["worker"]["threadAlive"] or any(report["boundaries"].values())
                        or set(report["filesSha256"]) != set(FILES) - {"report.json"}
                        or any(sha256(local / name) != digest for name, digest in report["filesSha256"].items())):
                    raise ValueError("complete_actual_live_download_required")
                collection["runs"][name]["reportSha256"] = sha256(local / "report.json")
                checkpoint()
                print(json.dumps({"phase": "passive_live_completed", "condition": name,
                                  "processedScans": report["observations"]["processedScans"]}), flush=True)
        collection["result"] = "two_actual_live_humble_sessions_closed_not_product_acceptance"
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
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(args)["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
