"""Identity-checked LAN execution of isolated ARM64 point loading/contracts only."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat

import glim_point_target_probe as probe
from run_glim_target_preprocess import execute
from run_slam_target_trial import connect, target_directory
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
RESULT = "arm64_point_loading_contracts_collected_not_recorded_or_live_acceptance"
FILES = ("report.json", *("private-contract-" + name + ".log" for name in probe.SCENARIOS))
PRIVATE_COMMAND_LOG = "private-command-output.log"
PREFLIGHT = """import hashlib,json,os,platform,shutil,sys
from pathlib import Path
core=Path(sys.argv[1])
manifest=core/'bundle-manifest.json'
if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size>1048576:
    raise ValueError('bounded_direct_core_manifest_required')
print(json.dumps({
    'uid':os.geteuid(),'home':str(Path.home()),'machine':platform.machine(),
    'python':platform.python_version(),'libc':list(platform.libc_ver()),
    'freeDiskBytes':shutil.disk_usage(Path.home()).free,
    'coreManifestSha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
    'productNodeExists':Path('/opt/lingxi-next/current/runtime/node/bin/node').is_file(),
    'systemNodeExists':Path('/usr/bin/node').is_file()
}))
"""


def paths(home, args):
    bundle, core = (target_directory(str(home), name)
                    for name in (args.directory, args.core_directory))
    if (bundle == core or not re.fullmatch(r"glim-point-target-[a-z0-9-]+", args.run_name)):
        raise ValueError("separate_home_scoped_point_probe_paths_required")
    return bundle, core, home / ".cache/lingxi-next" / args.run_name


def command(bundle, core, output):
    return shlex.join([
        "timeout", "--signal=TERM", "--kill-after=5s", "120s", "/usr/bin/nice", "-n", "10",
        "env", "-i", *(key + "=" + value for key, value in probe.environment(bundle, core).items()),
        "/usr/bin/python3", "-B", str(bundle / "tools/glim_point_target_probe.py"),
        "--bundle", str(bundle), "--core-root", str(core), "--output", str(output),
    ])


def remote_digest(sftp, path, maximum=8 * 1024 * 1024):
    info = sftp.lstat(str(path))
    if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= maximum:
        raise ValueError("bounded_direct_private_point_file_required")
    digest, size = hashlib.sha256(), 0
    with sftp.open(str(path), "rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            size += len(block)
            if size > maximum:
                raise ValueError("remote_point_file_grew_beyond_bound")
            digest.update(block)
    if size != info.st_size:
        raise ValueError("remote_point_file_size_changed")
    return {"sha256": digest.hexdigest(), "bytes": size}


def private_directory(sftp, path, uid):
    info = sftp.lstat(str(path))
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077
            or sftp.normalize(str(path)) != str(path)):
        raise ValueError("owned_direct_private_target_directory_required")


def require_absent(sftp, path):
    try:
        sftp.lstat(str(path))
    except FileNotFoundError:
        return
    raise ValueError("existing_remote_point_output_is_preserved")


def validate_report(report, manifest, manifest_hash, core_manifest_hash):
    target = report["target"]
    libraries = {**manifest["expectedCoreLibrariesSha256"],
                 probe.LIBRARY: manifest["files"]["lib/" + probe.LIBRARY]["sha256"]}
    expected_stages = [{
        "scenario": name, "exitCode": 0, "forcedTermination": False,
        "processAlive": False, "timedOut": False,
    } for name in probe.SCENARIOS]
    if (type(report["schemaVersion"]) is not int or report["schemaVersion"] != 1
            or report["result"] != probe.RESULT
            or report["inputClassification"] != "isolated_synthetic_contracts"
            or report["bundleManifestSha256"] != manifest_hash
            or report["coreManifestSha256"] != core_manifest_hash
            or target["machine"] != "aarch64"
            or not re.fullmatch(r"3\.10\.[0-9]+", target["python"])
            or type(target["libc"]) is not list or len(target["libc"]) != 2
            or target["libc"][0] != "glibc"
            or not re.fullmatch(r"[0-9]+\.[0-9]+", target["libc"][1])
            or tuple(map(int, target["libc"][1].split("."))) < (2, 34)
            or report["actualLoadedLibrariesSha256"] != libraries
            or report["emptyAbi"] != {"open": 1, "close": 1, "callbacks": 0, "copiedPackets": 0}
            or any(type(value) is not int for value in report["emptyAbi"].values())
            or type(report["nativeContractExecutionsOnTarget"]) is not int
            or report["nativeContractExecutionsOnTarget"] != len(probe.SCENARIOS)
            or report["stages"] != expected_stages
            or any(type(stage["exitCode"]) is not int
                   or any(stage[name] is not False for name in ("forcedTermination", "processAlive", "timedOut"))
                   for stage in report["stages"])
            or set(report["boundaries"]) != set(probe.BOUNDARIES)
            or any(value is not False for value in report["boundaries"].values())):
        raise ValueError("complete_actual_arm64_point_synthetic_contracts_required")


def run(args):
    if getattr(args, "operator_confirmed", False) is not True:
        raise ValueError("fresh_stationary_supervised_estop_confirmation_required")
    output = args.output.resolve()
    if (args.user == "root" or output.exists() or output == (ROOT / ".local").resolve()
            or not output.is_relative_to((ROOT / ".local").resolve())
            or not probe.digest(args.core_manifest_sha256)):
        raise ValueError("ordinary_user_new_private_output_and_explicit_core_identity_required")
    paths(PurePosixPath("/home/validation"), args)
    bundle = args.bundle.resolve(strict=True)
    if output == bundle or output in bundle.parents or bundle in output.parents:
        raise ValueError("private_point_result_must_not_overlap_frozen_bundle")
    manifest = probe.verify_bundle(bundle, args.manifest_sha256)
    for name in probe.MEMBERS[2:]:
        if sha256(ROOT / name) != manifest["files"][name]["sha256"]:
            raise ValueError("same_prepared_and_current_probe_source_required")
    output.mkdir(mode=0o700, parents=True)
    collection = {
        "schemaVersion": 1, "result": "running", "coordinatorSha256": sha256(__file__),
        "bundleManifestSha256": args.manifest_sha256,
        "coreManifestSha256": args.core_manifest_sha256,
        "operatorConfirmedForThisExecution": True, "hostIdentityVerified": False,
        "connectionClosed": False, "uploadedFiles": {}, "filesSha256": {},
    }
    client = None

    def checkpoint():
        (output / "collection-report.json").write_text(
            json.dumps(collection, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    try:
        checkpoint()
        client = connect(args.host, args.user, args.known_identity)
        collection["hostIdentityVerified"] = True
        with client.open_sftp() as sftp:
            home = PurePosixPath(sftp.normalize("."))
            remote, core, remote_output = paths(home, args)
            status, text = execute(client, shlex.join([
                "env", "-i", "PATH=/usr/bin:/bin", "LANG=C.UTF-8",
                "/usr/bin/python3", "-B", "-c", PREFLIGHT, str(core),
            ]))
            if status != 0:
                raise RuntimeError("read_only_point_target_preflight_failed")
            preflight = json.loads(text.decode("utf-8"))
            collection["readOnlyPreflight"] = preflight
            if (type(preflight["uid"]) is not int or preflight["uid"] <= 0
                    or preflight["home"] != str(home) or preflight["machine"] != "aarch64"
                    or not re.fullmatch(r"3\.10\.[0-9]+", preflight["python"])
                    or type(preflight["libc"]) is not list or len(preflight["libc"]) != 2
                    or preflight["libc"][0] != "glibc"
                    or not re.fullmatch(r"[0-9]+\.[0-9]+", preflight["libc"][1])
                    or tuple(map(int, preflight["libc"][1].split("."))) < (2, 34)
                    or preflight["coreManifestSha256"] != args.core_manifest_sha256
                    or preflight["freeDiskBytes"] < 64 * 1024 * 1024):
                raise ValueError("same_ordinary_arm64_target_and_preserved_core_required")
            for path in (core, home / ".cache", remote_output.parent):
                private_directory(sftp, path, preflight["uid"])
            for path in (remote, remote_output):
                require_absent(sftp, path)
            sftp.mkdir(str(remote), mode=0o700)
            for name in ("lib", "bin", "tools"):
                sftp.mkdir(str(remote / name), mode=0o700)
            entries = {**manifest["files"], "point-manifest.json": {
                "sha256": args.manifest_sha256, "bytes": (bundle / "point-manifest.json").stat().st_size,
                "executable": False,
            }}
            for name, expected in entries.items():
                if sha256(bundle / name) != expected["sha256"]:
                    raise ValueError("point_source_changed_during_upload")
                destination = remote / name
                sftp.put(str(bundle / name), str(destination), confirm=True)
                mode = 0o700 if expected["executable"] else 0o600
                sftp.chmod(str(destination), mode)
                observed = remote_digest(sftp, destination)
                if observed != {key: expected[key] for key in ("sha256", "bytes")}:
                    raise ValueError("identical_uploaded_point_bundle_required")
                collection["uploadedFiles"][name] = {**observed, "mode": mode}
            checkpoint()
            print(json.dumps({"phase": "point_contract_bundle_verified", "files": len(entries)}), flush=True)
            text_command = (command(remote, core, remote_output) + " " + shlex.join([
                "--manifest-sha256", args.manifest_sha256, "--operator-confirmed",
            ]))
            status, text = execute(client, text_command)
            collection["exitCode"] = status
            (output / PRIVATE_COMMAND_LOG).write_bytes(text)
            collection["filesSha256"][PRIVATE_COMMAND_LOG] = sha256(output / PRIVATE_COMMAND_LOG)
            for name in FILES:
                try:
                    expected = remote_digest(sftp, remote_output / name, maximum=1024 * 1024)
                    sftp.get(str(remote_output / name), str(output / name))
                    (output / name).chmod(0o600)
                    if sha256(output / name) != expected["sha256"]:
                        raise ValueError("identical_downloaded_point_result_required")
                    collection["filesSha256"][name] = expected["sha256"]
                except FileNotFoundError:
                    if status == 0:
                        raise
            checkpoint()
            if status != 0:
                raise RuntimeError("target_point_contract_failed_see_preserved_private_results")
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            validate_report(report, manifest, args.manifest_sha256, args.core_manifest_sha256)
            if probe.verify_bundle(bundle, args.manifest_sha256) != manifest:
                raise ValueError("point_bundle_changed_during_execution")
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
    for name in ("host", "user", "known-identity", "directory", "core-directory",
                 "run-name", "manifest-sha256", "core-manifest-sha256"):
        parser.add_argument("--" + name, required=True)
    for name in ("bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--operator-confirmed", action="store_true")
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(parser.parse_args())["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
