"""Package a source-bound live point runtime offline, without robot contact."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import sys

import live_bundle as bundle


ROOT = bundle.ROOT
sys.path.insert(0, str(ROOT / "tools"))
import prepare_glim_point_consumer as consumer


ARCHIVE = "live-point-runtime-arm64.tar.gz"


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def prepare(args, *, profile=bundle.LIVE):
    if sys.platform != "linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        raise ValueError("ordinary_linux_x64_live_preparation_required")
    build, dependencies = args.build.resolve(strict=True), args.consumer.resolve(strict=True)
    output = args.output.absolute()
    if (output.exists() or output.is_symlink() or output.parent.resolve(strict=True) != output.parent
            or any(output == path or output in path.parents or path in output.parents
                   for path in (build, dependencies, ROOT))):
        raise ValueError("new_private_live_preparation_outside_inputs_required")
    built = bundle.verify_build(build, args.build_manifest_sha256, profile=profile)
    published = json.loads((ROOT / "docs/slam-point-consumer-preparation-evidence.json").read_text(encoding="utf-8"))
    if args.consumer_manifest_sha256 != published["manifestSha256"]:
        raise ValueError("same_frozen_consumer_dependency_manifest_required")
    frozen = consumer.verify_bundle(dependencies, args.consumer_manifest_sha256)
    if (frozen["files"]["node/bin/node"]["sha256"] != published["nodeBinarySha256"]
            or frozen["packageLockSha256"] != bundle.sha256(ROOT / "package-lock.json")):
        raise ValueError("same_verified_node_and_project_lock_required")
    output.mkdir(mode=0o700)
    stage = output / "bundle"
    stage.mkdir(mode=0o700)
    report = {"schemaVersion": 1, "result": "failed", "boundaries": dict.fromkeys(bundle.BOUNDARIES, False)}
    try:
        def copy(source, name):
            destination = stage / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
            destination.chmod(0o700 if name == "node/bin/node" else 0o600)

        for name in bundle.dependency_entries(frozen):
            copy(dependencies / name, name)
        copy(dependencies / consumer.MANIFEST, "references/consumer-manifest.json")
        for name in built["sourceSha256"]:
            copy(ROOT / name, "source/" + name)
        for name in built["files"]:
            copy(build / name, name)
            copy(build / name, "references/build/" + name)
        copy(build / "build-manifest.json", "references/build/build-manifest.json")
        manifest = {
            "schemaVersion": 1, "purpose": profile.purpose,
            "nodeVersion": frozen["nodeVersion"], "nodeBinarySha256": published["nodeBinarySha256"],
            "consumerManifestSha256": args.consumer_manifest_sha256,
            "buildManifestSha256": args.build_manifest_sha256, "packageLockSha256": frozen["packageLockSha256"],
            "sourceSha256": built["sourceSha256"], "installedPackages": frozen["installedPackages"],
            "files": bundle.inventory(stage), "boundaries": dict.fromkeys(bundle.BOUNDARIES, False),
        }
        private_json(stage / profile.manifest, manifest)
        digest = bundle.sha256(stage / profile.manifest)
        bundle.verify_bundle(stage, digest, profile=profile)
        bundle.write_archive(stage, output / profile.archive, manifest, profile=profile)
        bundle.verify_archive(output / profile.archive, digest, profile=profile)
        if (bundle.verify_build(build, args.build_manifest_sha256, profile=profile) != built
                or consumer.verify_bundle(dependencies, args.consumer_manifest_sha256) != frozen
                or bundle.verify_bundle(stage, digest, profile=profile) != manifest):
            raise ValueError("unchanged_live_preparation_inputs_required")
        report.update(
            result=profile.result_prefix + "_prepared_not_target_executed", manifestSha256=digest,
            archiveSha256=bundle.sha256(output / profile.archive), archiveBytes=(output / profile.archive).stat().st_size,
            buildManifestSha256=args.build_manifest_sha256, consumerManifestSha256=args.consumer_manifest_sha256,
            sourceFiles=len(built["sourceSha256"]), bundleFiles=len(manifest["files"]) + 1,
            installedPackageCount=len(frozen["installedPackages"]),
        )
    except BaseException as error:
        report["failureType"] = type(error).__name__
        raise
    finally:
        private_json(output / "preparation-report.json", report)
    return report


def evidence(preparation, *, profile=bundle.LIVE):
    report = json.loads((preparation / "preparation-report.json").read_text(encoding="utf-8"))
    manifest = bundle.verify_bundle(preparation / "bundle", report["manifestSha256"], profile=profile)
    archive = preparation / profile.archive
    if (set(report) != {"schemaVersion", "result", "boundaries", "manifestSha256", "archiveSha256",
                       "archiveBytes", "buildManifestSha256", "consumerManifestSha256", "sourceFiles",
                       "bundleFiles", "installedPackageCount"}
            or type(report["schemaVersion"]) is not int or report["schemaVersion"] != 1
            or report["result"] != profile.result_prefix + "_prepared_not_target_executed"
            or report["archiveSha256"] != bundle.sha256(archive)
            or type(report["archiveBytes"]) is not int or report["archiveBytes"] != archive.stat().st_size
            or report["buildManifestSha256"] != manifest["buildManifestSha256"]
            or report["consumerManifestSha256"] != manifest["consumerManifestSha256"]
            or type(report["sourceFiles"]) is not int or report["sourceFiles"] != len(manifest["sourceSha256"])
            or type(report["bundleFiles"]) is not int or report["bundleFiles"] != len(manifest["files"]) + 1
            or type(report["installedPackageCount"]) is not int
            or report["installedPackageCount"] != len(manifest["installedPackages"])
            or report["boundaries"] != dict.fromkeys(bundle.BOUNDARIES, False)
            or any(value is not False for value in report["boundaries"].values())):
        raise ValueError("independently_verified_preparation_only_report_required")
    bundle.verify_archive(archive, report["manifestSha256"], profile=profile)
    return {
        "schemaVersion": 1, "result": report["result"], "manifestSha256": report["manifestSha256"],
        "archiveSha256": report["archiveSha256"], "archiveBytes": report["archiveBytes"],
        "buildManifestSha256": report["buildManifestSha256"],
        "consumerManifestSha256": report["consumerManifestSha256"],
        "nodeBinarySha256": manifest["nodeBinarySha256"], "nodeVersionPrepared": manifest["nodeVersion"],
        "packageLockSha256": manifest["packageLockSha256"],
        "sourceFiles": report["sourceFiles"], "bundleFiles": report["bundleFiles"],
        "installedPackageCount": report["installedPackageCount"], "allArchiveMemberBytesVerified": True,
        "runtimeSourceSha256": {name: value for name, value in manifest["sourceSha256"].items()
                               if name.startswith("robot-runtime/")},
        "boundaries": dict.fromkeys(bundle.BOUNDARIES, False),
    }


def main(*, profile=bundle.LIVE):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify", "extract"))
    for name in ("build", "consumer", "output", "preparation", "archive"):
        parser.add_argument("--" + name, type=Path)
    for name in ("build-manifest-sha256", "consumer-manifest-sha256", "archive-sha256", "manifest-sha256"):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        if args.command == "prepare":
            names = ("build", "consumer", "output", "build_manifest_sha256", "consumer_manifest_sha256")
            function = lambda: prepare(args, profile=profile)
        elif args.command == "verify":
            names = ("preparation", "output")
            function = lambda: evidence(args.preparation.resolve(strict=True), profile=profile)
        else:
            names = ("archive", "output", "archive_sha256", "manifest_sha256")
            function = lambda: bundle.extract(
                args.archive, args.output, args.archive_sha256, args.manifest_sha256, profile=profile)
        if any(getattr(args, name) is None for name in names):
            raise ValueError("explicit_live_bundle_operation_inputs_required")
        result = function()
        if args.command == "verify":
            private_json(args.output, result)
        print(json.dumps({"result": result["result"]}))
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Lingxi Next live bundle operation failed; inspect private reports.", file=sys.stderr)
        raise SystemExit(1)
