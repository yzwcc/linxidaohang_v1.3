"""One explicit ARM64 supervised recorded replay over identity-checked LAN SSH."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat

import glim_supervised_target as target
from run_glim_target_preprocess import execute
from run_slam_target_trial import connect, target_directory
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
RESULT = "arm64_supervised_recorded_replay_collected_not_product_acceptance"
FILES = ("report.json", "child-report.json", "private-child.log")


def source_files():
    names = set(target.SOURCES)
    names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "tools").glob("*.py"))
    result = {}
    for name in sorted(names):
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("direct_project_source_required")
        result[name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    return result


def replay_command(source, root, core, reference, home, output, hypothesis, rate):
    target.shared.replay_rate(rate)
    if (hypothesis not in ("header_is_scan_start", "header_is_scan_end")
            or any(path.parent != home for path in (source, root, core))
            or len({source, root, core}) != 3 or reference != root / "result-live"
            or output.parent != home / ".cache/lingxi-next"
            or not re.fullmatch(r"glim-supervised-replay-[a-z0-9-]+", output.name)):
        raise ValueError("explicit_separate_home_scoped_recorded_paths_required")
    environment = {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
        "AMENT_PREFIX_PATH": "/opt/ros/humble",
        "PYTHONPATH": ("/opt/ros/humble/local/lib/python3.10/dist-packages:"
                       "/opt/ros/humble/lib/python3.10/site-packages"),
        "LD_LIBRARY_PATH": ":".join((str(root / "lib"), str(core / "lib"),
                                    "/opt/ros/humble/lib/aarch64-linux-gnu", "/opt/ros/humble/lib")),
        "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
        "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }
    return shlex.join([
        "timeout", "--signal=INT", "--kill-after=10s", "180s", "env", "-i",
        *(key + "=" + value for key, value in environment.items()),
        "/usr/bin/python3", "-B", str(source / "tools/glim_supervised_target.py"), "run",
        "--root", str(root), "--core-root", str(core), "--reference", str(reference),
        "--output", str(output), "--hypothesis", hypothesis, "--rate", str(rate),
        "--operator-confirmed",
    ])


def paths(home, args):
    source, root, core = (
        target_directory(str(home), name)
        for name in (args.directory, args.recorded_directory, args.core_directory)
    )
    output = home / ".cache/lingxi-next" / args.run_name
    command = replay_command(source, root, core, root / "result-live", home, output,
                             args.hypothesis, args.rate)
    return source, output, command


def run(args):
    if getattr(args, "operator_confirmed", False) is not True:
        raise ValueError("fresh_stationary_supervised_estop_confirmation_required")
    output = args.output.resolve()
    if (args.user == "root" or output.exists()
            or not output.is_relative_to((ROOT / ".local").resolve())):
        raise ValueError("ordinary_user_and_new_private_local_output_required")
    paths(PurePosixPath("/home/validation"), args)
    sources = source_files()
    output.mkdir(mode=0o700, parents=True)
    collection = {
        "schemaVersion": 1, "result": "running", "coordinatorSha256": sha256(__file__),
        "uploadedSourceFiles": sources, "filesSha256": {},
    }
    client = None

    def checkpoint():
        (output / "collection-report.json").write_text(
            json.dumps(collection, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    try:
        checkpoint()
        client = connect(args.host, args.user, args.known_identity)
        with client.open_sftp() as sftp:
            source, remote_output, command = paths(PurePosixPath(sftp.normalize(".")), args)
            sftp.mkdir(str(source), mode=0o700)
            parents = set()
            for name in sources:
                parent = (source / name).parent
                while parent != source:
                    parents.add(parent)
                    parent = parent.parent
            for parent in sorted(parents, key=lambda value: (len(value.parts), str(value))):
                sftp.mkdir(str(parent), mode=0o700)
            for name, expected in sources.items():
                if sha256(ROOT / name) != expected["sha256"]:
                    raise ValueError("source_changed_during_upload")
                sftp.put(str(ROOT / name), str(source / name), confirm=True)
                sftp.chmod(str(source / name), 0o600)
                with sftp.open(str(source / name), "rb") as stream:
                    import hashlib
                    if hashlib.file_digest(stream, "sha256").hexdigest() != expected["sha256"]:
                        raise ValueError("identical_uploaded_source_required")
            print(json.dumps({"phase": "supervised_source_uploaded", "files": len(sources)}), flush=True)
            status, text = execute(client, command)
            (output / "private-command-output.log").write_bytes(text)
            collection["exitCode"] = status
            for name in FILES:
                try:
                    info = sftp.lstat(str(remote_output / name))
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
                        raise ValueError("bounded_direct_private_supervised_result_required")
                    sftp.get(str(remote_output / name), str(output / name))
                    (output / name).chmod(0o600)
                    collection["filesSha256"][name] = sha256(output / name)
                except FileNotFoundError:
                    if status == 0:
                        raise
            checkpoint()
            if status != 0:
                raise RuntimeError("target_supervised_replay_failed_see_private_results")
            parent, child = (target.shared.load(output / name) for name in FILES[:2])
            target.shared.complete_child(child, parent["inputIntegrity"]["journal"])
            if (parent["result"] != target.RESULT or child["machine"] != "aarch64"
                    or parent["inputIntegrity"]["sourceSha256"] != target.hashes()
                    or child["inputIntegrity"] != parent["inputIntegrity"]
                    or parent["childReportSha256"] != collection["filesSha256"]["child-report.json"]
                    or parent["privateLogSha256"] != collection["filesSha256"]["private-child.log"]
                    or parent["process"]["session"] != child["session"]):
                raise ValueError("same_complete_target_supervised_results_required")
            collection["result"] = RESULT
            print(json.dumps({"phase": "supervised_recorded_replay_collected",
                              "completedMessages": child["worker"]["counts"]["completedMessages"]}), flush=True)
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
    for name in ("host", "user", "known-identity", "directory", "recorded-directory",
                 "core-directory", "run-name"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
    parser.add_argument("--rate", type=float, choices=(.5, 1.), required=True)
    parser.add_argument("--operator-confirmed", action="store_true")
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(parser.parse_args())["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
