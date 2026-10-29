"""Prepare an isolated, locked ARM64 recorded-point consumer; never run ARM code."""
import argparse
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import tarfile

import run_glim_point_pipe_trial as pipe
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
VERSION = "22.23.4"
NODE_ARCHIVE_SHA256 = "f7a1e486dbc8b286847815b477d4fb5e9fb963ed4489c3e1588853d685f589f9"
PURPOSE = "isolated_arm64_recorded_point_consumer"
RESULT = "arm64_recorded_point_consumer_prepared_not_target_executed"
MANIFEST = "consumer-manifest.json"
BOUNDARIES = ("robotContacted", "targetNodeExecuted", "recordedNativeBindingValidated",
              "livePointChannelValidated", "navigationValidated", "deploymentApproved")


def inventory(root):
    files, total = {}, 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("direct_consumer_bundle_members_only")
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if name == MANIFEST:
            continue
        size = path.stat().st_size
        if size > 256 * 1024 * 1024 or len(files) >= 50_000:
            raise ValueError("bounded_consumer_bundle_required")
        total += size
        if total > 512 * 1024 * 1024 or path.suffix == ".node":
            raise ValueError("bounded_javascript_only_consumer_dependencies_required")
        files[name] = {"sha256": sha256(path), "bytes": size, "executable": name == "node/bin/node"}
    return files


def installed_packages(root, lock):
    packages = {}

    def visit(directory):
        for path in sorted(directory.iterdir()):
            if path.name.startswith("."):
                continue
            if path.name.startswith("@"):
                for child in sorted(path.iterdir()):
                    add(child)
            else:
                add(path)

    def add(path):
        name = path.relative_to(root).as_posix()
        record = path / "package.json"
        if path.is_symlink() or not path.is_dir() or record.is_symlink() or not record.is_file():
            raise ValueError("direct_locked_installed_package_required")
        metadata, expected = json.loads(record.read_text(encoding="utf-8")), lock["packages"].get(name)
        if (not expected or expected.get("dev") is True
                or metadata["version"] != expected["version"] or expected.get("hasInstallScript") is True):
            raise ValueError("script_free_production_package_from_same_lock_required")
        packages[name] = metadata["version"]
        nested = path / "node_modules"
        if nested.exists():
            visit(nested)

    visit(root / "node_modules")
    required = {name for name, entry in lock["packages"].items() if name
                and not any(entry.get(key) for key in ("dev", "optional", "devOptional"))}
    if not required.issubset(packages):
        raise ValueError("all_required_locked_production_packages_required")
    return packages


def verified_consumer(path):
    published = json.loads((ROOT / "docs/slam-native-point-pipe-evidence.json").read_text(encoding="utf-8"))
    if (path.is_symlink() or not path.is_file() or sha256(path) != published["consumerBundleSha256"]
            or pipe.source_hashes() != published["transportSourceSha256"]):
        raise ValueError("same_previously_verified_recorded_consumer_and_sources_required")
    return published


def verify_bundle(root, expected_manifest):
    root = root.resolve(strict=True)
    record = root / MANIFEST
    if (record.is_symlink() or len(expected_manifest) != 64 or sha256(record) != expected_manifest):
        raise ValueError("explicit_identical_consumer_manifest_required")
    manifest = json.loads(record.read_text(encoding="utf-8"))
    for name, entry in manifest["files"].items():
        if (set(entry) != {"sha256", "bytes", "executable"}
                or not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or type(entry["bytes"]) is not int or entry["bytes"] < 0
                or entry["executable"] is not (name == "node/bin/node")):
            raise ValueError("strict_consumer_file_inventory_required")
    if (type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1
            or manifest["purpose"] != PURPOSE or manifest["inputMode"] != "recorded"
            or manifest["nodeVersion"] != VERSION or manifest["nodeArchiveSha256"] != NODE_ARCHIVE_SHA256
            or manifest["boundaries"] != dict.fromkeys(BOUNDARIES, False)
            or any(value is not False for value in manifest["boundaries"].values())
            or inventory(root) != manifest["files"]):
        raise ValueError("unchanged_isolated_arm64_consumer_bundle_required")
    for name in manifest["files"]:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("relative_consumer_member_required")
    for name in ("consumer.mjs", "package.json", "package-lock.json", "node/bin/node", "node/LICENSE"):
        if name not in manifest["files"]:
            raise ValueError("complete_standalone_consumer_required")
    from glim_point_target_probe import arm64_elf
    arm64_elf(root / "node/bin/node")
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
    if (sha256(root / "package-lock.json") != manifest["packageLockSha256"]
            or sha256(root / "consumer.mjs") != manifest["consumerBundleSha256"]
            or installed_packages(root, lock) != manifest["installedPackages"]):
        raise ValueError("identical_locked_consumer_dependencies_required")
    return manifest


def npm_environment(output):
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(output),
        "npm_config_userconfig": str(output / "private-user.npmrc"),
        "npm_config_globalconfig": str(output / "private-global.npmrc"),
        "npm_config_cache": str(output / "private-npm-cache"),
        "npm_config_registry": "https://registry.npmjs.org",
    }


def prepare(args):
    if (platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0):
        raise ValueError("ordinary_user_linux_x86_preparation_required")
    output = pipe.points.private_output(args.output)
    sources = tuple(getattr(args, name).resolve(strict=True)
                    for name in ("node_archive", "consumer", "host_node", "npm_cli"))
    if any(output == path or output in path.parents or path in output.parents for path in sources):
        raise ValueError("consumer_output_must_not_overlap_inputs")
    node_archive, consumer, host_node, npm_cli = sources
    published = verified_consumer(consumer)
    if sha256(node_archive) != NODE_ARCHIVE_SHA256:
        raise ValueError("same_checksum_pinned_arm64_node_archive_required")
    output.mkdir(mode=0o700, parents=True)
    bundle, install = output / "bundle", output / "private-install"
    bundle.mkdir(mode=0o700)
    install.mkdir(mode=0o700)
    report = {"schemaVersion": 1, "result": "running", "boundaries": dict.fromkeys(BOUNDARIES, False)}
    try:
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(ROOT / name, install / name)
            shutil.copyfile(ROOT / name, bundle / name)
        with (output / "private-npm-ci.log").open("xb") as log:
            command = [str(host_node), str(npm_cli), "ci", "--omit=dev", "--ignore-scripts",
                       "--no-audit", "--no-fund", "--os=linux", "--cpu=arm64", "--libc=glibc"]
            completed = subprocess.run(command, cwd=install, env=npm_environment(output),
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=240)
        report["npmExitCode"] = completed.returncode
        if completed.returncode != 0:
            raise RuntimeError("locked_consumer_install_failed_see_private_log")
        shutil.copytree(install / "node_modules", bundle / "node_modules", symlinks=True,
                        ignore=lambda directory, names: {".bin"} if Path(directory).name == "node_modules" else set())
        shutil.copyfile(consumer, bundle / "consumer.mjs")
        with tarfile.open(node_archive, "r:gz") as archive:
            prefix = "node-v" + VERSION + "-linux-arm64/"
            for source, name in (("bin/node", "node/bin/node"), ("LICENSE", "node/LICENSE")):
                member = archive.getmember(prefix + source)
                if not member.isfile() or not 0 < member.size <= 256 * 1024 * 1024:
                    raise ValueError("bounded_direct_pinned_node_member_required")
                destination = bundle / name
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.extractfile(member) as stream, destination.open("xb") as target:
                    shutil.copyfileobj(stream, target)
                destination.chmod(0o700 if source == "bin/node" else 0o600)
        manifest = {
            "schemaVersion": 1, "purpose": PURPOSE, "inputMode": "recorded", "nodeVersion": VERSION,
            "nodeArchiveSha256": NODE_ARCHIVE_SHA256, "packageLockSha256": sha256(ROOT / "package-lock.json"),
            "consumerBundleSha256": published["consumerBundleSha256"],
            "sourceSha256": published["transportSourceSha256"],
            "referenceEvidenceSha256": sha256(ROOT / "docs/slam-native-point-pipe-evidence.json"),
            "installedPackages": installed_packages(bundle, json.loads((bundle / "package-lock.json").read_text())),
            "files": inventory(bundle), "boundaries": dict.fromkeys(BOUNDARIES, False),
        }
        with (bundle / MANIFEST).open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, allow_nan=False)
            stream.write("\n")
        digest = sha256(bundle / MANIFEST)
        verify_bundle(bundle, digest)
        archive_path = output / "point-consumer-arm64.tar.gz"
        with archive_path.open("xb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for name in sorted((*manifest["files"], MANIFEST)):
                    path = bundle / name
                    member = tarfile.TarInfo("point-consumer/" + name)
                    member.size, member.mode = path.stat().st_size, 0o700 if name == "node/bin/node" else 0o600
                    with path.open("rb") as stream:
                        archive.addfile(member, stream)
        if (sha256(node_archive) != NODE_ARCHIVE_SHA256
                or verified_consumer(consumer) != published or verify_bundle(bundle, digest) != manifest):
            raise ValueError("consumer_preparation_inputs_changed")
        report.update(
            result=RESULT, manifestSha256=digest, archiveSha256=sha256(archive_path),
            archiveBytes=archive_path.stat().st_size, bundleFiles=len(manifest["files"]) + 1,
            installedPackageCount=len(manifest["installedPackages"]), preparerSha256=sha256(__file__),
            hostNodeSha256=sha256(host_node), npmCliSha256=sha256(npm_cli),
            nodeArchiveSha256=NODE_ARCHIVE_SHA256, consumerBundleSha256=manifest["consumerBundleSha256"],
            packageLockSha256=manifest["packageLockSha256"])
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        with (output / "preparation-report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("node-archive", "consumer", "host-node", "npm-cli", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps(prepare(args)))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
