"""Collect an isolated ARM64 recorded localization check over verified LAN SSH."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import stat
import subprocess
import sys
import venv
import zipfile

import run_glim_point_target as remote
from verify_slam_capture import sha256

ROOT = Path(__file__).resolve().parents[1]
WHEEL = "small_gicp-1.0.1-cp310-cp310-manylinux_2_26_aarch64.manylinux_2_28_aarch64.whl"
WHEEL_SHA256 = "0a68868802b1a37dd945beb9b941678a294ab2a775e3d4887bb6d010c11ca544"
RESULT = "arm64_recorded_localization_https_collected_not_live_or_navigation_acceptance"
FILES = (
    "check-report.json", "private-launcher.log", "run/owner-report.json",
    "run/private-api.log", "run/native/private-child.log", "run/native/child-report.json",
    "run/native/localization-final.json",
)


def source_files():
    from run_glim_supervised_target import source_files as frozen_sources
    names = set(frozen_sources())
    for folder, pattern in (("robot-runtime/python", "*.py"),
                            ("robot-slam/lingxi_slam", "*.py"), ("docs", "*.json")):
        names.update(file.relative_to(ROOT).as_posix() for file in (ROOT / folder).glob(pattern))
    result = {name: ROOT / name for name in sorted(names)}
    if any(file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(ROOT)
           for file in result.values()):
        raise ValueError("direct_localization_source_files_required")
    return result


def read_remote_report(sftp, file):
    expected = remote.remote_digest(sftp, file)
    with sftp.open(str(file), "rb") as stream:
        payload = stream.read(8 * 1024**2 + 1)
    import hashlib
    if len(payload) != expected["bytes"] or hashlib.sha256(payload).hexdigest() != expected["sha256"]:
        raise ValueError("identical_bounded_remote_localization_report_required")
    return json.loads(payload)


def inventory(directory):
    """Only ordinary bounded files from an explicit private input tree."""
    result = {}
    for file in sorted(directory.rglob("*")):
        if file.is_symlink() or not (file.is_file() or file.is_dir()):
            raise ValueError("direct_localization_trial_inputs_required")
        if file.is_file():
            size = file.stat().st_size
            if size > 64 * 1024**2 or len(result) >= 4096:
                raise ValueError("bounded_localization_trial_inputs_required")
            result[file.relative_to(directory).as_posix()] = {"sha256": sha256(file), "bytes": size}
    if sum(entry["bytes"] for entry in result.values()) > 64 * 1024**2:
        raise ValueError("bounded_localization_trial_input_tree_required")
    return result


def install_wheel(wheel, environment):
    if wheel.name != WHEEL or sha256(wheel) != WHEEL_SHA256 or environment.exists():
        raise ValueError("new_isolated_environment_and_pinned_arm64_wheel_required")
    with zipfile.ZipFile(wheel) as archive:
        entries = archive.infolist()
        if (not 1 <= len(entries) <= 64 or len({entry.filename for entry in entries}) != len(entries)
                or sum(entry.file_size for entry in entries) > 16 * 1024**2):
            raise ValueError("bounded_direct_small_gicp_wheel_required")
        for entry in entries:
            name = PurePosixPath(entry.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in entry.filename
                    or name.as_posix() != entry.filename.rstrip("/")
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or not (name.parts[0].startswith("small_gicp.") or
                            name.parts[0] in ("small_gicp-1.0.1.dist-info", "small_gicp.libs"))):
                raise ValueError("only_pinned_small_gicp_members_required")
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(environment)
        destination = environment / "lib/python3.10/site-packages"
        for entry in entries:
            file = destination / entry.filename
            if entry.is_dir():
                file.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with file.open("xb") as stream:
                    stream.write(archive.read(entry))
                file.chmod(0o600)


def target_prepare(plan_file):
    if sys.platform != "linux" or platform.machine() != "aarch64" or os.geteuid() == 0:
        raise ValueError("ordinary_arm64_target_required")
    plan = json.loads(plan_file.read_text())
    source, consumer = Path(plan["source"]), Path(plan["consumer"])
    if source != ROOT or source.resolve(strict=True) != source:
        raise ValueError("same_uploaded_localization_source_required")
    from prepare_glim_point_consumer import verify_bundle
    manifest = verify_bundle(consumer, plan["consumerManifestSha256"])
    if manifest["files"]["node/bin/node"]["sha256"] != plan["nodeSha256"]:
        raise ValueError("same_previously_verified_arm64_node_required")
    for name, entry in plan["uploadedFiles"].items():
        file = source / name
        if (file.is_symlink() or not file.resolve(strict=True).is_relative_to(source)
                or file.stat().st_size != entry["bytes"] or sha256(file) != entry["sha256"]):
            raise ValueError("same_uploaded_localization_trial_bytes_required")
    # Resolve ESM dependencies through the separately verified, unchanged bundle.
    (source / "node_modules").symlink_to(consumer / "node_modules", target_is_directory=True)
    install_wheel(source / WHEEL, source / "env")
    python = source / "env/bin/python"
    code = ("import json,sys,numpy,scipy,small_gicp;"
            "from importlib.metadata import version;"
            "print(json.dumps(dict(python=sys.version.split()[0],numpy=numpy.__version__,"
            "scipy=scipy.__version__,smallGicp=version('small-gicp'),"
            "extension=small_gicp.__file__)))")
    versions = json.loads(subprocess.check_output([str(python), "-B", "-c", code], timeout=15))
    if versions["smallGicp"] != "1.0.1" or not versions["python"].startswith("3.10."):
        raise ValueError("matching_isolated_arm64_localization_dependencies_required")
    from mapping_platform import MappingPlatform, configuration
    binding = MappingPlatform(configuration(source / "private-config.json")[0])
    from localization_control import load_map
    load_map(source / "maps/saved-map", plan["mapId"], plan["receiptSha256"], binding.profile,
             plan["profileSha256"], session="0" * 32, input_mode="recorded",
             sensor_provenance=binding.sensor_provenance())
    report = {"result": "isolated_arm64_localization_prepared", "versions": versions,
            "smallGicpExtensionSha256": sha256(Path(versions["extension"])),
            "frozenInputs": binding.frozen}
    from mapping_service import private_json
    private_json(source / "private-prepare-report.json", report)
    return report


def command(argv, environment, seconds):
    return shlex.join([
        "timeout", "--signal=INT", "--kill-after=10s", str(seconds) + "s",
        "/usr/bin/nice", "-n", "10", "env", "-i",
        *(key + "=" + value for key, value in environment.items()), *map(str, argv),
    ])


def collect(args):
    output = args.output.resolve()
    if (args.user == "root" or output.exists() or output == ROOT / ".local"
            or not output.is_relative_to((ROOT / ".local").resolve())):
        raise ValueError("ordinary_user_and_new_private_collection_required")
    baseline = json.loads((ROOT / "docs/slam-managed-localization-evidence.json").read_text())
    arm = json.loads((ROOT / "docs/slam-managed-mapping-runtime-preparation-evidence.json").read_text())
    point = json.loads((ROOT / "docs/slam-point-arm64-target-evidence.json").read_text())
    receipt = args.saved_map / "map-ready.json"
    if (sha256(receipt) != baseline["receiptSha256"]
            or sha256(args.seed_sample) != baseline["seedSampleSha256"]
            or sha256(args.api_bundle) != baseline["apiBundleSha256"]
            or sha256(args.wheel) != WHEEL_SHA256 or args.wheel.name != WHEEL):
        raise ValueError("explicit_preserved_map_seed_api_and_pinned_wheel_required")
    sources = source_files()
    sources.update({"maps/saved-map/" + name: args.saved_map / name for name in inventory(args.saved_map)})
    sources.update({WHEEL: args.wheel, "api.mjs": args.api_bundle, "private-seed.npz": args.seed_sample})
    files = {name: {"sha256": sha256(file), "bytes": file.stat().st_size} for name, file in sources.items()}
    if any(file.is_symlink() or not file.is_file() for file in sources.values()):
        raise ValueError("direct_localization_upload_files_required")
    output.mkdir(mode=0o700, parents=True)
    report = {"result": "running", "hostIdentityVerified": False, "robotContacted": False,
              "motionCommandsSent": False, "localizationReady": False, "navigationReady": False,
              "mapProfileAssociationVerified": False, "uploadedFiles": files, "filesSha256": {},
              "mapOrigin": "preserved_x64_saved_map_copied_without_native_arm64_reload",
              "coordinatorSha256": sha256(__file__), "connectionClosed": False}
    client = None

    def checkpoint():
        (output / "collection-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    try:
        checkpoint()
        client = remote.connect(args.host, args.user, args.known_identity)
        report.update(hostIdentityVerified=True, robotContacted=True)
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(30.)
            home = PurePosixPath(sftp.normalize("."))
            source, recorded, core, mapper, points, consumer = (
                remote.target_directory(str(home), getattr(args, name)) for name in (
                    "directory", "recorded_directory", "core_directory", "mapping_directory",
                    "point_directory", "consumer_directory"))
            if len({source, recorded, core, mapper, points, consumer}) != 6:
                raise ValueError("separate_home_scoped_localization_trial_inputs_required")
            remote.require_absent(sftp, source)
            # Short output path leaves room for both private Unix control sockets.
            result = home / ".cache/lingxi-next" / args.run_name
            if (not re.fullmatch(r"la-[a-z0-9-]{1,20}", args.run_name)
                    or len(os.fsencode(result / "run/native/localization.sock")) >= 100):
                raise ValueError("bounded_new_private_localization_result_path_required")
            remote.require_absent(sftp, result)
            uid = sftp.stat(str(home)).st_uid
            if uid <= 0:
                raise ValueError("ordinary_target_owner_required")
            for directory in (recorded, core, mapper, points, consumer, result.parent):
                remote.private_directory(sftp, directory, uid)
            config = {
                "platform": "arm64-humble", "inputMode": "recorded", "root": str(recorded),
                "coreRoot": str(core), "mappingRoot": str(mapper), "pointAdapter": str(points),
                "pointManifestSha256": point["bundleManifestSha256"],
                "reference": str(recorded / "result-live"), "hypothesis": "header_is_scan_start", "rate": 1.,
            }
            plan = {
                "source": str(source), "consumer": str(consumer), "uploadedFiles": files,
                "consumerManifestSha256": arm["consumerManifestSha256"], "nodeSha256": arm["nodeBinarySha256"],
                "mapId": "slam-" + json.loads(receipt.read_text())["session"],
                "receiptSha256": baseline["receiptSha256"], "profileSha256": baseline["profileSha256"],
            }
            sftp.mkdir(str(source), mode=0o700)
            parents = {parent for name in files for parent in (source / name).parents
                       if parent != source and source in parent.parents}
            for parent in sorted(parents, key=lambda path: (len(path.parts), str(path))):
                sftp.mkdir(str(parent), mode=0o700)
            for index, (name, file) in enumerate(sources.items(), 1):
                destination = source / name
                if sha256(file) != files[name]["sha256"]:
                    raise ValueError("localization_source_changed_before_upload")
                sftp.put(str(file), str(destination), confirm=True)
                sftp.chmod(str(destination), 0o600)
                if remote.remote_digest(sftp, destination, maximum=64 * 1024**2) != files[name]:
                    raise ValueError("identical_uploaded_localization_inputs_required")
                if index % 50 == 0:
                    print(json.dumps({"phase": "upload_verified", "files": index, "total": len(files)}), flush=True)
            for name, value in (("private-config.json", config), ("private-plan.json", plan)):
                with sftp.open(str(source / name), "wx") as stream:
                    stream.write(json.dumps(value, indent=2) + "\n")
                sftp.chmod(str(source / name), 0o600)
            # These paths are remote; do not resolve them on the Windows collector.
            environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                "AMENT_PREFIX_PATH": "/opt/ros/humble",
                "PYTHONPATH": "/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
                "LD_LIBRARY_PATH": ":".join(map(str, (recorded / "lib", core / "lib",
                    PurePosixPath("/opt/ros/humble/lib/aarch64-linux-gnu"), PurePosixPath("/opt/ros/humble/lib")))),
                "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
            for label, argv, seconds in (
                ("prepare", ["/usr/bin/python3", "-B", source / "tools/run_localization_arm64_trial.py",
                             "target-prepare", "--plan", source / "private-plan.json"], 90),
                ("check", [source / "env/bin/python", "-B", source / "tools/check_managed_localization_runtime.py",
                    "--config", source / "private-config.json", "--saved-map", source / "maps/saved-map",
                    "--map-id", plan["mapId"], "--receipt-sha256", plan["receiptSha256"],
                    "--profile-sha256", plan["profileSha256"], "--seed-sample", source / "private-seed.npz",
                    "--seed-sha256", baseline["seedSampleSha256"], "--node", consumer / "node/bin/node",
                    "--node-sha256", plan["nodeSha256"], "--api-bundle", source / "api.mjs",
                    "--api-sha256", baseline["apiBundleSha256"], "--output", result], 150),
            ):
                status, text = remote.execute(client, command(argv, environment, seconds))
                log = output / ("private-" + label + "-command.log")
                log.write_bytes(text)
                report["filesSha256"][log.name] = sha256(log)
                report[label + "ExitCode"] = status
                if status:
                    report["result"] = "failed"
                    break
                if label == "prepare":
                    report[label] = read_remote_report(sftp, source / "private-prepare-report.json")
                    if report[label]["result"] != "isolated_arm64_localization_prepared":
                        raise ValueError("verified_localization_preparation_report_required")
                print(json.dumps({"phase": label + "_completed"}), flush=True)
            for name in FILES:
                try:
                    expected = remote.remote_digest(sftp, result / name)
                except FileNotFoundError:
                    continue
                file = output / name
                file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                sftp.get(str(result / name), str(file))
                if sha256(file) != expected["sha256"]:
                    raise ValueError("identical_collected_localization_reports_required")
                report["filesSha256"][name] = expected["sha256"]
            if report["result"] == "failed":
                raise RuntimeError("arm64_localization_check_failed_see_private_reports")
            check = json.loads((output / "check-report.json").read_text())
            owner = json.loads((output / "run/owner-report.json").read_text())
            child = json.loads((output / "run/native/child-report.json").read_text())
            import run_glim_supervised_replay as replay
            replay.complete_child(child, report["prepare"]["frozenInputs"]["journal"])
            if (check["result"] != "managed_recorded_localization_https_checked"
                    or owner["result"] != "completed" or child["machine"] != "aarch64"
                    or child["versions"]["decoder"] != "installed_humble_rclpy"
                    or not all(check[name] is True for name in (
                        "pauseConfirmed", "stopConfirmed", "staleGenerationRejected", "singleNativeWorker"))
                    or any(check[name] is not False for name in (
                        "motionCommandsSent", "localizationReady", "navigationReady", "mapProfileAssociationVerified"))
                    or any(sha256(file) != files[name]["sha256"] for name, file in sources.items())):
                raise ValueError("complete_same_input_arm64_localization_check_required")
            report.update(result=RESULT, checkSummary=check)
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        report["connectionClosed"] = True
        checkpoint()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("target-prepare")
    prepare.add_argument("--plan", type=Path, required=True)
    run = commands.add_parser("collect")
    for name in ("host", "user", "known-identity", "directory", "recorded-directory", "core-directory",
                 "mapping-directory", "point-directory", "consumer-directory", "run-name"):
        run.add_argument("--" + name, required=True)
    for name in ("saved-map", "api-bundle", "seed-sample", "wheel", "output"):
        run.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "robot-runtime/python"))
    previous = os.umask(0o077)
    try:
        result = target_prepare(args.plan) if args.command == "target-prepare" else collect(args)
        print(json.dumps(result if args.command == "target-prepare" else {"result": result["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
