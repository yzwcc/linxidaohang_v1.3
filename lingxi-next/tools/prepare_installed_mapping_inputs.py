"""Materialize verified ARM64 inputs, never load mapping libraries or start ROS."""
import argparse
import copy
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
from async_mapping_bundle import CORE_LIBRARIES, digest, direct, sha256

MANIFEST = "native-inputs-manifest.json"
PURPOSE = "installed_live_arm64_mapping_inputs_not_target_acceptance"
COMPONENTS = {"root": ("odometry", "replay-manifest.json"),
              "coreRoot": ("core", "bundle-manifest.json"),
              "mappingRoot": ("mapping", "replay-manifest.json"),
              "pointAdapter": ("points", "point-manifest.json"),
              "fullMapping": ("full", "async-mapping-manifest.json")}
BOUNDARIES = dict.fromkeys(("mappingLibrariesLoaded", "ddsProfileValidated", "rosNodesCreated",
    "sensorsSubscribed", "motionCommandsSent", "vendorFilesChanged", "physicalCalibrationAccepted",
    "navigationReady", "productAccepted"), False)
MAX_FILES, MAX_BYTES = 512, 1024**3


def read_json(path, maximum=2_000_000):
    direct(path)
    if not 0 < path.stat().st_size <= maximum:
        raise ValueError("bounded_native_input_metadata_required")
    return json.loads(path.read_text(encoding="utf-8"))


def roots(value):
    return {name: Path(value[name]["root"] if name == "fullMapping" else value[name])
            for name in COMPONENTS}


def rewritten(value, destination):
    result = copy.deepcopy(value)
    for name, (directory, _) in COMPONENTS.items():
        if name == "fullMapping":
            result[name]["root"] = str(destination / directory)
        else:
            result[name] = str(destination / directory)
    return result


def inspect_configuration(path):
    """Run existing file/receipt verifiers only; DDS and target execution stay pending."""
    from mapping_platform import configuration
    from glim_live_session import PURPOSE as LIVE_PURPOSE
    from glim_target_controlled_mapping import PURPOSE as MAPPING_PURPOSE, configuration_files, verify_control
    from glim_target_replay import verify_replay_bundle
    from slam_target_probe import verify_bundle as verify_core
    from glim_point_target_probe import verify_bundle as verify_points, LIBRARY
    from async_mapping_bundle import verify_bundle as verify_full
    from run_glim_offline_trial import explicit_diagnostic_profile

    value, _ = configuration(path)
    if (value.get("platform") != "arm64-humble" or value.get("inputMode") != "live"
            or value.get("fullMapping", {}).get("captureObservations") is not True):
        raise ValueError("explicit_live_arm64_observed_mapper_required")
    selected = roots(value)
    core = verify_core(selected["coreRoot"])
    live = verify_replay_bundle(selected["root"], LIVE_PURPOSE)
    mapper = verify_replay_bundle(selected["mappingRoot"], MAPPING_PURPOSE)
    for name in ("root", "mappingRoot"):
        verify_control(selected[name])
    core_hash = sha256(selected["coreRoot"] / "bundle-manifest.json")
    baseline = read_json(ROOT / "docs/slam-humble-live-evidence.json")
    points = verify_points(selected["pointAdapter"], value["pointManifestSha256"])
    loading = read_json(ROOT / "docs/slam-point-arm64-target-evidence.json")
    if (sha256(selected["root"] / "replay-manifest.json") != baseline["replayManifestSha256"]
            or core_hash != baseline["coreBundleManifestSha256"]
            or live["coreBundleManifestSha256"] != core_hash or mapper["coreBundleManifestSha256"] != core_hash
            or configuration_files(selected["root"] / "config") != configuration_files(selected["mappingRoot"] / "config")
            or mapper["odometryAdapterSha256"] != sha256(selected["root"] / "lib/liblingxi_glim_offline.so")
            or mapper["mappingAdapterSha256"] != sha256(selected["mappingRoot"] / "lib/liblingxi_glim_mapping.so")
            or loading["bundleManifestSha256"] != value["pointManifestSha256"]
            or loading["coreManifestSha256"] != core_hash
            or loading["actualLoadedLibrariesSha256"][LIBRARY] != points["files"]["lib/" + LIBRARY]["sha256"]
            or any(core["files"].get("lib/" + name, {}).get("sha256") != identity
                   for name, identity in points["expectedCoreLibrariesSha256"].items())):
        raise ValueError("same_verified_live_mapping_point_and_core_inputs_required")
    explicit_diagnostic_profile(read_json(selected["root"] / "private-profile.json"),
        read_json(selected["root"] / "private-sensor-diagnostic.json"), value["hypothesis"])
    full = verify_full(selected["fullMapping"], value["fullMapping"]["manifestSha256"],
        baseline=selected["root"], core_manifest=core_hash,
        core_libraries={name: sha256(selected["coreRoot"] / "lib" / name) for name in CORE_LIBRARIES},
        platform="linux-aarch64", observed=True)
    return value, {"root": live, "coreRoot": core, "mappingRoot": mapper,
                   "pointAdapter": points, "fullMapping": full}, baseline["runs"]["start"]["ddsProfileSha256"]


def fingerprint(path):
    direct(path)
    return {"bytes": path.stat().st_size, "sha256": sha256(path),
            "executable": bool(path.stat().st_mode & 0o111)}


def members(value, manifests):
    result = {}
    for name, source in roots(value).items():
        directory, receipt = COMPONENTS[name]
        declared = manifests[name]["files"]
        for member in (*declared, receipt):
            relative = PurePosixPath(member)
            if (not member or relative.is_absolute() or str(relative) != member or ".." in relative.parts
                    or "\\" in member or relative.suffix.lower() in (".bag", ".db3", ".mcap", ".npz", ".npy", ".pcd", ".ply")):
                raise ValueError("declared_native_metadata_not_recordings_or_maps_required")
            path = source / member
            info = fingerprint(path)
            if member in declared and (info["bytes"] != declared[member]["bytes"]
                    or info["sha256"] != declared[member]["sha256"]):
                raise ValueError("unchanged_declared_native_input_required")
            result[directory + "/" + member] = (path, info)
    if (len(result) > MAX_FILES or any(not 0 < info["bytes"] <= 256 * 1024**2 for _, info in result.values())
            or sum(info["bytes"] for _, info in result.values()) > MAX_BYTES):
        raise ValueError("bounded_native_input_closure_required")
    return result


def private_directory(path):
    direct(path, directory=True)
    info = path.stat()
    if info.st_mode & 0o077 or sys.platform == "linux" and info.st_uid != os.geteuid():
        raise ValueError("owned_private_native_input_directory_required")


def tree(root, files):
    expected_dirs = {str(parent) for name in files for parent in PurePosixPath(name).parents if str(parent) != "."}
    seen, directories = set(), set()
    for path in root.rglob("*"):
        info = path.lstat()
        name = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            private_directory(path)
            directories.add(name)
        elif stat.S_ISREG(info.st_mode):
            if name == MANIFEST:
                continue
            if info.st_mode & 0o077 or name not in files or fingerprint(path) != files[name]:
                raise ValueError("exact_private_native_input_members_required")
            seen.add(name)
        else:
            raise ValueError("direct_native_input_files_and_directories_required")
    if seen != set(files) or directories != expected_dirs:
        raise ValueError("exact_native_input_tree_required")


def verify(root, expected):
    private_directory(root)
    receipt = root / MANIFEST
    if not digest(expected) or sha256(direct(receipt)) != expected or receipt.stat().st_mode & 0o077:
        raise ValueError("pinned_private_native_input_manifest_required")
    report = read_json(receipt)
    if (type(report) is not dict or set(report) != {"schemaVersion", "purpose", "sourceConfigSha256",
            "configSha256", "preparerSha256", "expectedDdsProfileSha256", "files", "boundaries"}
            or type(report["schemaVersion"]) is not int or report["schemaVersion"] != 1
            or report["purpose"] != PURPOSE or report["preparerSha256"] != sha256(Path(__file__))
            or report["boundaries"] != BOUNDARIES or any(report["boundaries"][key] is not False for key in BOUNDARIES)
            or type(report["files"]) is not dict or not 2 < len(report["files"]) <= MAX_FILES + 2):
        raise ValueError("explicit_unaccepted_native_input_materialization_required")
    tree(root, report["files"])
    original, config = root / "original-config.json", root / "mapping.json"
    if sha256(original) != report["sourceConfigSha256"] or sha256(config) != report["configSha256"]:
        raise ValueError("unchanged_native_input_configurations_required")
    value, manifests, dds = inspect_configuration(config)
    if value != rewritten(read_json(original, 4096), root) or dds != report["expectedDdsProfileSha256"]:
        raise ValueError("only_explicit_native_input_paths_may_change")
    expected_files = {name: info for name, (_, info) in members(value, manifests).items()}
    expected_files.update({path.name: fingerprint(path) for path in (original, config)})
    if expected_files != report["files"] or sha256(receipt) != expected:
        raise ValueError("unchanged_native_input_manifest_and_closure_required")
    return report


def prepare(config, expected, output):
    preparer = sha256(Path(__file__))
    direct(config)
    if not digest(expected) or sha256(config) != expected:
        raise ValueError("pinned_original_mapping_configuration_required")
    value, manifests, dds = inspect_configuration(config)
    plan = members(value, manifests)
    if (not output.is_absolute() or output.resolve() != output or output.exists() or output.is_symlink()
            or any(output.is_relative_to(Path(path)) for path in ("/home", "/root"))
            or config.is_relative_to(output)
            or any(output == source or output.is_relative_to(source) or source.is_relative_to(output)
                   for source in roots(value).values())):
        raise ValueError("new_direct_service_accessible_native_input_output_required")
    private_directory(output.parent)
    output.mkdir(mode=0o700)
    files = {}
    for name, (source, info) in sorted(plan.items()):
        target = output / name
        for parent in reversed(PurePosixPath(name).parents):
            if str(parent) != ".":
                (output / str(parent)).mkdir(mode=0o700, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o700 if info["executable"] else 0o600)
        if fingerprint(target) != info or fingerprint(source) != info:
            raise ValueError("identical_native_input_copy_required")
        files[name] = info
    shutil.copyfile(config, output / "original-config.json")
    (output / "original-config.json").chmod(0o600)
    effective = output / "mapping.json"
    effective.write_text(json.dumps(rewritten(value, output), indent=2, allow_nan=False) + "\n", encoding="utf-8")
    effective.chmod(0o600)
    # Re-run the original verifiers at the new location before writing a completion receipt.
    copied, copied_manifests, copied_dds = inspect_configuration(effective)
    if (copied_dds != dds or copied_manifests != manifests or sha256(config) != expected
            or sha256(output / "original-config.json") != expected or sha256(Path(__file__)) != preparer
            or {name: info for name, (_, info) in members(copied, copied_manifests).items()} != files):
        raise ValueError("unchanged_original_and_relocated_native_inputs_required")
    files.update({path.name: fingerprint(path) for path in (output / "original-config.json", effective)})
    report = {"schemaVersion": 1, "purpose": PURPOSE, "sourceConfigSha256": expected,
              "configSha256": sha256(effective), "preparerSha256": preparer,
              "expectedDdsProfileSha256": dds, "files": files, "boundaries": BOUNDARIES}
    tree(output, files)
    receipt = output / MANIFEST
    receipt.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    receipt.chmod(0o600)
    identity = sha256(receipt)
    verify(output, identity)
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("prepare")
    for name in ("config", "output"):
        install.add_argument("--" + name, type=Path, required=True)
    install.add_argument("--config-sha256", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--root", type=Path, required=True)
    check.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() == 0:
        raise ValueError("ordinary_user_linux_native_input_preparation_required")
    previous = os.umask(0o077)
    try:
        if args.command == "prepare":
            identity = prepare(args.config, args.config_sha256, args.output)
            root = args.output
        else:
            root, identity = args.root, args.manifest_sha256
        report = verify(root, identity)
        print(json.dumps({"result": "native_mapping_inputs_verified_not_target_accepted",
            "manifestSha256": identity, "config": str(root / "mapping.json"),
            "configSha256": report["configSha256"], "files": len(report["files"]),
            "boundaries": report["boundaries"]}, allow_nan=False))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
