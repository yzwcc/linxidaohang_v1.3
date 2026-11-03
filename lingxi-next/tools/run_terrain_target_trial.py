"""Prepare/run the current cross-built terrain contracts on a robot without ROS."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
from types import SimpleNamespace

import terrain_target_probe as probe
from run_acquisition_target_trial import private_json, receive
from run_slam_target_trial import connect, target_directory

ROOT = Path(__file__).resolve().parents[1]


def prepare(args):
    import build_terrain_arm64 as builder
    options = SimpleNamespace(**vars(args))
    options.output = args.build
    report = builder.verify(options)
    output = args.output
    inputs = (args.build, args.cross_work, args.native_work, args.ground_host, args.octomap_host, args.octomap_arm64, ROOT)
    if (not output.is_absolute() or output.exists() or output.is_symlink()
            or output.parent.resolve(strict=True) != output.parent
            or any(output == path or output in path.parents or path in output.parents for path in inputs)):
        raise ValueError("new_direct_terrain_trial_outside_inputs_required")
    sources = {**report["inputs"]["sourceSha256"], **report["inputs"]["hostContractSourceSha256"]}
    sources.update({name: probe.sha256(ROOT / name) for name in probe.SOURCES + ("tools/run_terrain_target_trial.py",)})
    contracts = probe.contract_ids(probe.load_contracts(ROOT))
    if len(contracts) != 21:
        raise ValueError("exact21_current_terrain_contracts_required")
    members = {name: ROOT / name for name in probe.SOURCES}
    members.update({"native/" + name: args.build / "build" / name for name in probe.LIBRARIES + probe.NOTICES})
    members.update({"native/lib/" + name: args.build / "build/lib" / name for name in probe.DEPENDENCIES})
    output.mkdir(mode=0o700)
    files = {}
    for name, source in members.items():
        target = output / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o600)
        files[name] = dict(bytes=target.stat().st_size, sha256=probe.sha256(target))
    for name, target in probe.aliases().items():
        (output / name).symlink_to(target)
    for item in report["artifacts"]:
        name = "native/" + item["file"]
        if files[name]["sha256"] != item["sha256"]:
            raise ValueError("same_cross_built_terrain_libraries_required")
    value = dict(schemaVersion=1, purpose="synthetic_arm64_terrain_trial", platform="linux-aarch64",
        inputClassification="synthetic_fixture", buildReportSha256=probe.sha256(args.build / "build-report.json"),
        sourceSha256=sources, contracts=contracts, files=files, symlinks=probe.aliases(), boundaries=dict(probe.BOUNDARIES))
    private_json(output / probe.MANIFEST, value)
    probe.verify_bundle(output)
    if builder.verify(options) != report or any(probe.sha256(ROOT / name) != identity for name, identity in sources.items()):
        raise ValueError("unchanged_terrain_trial_preparation_inputs_required")
    return dict(result="arm64_terrain_trial_prepared_not_executed", files=len(files), contracts=len(contracts),
        trialManifestSha256=probe.sha256(output / probe.MANIFEST), boundaries=dict(probe.BOUNDARIES))


def command(remote):
    return shlex.join(["timeout", "--signal=TERM", "--kill-after=5s", "120s", "env", "-i",
        "PATH=/usr/bin:/bin", "HOME=" + str(remote.parent), "LANG=C.UTF-8",
        "LD_LIBRARY_PATH=" + str(remote / "native/lib"), "OMP_NUM_THREADS=2", "OPENBLAS_NUM_THREADS=2",
        "python3", "-I", "-B", str(remote / "tools/terrain_target_probe.py"), "--root", str(remote)])


def validate_result(value, manifest, identity):
    probe.validate_result(value, manifest, identity)


def run(args):
    bundle, output = args.bundle, args.output
    manifest = probe.verify_bundle(bundle)
    identity = probe.sha256(bundle / probe.MANIFEST)
    if (args.user == "root" or output.exists() or output.is_symlink() or not output.is_absolute()
            or output.parent.resolve(strict=True) != output.parent or output == bundle or bundle in output.parents
            or args.manifest_sha256 != identity
            or any(probe.sha256(ROOT / name) != expected for name, expected in manifest["sourceSha256"].items())):
        raise ValueError("current_source_bound_trial_and_new_external_private_report_required")
    client, report = None, dict(result="running", trialManifestSha256=identity)
    try:
        client = connect(args.host, args.user, args.known_identity)
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(30)
            remote = target_directory(sftp.normalize("."), args.directory)
            sftp.mkdir(str(remote), mode=0o700)
            report["privateRemoteDirectory"] = str(remote)
            folders = sorted({str(parent) for name in manifest["files"] for parent in PurePosixPath(name).parents if str(parent) != "."},
                key=lambda name: (len(PurePosixPath(name).parts), name))
            for name in folders:
                sftp.mkdir(str(remote / name), mode=0o700)
            for name in sorted(manifest["files"]) + [probe.MANIFEST]:
                sftp.put(str(bundle / name), str(remote / name), confirm=True)
                sftp.chmod(str(remote / name), 0o600)
            for name, target in manifest["symlinks"].items():
                sftp.symlink(target, str(remote / name))
        with client.get_transport().open_session(timeout=8) as channel:
            channel.exec_command(command(remote))
            status, data, errors = receive(channel)
        report.update(probeExitCode=status, privateOutputSha256=hashlib.sha256(data + errors).hexdigest())
        if status or errors:
            report["privateProbeOutput"] = (data + errors).decode("utf-8", errors="replace")
            raise RuntimeError("arm64_terrain_probe_failed_see_private_report")
        value = json.loads(data)
        validate_result(value, manifest, identity)
        if (probe.verify_bundle(bundle) != manifest or probe.sha256(bundle / probe.MANIFEST) != identity
                or any(probe.sha256(ROOT / name) != expected for name, expected in manifest["sourceSha256"].items())):
            raise ValueError("terrain_trial_changed_during_target_execution")
        report.update(result=probe.RESULT, targetProbe=value)
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        private_json(output, report)
    return dict(result=report["result"], executedContracts=len(report["targetProbe"]["executedContracts"]),
        trialManifestSha256=identity)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepared = commands.add_parser("prepare")
    for name in ("build", "cross-work", "native-work", "ground-host", "octomap-host", "octomap-arm64", "host-check", "output"):
        prepared.add_argument("--" + name, type=lambda value: Path(value).absolute(), required=True)
    executed = commands.add_parser("run")
    for name in ("host", "user", "known-identity", "directory", "manifest-sha256"):
        executed.add_argument("--" + name, required=True)
    for name in ("bundle", "output"):
        executed.add_argument("--" + name, type=lambda value: Path(value).absolute(), required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps({"prepare": prepare, "run": run}[args.command](args), allow_nan=False))
    finally:
        os.umask(previous)
