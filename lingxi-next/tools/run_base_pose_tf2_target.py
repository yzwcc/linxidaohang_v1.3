"""Upload the small synthetic tf2 check to a new private ordinary-user directory."""
import argparse
import json
import math
import os
from pathlib import Path, PurePosixPath
import shlex

import check_base_pose_tf2 as probe
from run_glim_point_target import connect, execute, private_directory, remote_digest, require_absent, target_directory
from verify_slam_capture import sha256

ROOT = Path(__file__).resolve().parents[1]


def validate(report, hashes):
    if (type(report.get("schemaVersion")) is not int or report["schemaVersion"] != 1
            or report.get("result") != probe.RESULT
            or report.get("machine") != "aarch64" or report.get("sourceSha256") != hashes
            or report.get("inputClassification") != "synthetic_transforms_no_sensors"
            or report.get("checks") != dict.fromkeys(probe.CHECKS, True)
            or any(value is not True for value in report["checks"].values())
            or report.get("boundaries") != dict.fromkeys(probe.BOUNDARIES, False)
            or any(value is not False for value in report["boundaries"].values())
            or type(report.get("maximumMatrixElementError")) not in (int, float)
            or not math.isfinite(report["maximumMatrixElementError"])
            or not 0 <= report["maximumMatrixElementError"] <= 1e-10):
        raise ValueError("source_bound_arm64_synthetic_tf2_report_required")


def run(args):
    if args.user == "root" or args.output.exists():
        raise ValueError("ordinary_user_and_new_private_output_required")
    args.output.mkdir(parents=True, mode=0o700)
    hashes = {name: sha256(ROOT / name) for name in probe.SOURCES}
    collection = {"result": "running", "sourceSha256": hashes,
                  "coordinatorSha256": sha256(__file__), "connectionClosed": False}
    client = None
    try:
        client = connect(args.host, args.user, args.known_identity)
        with client.open_sftp() as sftp:
            home = PurePosixPath(sftp.normalize("."))
            source = target_directory(str(home), args.directory)
            baseline = target_directory(str(home), args.baseline_directory)
            if source == baseline:
                raise ValueError("preserved_baseline_and_new_source_required")
            status, text = execute(client, "/usr/bin/python3 -c " + shlex.quote(
                "import json,os,platform; print(json.dumps({'uid':os.geteuid(),'machine':platform.machine()}))"))
            target = json.loads(text)
            if status != 0 or type(target["uid"]) is not int or target["uid"] <= 0 or target["machine"] != "aarch64":
                raise ValueError("ordinary_arm64_target_required")
            private_directory(sftp, baseline, target["uid"])
            require_absent(sftp, source)
            sftp.mkdir(str(source), mode=0o700)
            for folder in (source / "tools", source / "robot-slam", source / "robot-slam/lingxi_slam"):
                sftp.mkdir(str(folder), mode=0o700)
            for name, digest in hashes.items():
                sftp.put(str(ROOT / name), str(source / name), confirm=True)
                sftp.chmod(str(source / name), 0o600)
                if remote_digest(sftp, source / name)["sha256"] != digest or sha256(ROOT / name) != digest:
                    raise ValueError("unchanged_uploaded_source_required")
            environment = {
                "PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8",
                "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "AMENT_PREFIX_PATH": "/opt/ros/humble",
                "PYTHONPATH": "/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
                "LD_LIBRARY_PATH": "/opt/ros/humble/lib/aarch64-linux-gnu:/opt/ros/humble/lib",
                "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
            }
            status, text = execute(client, shlex.join([
                "timeout", "--signal=TERM", "--kill-after=5s", "30s", "env", "-i",
                *(key + "=" + value for key, value in environment.items()),
                str(baseline / "env/bin/python"), "-B", str(source / "tools/check_base_pose_tf2.py")])
                + " > " + shlex.quote(str(source / "report.json"))
                + " 2> " + shlex.quote(str(source / "private-warnings.log")))
            (args.output / "private-command-output.log").write_bytes(text)
            collection.update(exitCode=status, outputSha256=sha256(args.output / "private-command-output.log"))
            for name in ("report.json", "private-warnings.log"):
                expected = remote_digest(sftp, source / name, maximum=1024 * 1024)
                sftp.get(str(source / name), str(args.output / name))
                if sha256(args.output / name) != expected["sha256"]:
                    raise ValueError("unchanged_downloaded_tf2_result_required")
            collection["warningsSha256"] = sha256(args.output / "private-warnings.log")
            if status != 0:
                raise RuntimeError("target_tf2_check_failed_see_private_log")
            report = json.loads((args.output / "report.json").read_text())
            validate(report, hashes)
            if hashes != {name: sha256(ROOT / name) for name in probe.SOURCES}:
                raise ValueError("unchanged_local_probe_sources_required")
            collection.update(result=probe.RESULT, reportSha256=sha256(args.output / "report.json"))
    except BaseException as error:
        collection.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        collection["connectionClosed"] = True
        (args.output / "collection-report.json").write_text(json.dumps(collection, indent=2, allow_nan=False) + "\n")
    return collection


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity", "directory", "baseline-directory"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--output", type=Path, required=True)
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(parser.parse_args())["result"]}))
    finally:
        os.umask(previous)
