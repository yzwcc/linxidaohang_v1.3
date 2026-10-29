"""Collect one identity-checked ARM64 recorded point composition, without motion."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex

import export_glim_point_consumer as preparation
import extract_glim_point_consumer as extractor
import glim_point_supervised_target as binding
import run_glim_point_target as remote
import run_glim_supervised_target as supervised
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
RESULT = "arm64_recorded_point_composition_collected_not_live_or_product_acceptance"
FILES = ("report.json", "child-report.json", "point-report.json", "consumer-report.json",
         "private-child.log", "private-consumer.log")
PREFLIGHT = """import hashlib,json,os,platform,shutil,sys
from pathlib import Path
def digest(root,name):
    path=Path(root)/name
    if path.is_symlink() or not path.is_file() or path.stat().st_size>8388608:
        raise ValueError('bounded_direct_preserved_manifest_required')
    return hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps({
    'uid':os.geteuid(),'home':str(Path.home()),'machine':platform.machine(),
    'python':platform.python_version(),'libc':list(platform.libc_ver()),
    'freeDiskBytes':shutil.disk_usage(Path.home()).free,
    'coreManifestSha256':digest(sys.argv[1],'bundle-manifest.json'),
    'replayManifestSha256':digest(sys.argv[2],'replay-manifest.json'),
    'pointManifestSha256':digest(sys.argv[3],'point-manifest.json')
}))
"""


def source_files():
    files = supervised.source_files()
    for name in binding.source_hashes():
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("direct_current_point_composition_source_required")
        files[name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    return dict(sorted(files.items()))


def paths(home, args):
    names = ("directory", "consumer_directory", "recorded_directory", "core_directory", "point_directory")
    source, consumer, root, core, points = (
        remote.target_directory(str(home), getattr(args, name)) for name in names)
    output = home / ".cache/lingxi-next" / args.run_name
    binding.shared.replay_rate(args.rate)
    if (len({source, consumer, root, core, points}) != 5
            or not re.fullmatch(r"glim-point-channel-[a-z0-9-]+", args.run_name)
            or args.hypothesis not in ("header_is_scan_start", "header_is_scan_end")
            or len(os.fsencode(output / "point.sock")) >= 100):
        raise ValueError("separate_bounded_recorded_point_composition_paths_required")
    return source, consumer, root, core, points, output


def clean_command(seconds, argv, environment=None):
    return shlex.join([
        "timeout", "--signal=INT", "--kill-after=10s", str(seconds) + "s",
        "/usr/bin/nice", "-n", "10", "env", "-i",
        *(key + "=" + value for key, value in (environment or {
            "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
        }).items()), *map(str, argv),
    ])


def replay_command(values, args, manifest_sha256):
    source, consumer, root, core, points, output = values
    environment = {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "AMENT_PREFIX_PATH": "/opt/ros/humble",
        "PYTHONPATH": ("/opt/ros/humble/local/lib/python3.10/dist-packages:"
                       "/opt/ros/humble/lib/python3.10/site-packages"),
        "LD_LIBRARY_PATH": ":".join(map(str, (
            root / "lib", core / "lib", PurePosixPath("/opt/ros/humble/lib/aarch64-linux-gnu"),
            PurePosixPath("/opt/ros/humble/lib")))),
        "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
        "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }
    return clean_command(180, [
        "/usr/bin/python3", "-B", source / "tools/glim_point_supervised_target.py", "run",
        "--root", root, "--core-root", core, "--reference", root / "result-live",
        "--point-bundle", points, "--consumer-root", consumer, "--output", output,
        "--point-manifest-sha256", args.point_manifest_sha256,
        "--consumer-manifest-sha256", manifest_sha256,
        "--hypothesis", args.hypothesis, "--rate", args.rate, "--operator-confirmed",
    ], environment)


def validate_preflight(preflight, home, args):
    if (type(preflight["uid"]) is not int or preflight["uid"] <= 0
            or preflight["home"] != str(home) or preflight["machine"] != "aarch64"
            or not re.fullmatch(r"3\.10\.[0-9]+", preflight["python"])
            or type(preflight["libc"]) is not list or len(preflight["libc"]) != 2
            or preflight["libc"][0] != "glibc"
            or not re.fullmatch(r"[0-9]+\.[0-9]+", preflight["libc"][1])
            or tuple(map(int, preflight["libc"][1].split("."))) < (2, 34)
            or preflight["coreManifestSha256"] != args.core_manifest_sha256
            or preflight["replayManifestSha256"] != args.replay_manifest_sha256
            or preflight["pointManifestSha256"] != args.point_manifest_sha256
            or type(preflight["freeDiskBytes"]) is not int or preflight["freeDiskBytes"] < 1024**3):
        raise ValueError("same_preserved_ordinary_arm64_recorded_target_required")


def run(args):
    if getattr(args, "operator_confirmed", False) is not True:
        raise ValueError("fresh_stationary_supervised_estop_confirmation_required")
    output, inputs = args.output.resolve(), args.preparation.resolve(strict=True)
    if (args.user == "root" or output.exists() or output == (ROOT / ".local").resolve()
            or not output.is_relative_to((ROOT / ".local").resolve())
            or output == inputs or output in inputs.parents or inputs in output.parents
            or any(not remote.probe.digest(getattr(args, name)) for name in (
                "core_manifest_sha256", "replay_manifest_sha256", "point_manifest_sha256"))):
        raise ValueError("ordinary_user_new_private_output_and_explicit_input_identities_required")
    paths(PurePosixPath("/home/validation"), args)
    prepared, sources = preparation.evidence(inputs), source_files()
    published = binding.shared.load(ROOT / "docs/slam-point-consumer-preparation-evidence.json")
    if prepared != published:
        raise ValueError("same_independently_verified_consumer_preparation_required")
    output.mkdir(mode=0o700, parents=True)
    collection = {
        "schemaVersion": 1, "result": "running", "coordinatorSha256": sha256(__file__),
        "preparationEvidenceSha256": sha256(ROOT / "docs/slam-point-consumer-preparation-evidence.json"),
        "uploadedSourceFiles": sources, "filesSha256": {},
        "requestedReplayRate": args.rate, "requestedTimingHypothesis": args.hypothesis,
        "operatorConfirmedForThisExecution": True, "hostIdentityVerified": False, "connectionClosed": False,
    }
    client = None

    def checkpoint():
        (output / "collection-report.json").write_text(
            json.dumps(collection, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    def checked_command(label, command):
        status, text = remote.execute(client, command)
        name = "private-" + label + "-command.log"
        (output / name).write_bytes(text)
        collection["filesSha256"][name] = sha256(output / name)
        checkpoint()
        if status:
            raise RuntimeError(label + "_failed_see_preserved_private_log")
        return json.loads(text.decode("utf-8"))

    try:
        checkpoint()
        client = remote.connect(args.host, args.user, args.known_identity)
        collection["hostIdentityVerified"] = True
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(30.)
            home = PurePosixPath(sftp.normalize("."))
            values = paths(home, args)
            source, consumer, root, core, points, remote_output = values
            preflight = checked_command("preflight", clean_command(15, [
                "/usr/bin/python3", "-B", "-c", PREFLIGHT, core, root, points]))
            collection["readOnlyPreflight"] = preflight
            validate_preflight(preflight, home, args)
            for path in (root, core, points, home / ".cache", remote_output.parent):
                remote.private_directory(sftp, path, preflight["uid"])
            for path in (source, consumer, remote_output):
                remote.require_absent(sftp, path)
            sftp.mkdir(str(source), mode=0o700)
            parents = {parent for name in sources for parent in (source / name).parents
                       if parent != source and source in parent.parents}
            for parent in sorted(parents, key=lambda value: (len(value.parts), str(value))):
                sftp.mkdir(str(parent), mode=0o700)
            for name, expected in sources.items():
                if sha256(ROOT / name) != expected["sha256"]:
                    raise ValueError("point_composition_source_changed_during_upload")
                destination = source / name
                sftp.put(str(ROOT / name), str(destination), confirm=True)
                sftp.chmod(str(destination), 0o600)
                if remote.remote_digest(sftp, destination) != expected:
                    raise ValueError("identical_uploaded_point_composition_source_required")
            archive = source / "private-consumer-arm64.tar.gz"
            sftp.put(str(inputs / "point-consumer-arm64.tar.gz"), str(archive), confirm=True)
            sftp.chmod(str(archive), 0o600)
            collection["uploadedArchive"] = remote.remote_digest(sftp, archive, maximum=128 * 1024**2)
            if collection["uploadedArchive"] != {
                    "sha256": prepared["archiveSha256"], "bytes": prepared["archiveBytes"]}:
                raise ValueError("identical_uploaded_consumer_archive_required")
            collection["consumerExtraction"] = checked_command("extract", clean_command(90, [
                "/usr/bin/python3", "-B", source / "tools/extract_glim_point_consumer.py",
                "--archive", archive, "--output", consumer,
                "--archive-sha256", prepared["archiveSha256"],
                "--manifest-sha256", prepared["manifestSha256"]]))
            expected_extraction = {
                "result": extractor.RESULT, "archiveSha256": prepared["archiveSha256"],
                "manifestSha256": prepared["manifestSha256"], "bundleFiles": prepared["bundleFiles"],
                "nodeExecuted": False,
            }
            if (collection["consumerExtraction"] != expected_extraction
                    or collection["consumerExtraction"]["nodeExecuted"] is not False):
                raise ValueError("complete_unchanged_consumer_extraction_required")
            collection["nodeRuntime"] = checked_command("node", clean_command(10, [
                consumer / "node/bin/node", "-e",
                "console.log(JSON.stringify({machine:process.arch,node:process.version,platform:process.platform}))"]))
            if collection["nodeRuntime"] != {
                    "machine": "arm64", "node": "v" + binding.consumer_bundle.VERSION, "platform": "linux"}:
                raise ValueError("actual_prepared_arm64_node_runtime_required")
            print(json.dumps({"phase": "arm64_consumer_and_sources_verified",
                              "sourceFiles": len(sources)}), flush=True)
            status, text = remote.execute(client, replay_command(values, args, prepared["manifestSha256"]))
            collection["exitCode"] = status
            (output / remote.PRIVATE_COMMAND_LOG).write_bytes(text)
            collection["filesSha256"][remote.PRIVATE_COMMAND_LOG] = sha256(output / remote.PRIVATE_COMMAND_LOG)
            for name in FILES:
                try:
                    expected = remote.remote_digest(sftp, remote_output / name)
                    sftp.get(str(remote_output / name), str(output / name))
                    (output / name).chmod(0o600)
                    if sha256(output / name) != expected["sha256"]:
                        raise ValueError("identical_downloaded_recorded_point_reports_required")
                    collection["filesSha256"][name] = expected["sha256"]
                except FileNotFoundError:
                    if status == 0:
                        raise
            checkpoint()
            if status:
                raise RuntimeError("arm64_recorded_point_composition_failed_see_private_results")
            from export_glim_point_composition import validate_reports
            validate_reports(output, collection, prepared, sources)
            if source_files() != sources or preparation.evidence(inputs) != prepared:
                raise ValueError("point_composition_inputs_changed_during_execution")
            collection["result"] = RESULT
    except BaseException as error:
        collection.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        collection["connectionClosed"] = True
        checkpoint()
    return collection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity", "directory", "consumer-directory", "recorded-directory",
                 "core-directory", "point-directory", "run-name", "core-manifest-sha256",
                 "replay-manifest-sha256", "point-manifest-sha256"):
        parser.add_argument("--" + name, required=True)
    for name in ("preparation", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
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
