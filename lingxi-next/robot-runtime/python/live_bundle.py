"""Offline live-runtime inventory and archive checks; never execute robot code."""
import gzip
import hashlib
import importlib
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
MANIFEST = "live-manifest.json"
PREFIX = "live-point-runtime/"
PURPOSE = "isolated_arm64_live_point_runtime_preparation"
BOUNDARIES = ("robotContacted", "arm64RuntimeExecuted", "sensorsSubscribed", "motionCommandsSent",
              "servicesChanged", "liveRenderingValidated", "mappingReady", "localizationReady",
              "navigationReady", "productAccepted")
DIRECTORIES = ("web", "robot-api/src", "robot-runtime/src", "robot-runtime/python",
               "robot-slam/lingxi_slam", "robot-slam/native-point-output", "tools/glim-replay",
               "tools/glim-mapping", "shared")
METADATA = (
    "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json", "tsconfig.api.json",
    "tsconfig.runtime.json", "robot-runtime/build-live.mjs", "docs/slam-humble-live-evidence.json",
    "docs/slam-supervised-arm64-evidence.json", "docs/slam-point-arm64-target-evidence.json",
    "docs/slam-point-consumer-preparation-evidence.json", "docs/slam-native-point-pipe-evidence.json",
    "docs/slam-stream-input-evidence.json",
    "robot-slam/runtime-arm64-cp310.lock.json",
)

@dataclass(frozen=True)
class BundleProfile:
    manifest: str = MANIFEST
    prefix: str = PREFIX
    purpose: str = PURPOSE
    build_purpose: str = "isolated_live_point_assets_not_target_acceptance"
    archive: str = "live-point-runtime-arm64.tar.gz"
    result_prefix: str = "live_runtime"
    metadata: tuple = ()


LIVE = BundleProfile()
MAPPING = BundleProfile(
    manifest="mapping-manifest.json", prefix="managed-mapping-runtime/",
    purpose="isolated_arm64_managed_mapping_runtime_preparation",
    build_purpose="isolated_managed_mapping_assets_not_target_acceptance",
    archive="managed-mapping-runtime-arm64.tar.gz", result_prefix="mapping_runtime",
    metadata=("robot-runtime/build-mapping.mjs",),
)
SLAM = BundleProfile(
    manifest="slam-manifest.json", prefix="shared-slam-runtime/",
    purpose="shared_arm64_slam_runtime_preparation",
    build_purpose="shared_slam_assets_not_target_acceptance",
    archive="shared-slam-runtime-arm64.tar.gz", result_prefix="slam_runtime",
    metadata=("robot-runtime/build-slam.mjs",),
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def digest(value):
    return type(value) is str and re.fullmatch("[a-f0-9]{64}", value) is not None


def relative(name):
    path = PurePosixPath(name)
    if (type(name) is not str or not name or path.is_absolute() or ".." in path.parts
            or "\\" in name or ":" in name or path.as_posix() != name):
        raise ValueError("canonical_direct_live_bundle_member_required")
    return path


def ordinary_files(root, *, skip_caches=False):
    result = []
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories.copy():
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError("direct_live_bundle_directories_required")
            if skip_caches and name == "__pycache__":
                directories.remove(name)
        for name in files:
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("ordinary_live_bundle_files_required")
            result.append(path)
    return sorted(result)


def source_hashes(project=None, *, profile=LIVE):
    project = ROOT if project is None else project
    project = project.resolve(strict=True)
    names = set((*METADATA, *profile.metadata))
    for directory in DIRECTORIES:
        base = project / directory
        if base.is_symlink() or not base.is_dir():
            raise ValueError("complete_direct_live_source_tree_required")
        names.update(path.relative_to(project).as_posix() for path in ordinary_files(base, skip_caches=True))
    names.update(path.relative_to(project).as_posix() for path in (project / "tools").glob("*.py"))
    hashes, total = {}, 0
    for name in sorted(names):
        relative(name)
        if re.search(r"(^|/)(\.|private-)|\.(pem|key|npz|db3|sqlite3|log)$", name, re.I):
            raise ValueError("source_only_live_build_inventory_required")
        path = project / name
        if path.is_symlink() or not path.is_file() or path.resolve() != path:
            raise ValueError("direct_live_source_file_required")
        total += path.stat().st_size
        if len(names) > 2000 or total > 32 * 1024 * 1024 or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("bounded_live_source_tree_required")
        hashes[name] = sha256(path)
    return hashes


def inventory(root, excluded=()):
    result, total = {}, 0
    for path in ordinary_files(root):
        name = path.relative_to(root).as_posix()
        if name in excluded:
            continue
        relative(name)
        size = path.stat().st_size
        total += size
        if (len(result) >= 50_000 or size > 256 * 1024 * 1024 or total > 512 * 1024 * 1024
                or path.suffix in (".node", ".pem", ".key", ".npz", ".db3", ".sqlite3", ".log", ".pyc")
                or any(part == "__pycache__" for part in PurePosixPath(name).parts)
                or not name.startswith("node_modules/")
                and any(part.startswith(".") for part in PurePosixPath(name).parts)):
            raise ValueError("bounded_credential_free_javascript_live_bundle_required")
        result[name] = {"sha256": sha256(path), "bytes": size, "executable": name == "node/bin/node"}
    return result


def entries_valid(entries, *, profile=LIVE):
    if type(entries) is not dict or not 1 <= len(entries) < 50_000:
        raise ValueError("bounded_live_member_inventory_required")
    total = 0
    for name, entry in entries.items():
        relative(name)
        if (name == profile.manifest or type(entry) is not dict or set(entry) != {"sha256", "bytes", "executable"}
                or not digest(entry["sha256"]) or type(entry["bytes"]) is not int
                or not 0 <= entry["bytes"] <= 256 * 1024 * 1024
                or entry["executable"] is not (name == "node/bin/node")):
            raise ValueError("exact_live_member_record_required")
        total += entry["bytes"]
    if total > 512 * 1024 * 1024:
        raise ValueError("bounded_expanded_live_runtime_required")


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if value and (tag == "script" and name == "src" or tag == "link" and name == "href"):
                self.references.append(value)


def check_assets(root, entries):
    if "api/main.mjs" not in entries or "web/index.html" not in entries:
        raise ValueError("complete_live_api_and_web_assets_required")
    for name in entries:
        if name not in ("api/main.mjs", "web/index.html"):
            if not re.fullmatch(r"web/assets/[A-Za-z0-9_.-]+\.(?:js|css|svg|png|jpg|webp|ico|woff2)", name):
                raise ValueError("explicit_compiled_live_asset_required")
    parser = AssetReferences()
    parser.feed((root / "web/index.html").read_text(encoding="utf-8"))
    for value in parser.references:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc or "web/" + parsed.path.lstrip("/") not in entries:
            raise ValueError("local_present_web_asset_reference_required")
    if not parser.references:
        raise ValueError("web_entrypoint_asset_references_required")


def verify_build(root, expected, *, project=None, profile=LIVE):
    project = ROOT if project is None else project
    record = root / "build-manifest.json"
    if not digest(expected) or record.is_symlink() or sha256(record) != expected:
        raise ValueError("explicit_identical_live_build_manifest_required")
    manifest = json.loads(record.read_text(encoding="utf-8"))
    keys = {"schemaVersion", "purpose", "nodeBuildHostVersion", "esbuildVersion", "viteVersion",
            "environmentFilesLoaded", "customEnvironmentVariablesExposed", "sourceSha256", "files"}
    if (set(manifest) != keys or type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1
            or manifest["purpose"] != profile.build_purpose
            or manifest["environmentFilesLoaded"] is not False
            or manifest["customEnvironmentVariablesExposed"] is not False
            or re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", manifest["nodeBuildHostVersion"]) is None
            or manifest["sourceSha256"] != source_hashes(project, profile=profile)):
        raise ValueError("same_source_bound_live_build_required")
    lock = json.loads((project / "package-lock.json").read_text(encoding="utf-8"))
    if (manifest["esbuildVersion"] != lock["packages"]["node_modules/esbuild"]["version"]
            or manifest["viteVersion"] != lock["packages"]["node_modules/vite"]["version"]):
        raise ValueError("same_locked_live_build_tools_required")
    expected_files = {name: {"sha256": entry["sha256"], "bytes": entry["bytes"], "executable": False}
                      for name, entry in manifest["files"].items()
                      if type(entry) is dict and set(entry) == {"sha256", "bytes"}}
    entries_valid(expected_files, profile=profile)
    if len(expected_files) != len(manifest["files"]) or inventory(root, ("build-manifest.json",)) != expected_files:
        raise ValueError("identical_complete_live_build_assets_required")
    check_assets(root, expected_files)
    return manifest


def dependency_entries(consumer):
    return {name: entry for name, entry in consumer["files"].items()
            if name.startswith("node_modules/") or name in (
                "node/bin/node", "node/LICENSE", "package.json", "package-lock.json")}


def verify_bundle(root, expected, *, project=None, profile=LIVE):
    project = ROOT if project is None else project
    record = root / profile.manifest
    if not digest(expected) or record.is_symlink() or sha256(record) != expected:
        raise ValueError("explicit_identical_live_runtime_manifest_required")
    manifest = json.loads(record.read_text(encoding="utf-8"))
    keys = {"schemaVersion", "purpose", "nodeVersion", "nodeBinarySha256", "consumerManifestSha256",
            "buildManifestSha256", "packageLockSha256", "sourceSha256", "installedPackages", "files", "boundaries"}
    if (set(manifest) != keys or type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1
            or manifest["purpose"] != profile.purpose or manifest["boundaries"] != dict.fromkeys(BOUNDARIES, False)
            or any(value is not False for value in manifest["boundaries"].values())):
        raise ValueError("preparation_only_live_runtime_required")
    entries_valid(manifest["files"], profile=profile)
    if (inventory(root, (profile.manifest,)) != manifest["files"]
            or manifest["sourceSha256"] != source_hashes(project, profile=profile)):
        raise ValueError("identical_complete_live_runtime_required")
    reference = json.loads((project / "docs/slam-point-consumer-preparation-evidence.json").read_text(encoding="utf-8"))
    consumer_file = root / "references/consumer-manifest.json"
    if (manifest["consumerManifestSha256"] != reference["manifestSha256"]
            or sha256(consumer_file) != reference["manifestSha256"]
            or manifest["nodeBinarySha256"] != reference["nodeBinarySha256"]
            or sha256(root / "node/bin/node") != reference["nodeBinarySha256"]
            or manifest["nodeVersion"] != reference["nodeVersionPreparedNotExecuted"]
            or manifest["packageLockSha256"] != reference["packageLockSha256"]
            or sha256(root / "package-lock.json") != reference["packageLockSha256"]):
        raise ValueError("same_verified_arm64_runtime_dependencies_required")
    consumer = json.loads(consumer_file.read_text(encoding="utf-8"))
    dependencies = dependency_entries(consumer)
    for name, entry in dependencies.items():
        if manifest["files"].get(name) != entry:
            raise ValueError("unchanged_verified_live_dependency_bytes_required")
    for name, value in manifest["sourceSha256"].items():
        if manifest["files"].get("source/" + name, {}).get("sha256") != value:
            raise ValueError("complete_mirrored_live_sources_required")
    build_root = root / "references/build"
    build = verify_build(build_root, manifest["buildManifestSha256"], project=project, profile=profile)
    for name, entry in build["files"].items():
        if manifest["files"].get(name) != {**entry, "executable": False}:
            raise ValueError("same_source_bound_live_runtime_assets_required")
    allowed = set(dependencies) | {"source/" + name for name in manifest["sourceSha256"]}
    allowed |= set(build["files"]) | {"references/consumer-manifest.json", "references/build/build-manifest.json"}
    allowed |= {"references/build/" + name for name in build["files"]}
    if set(manifest["files"]) != allowed:
        raise ValueError("only_approved_live_runtime_members_required")
    from prepare_glim_point_consumer import installed_packages
    from glim_point_target_probe import arm64_elf
    arm64_elf(root / "node/bin/node")
    packages = installed_packages(root, json.loads((root / "package-lock.json").read_text(encoding="utf-8")))
    if packages != consumer["installedPackages"] or packages != manifest["installedPackages"]:
        raise ValueError("identical_locked_live_packages_required")
    return manifest


def write_archive(root, archive, manifest, *, profile=LIVE):
    with archive.open("xb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|") as stream:
            for name in sorted((*manifest["files"], profile.manifest)):
                path = root / name
                member = tarfile.TarInfo(profile.prefix + name)
                member.size, member.mode = path.stat().st_size, 0o700 if name == "node/bin/node" else 0o600
                with path.open("rb") as payload:
                    stream.addfile(member, payload)


def archive_manifest(archive, expected, *, profile=LIVE):
    with tarfile.open(archive, "r:gz") as stream:
        member = stream.getmember(profile.prefix + profile.manifest)
        if not member.isfile() or not 0 < member.size <= 8 * 1024 * 1024:
            raise ValueError("bounded_direct_live_manifest_required")
        with stream.extractfile(member) as payload:
            data = payload.read()
    if not digest(expected) or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("identical_archived_live_manifest_required")
    manifest = json.loads(data)
    entries_valid(manifest["files"], profile=profile)
    if manifest.get("purpose") != profile.purpose:
        raise ValueError("same_runtime_archive_purpose_required")
    return manifest, len(data)


def verify_archive(archive, expected, *, profile=LIVE):
    manifest, size = archive_manifest(archive, expected, profile=profile)
    entries = {**manifest["files"], profile.manifest: {"sha256": expected, "bytes": size, "executable": False}}
    seen = set()
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream:
            name = member.name.removeprefix(profile.prefix)
            if (member.name != profile.prefix + name or name not in entries or name in seen or not member.isfile()
                    or member.size != entries[name]["bytes"] or member.uid or member.gid or member.mtime
                    or member.uname or member.gname
                    or set(member.pax_headers) - {"path"}
                    or "path" in member.pax_headers and member.pax_headers["path"] != member.name
                    or member.mode != (0o700 if entries[name]["executable"] else 0o600)):
                raise ValueError("exact_deterministic_live_archive_required")
            digest_ = hashlib.sha256()
            with stream.extractfile(member) as payload:
                for block in iter(lambda: payload.read(65536), b""):
                    digest_.update(block)
            if digest_.hexdigest() != entries[name]["sha256"]:
                raise ValueError("identical_live_archive_member_bytes_required")
            seen.add(name)
    if seen != set(entries):
        raise ValueError("all_live_archive_members_required")
    return manifest


def extract(archive, output, archive_sha256, manifest_sha256, *, profile=LIVE):
    archive = archive.resolve(strict=True)
    output = output.absolute()
    if (not digest(archive_sha256) or archive.is_symlink() or sha256(archive) != archive_sha256
            or output.exists() or output.is_symlink() or output.parent.resolve(strict=True) != output.parent
            or output in archive.parents or not 0 < archive.stat().st_size <= 128 * 1024 * 1024):
        raise ValueError("explicit_matching_archive_and_new_private_live_output_required")
    manifest = verify_archive(archive, manifest_sha256, profile=profile)
    output.mkdir(mode=0o700)
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream:
            name = member.name.removeprefix(profile.prefix)
            destination = output / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with stream.extractfile(member) as payload, destination.open("xb") as target:
                shutil.copyfileobj(payload, target)
            destination.chmod(0o700 if name == "node/bin/node" else 0o600)
    importlib.invalidate_caches()
    verify_bundle(output, manifest_sha256, project=output / "source", profile=profile)
    if sha256(archive) != archive_sha256:
        raise ValueError("unchanged_live_archive_during_extraction_required")
    return {"result": profile.result_prefix + "_extracted_not_executed", "bundleFiles": len(manifest["files"]) + 1,
            "archiveSha256": archive_sha256, "manifestSha256": manifest_sha256}
