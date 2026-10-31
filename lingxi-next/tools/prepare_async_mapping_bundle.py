"""Freeze the verified full mapper and configuration separately from vendor/runtime roots."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot-runtime/python"))
import async_mapping_bundle as bundle
import run_glim_async_mapping as asynchronous


def prepare(args):
    output = asynchronous.full.private_output(args.output)
    if any(output == path or path in output.parents or output in path.parents
           for path in (args.build, args.native_work, args.upstream, args.baseline)):
        raise ValueError("new_full_mapping_bundle_outside_frozen_inputs_required")
    before = bundle.source_hashes()
    if args.platform == "linux-aarch64":
        from build_glim_async_arm64 import inputs
        from slam_target_probe import verify_bundle
        if args.cross_work is None or args.core_root is None:
            raise ValueError("explicit_full_mapper_cross_core_required")
        report = bundle.read_json(args.build / "build-report.json")
        if report.get("inputs") != inputs(args):
            raise ValueError("unchanged_verified_async_cross_build_inputs_required")
        core = verify_bundle(args.core_root)
        core_manifest = bundle.sha256(args.core_root / "bundle-manifest.json")
        libraries = {name: bundle.sha256(args.cross_work / "prefix/lib" / name)
                     for name in bundle.CORE_LIBRARIES}
        if any(core["files"]["lib/" + name]["sha256"] != value for name, value in libraries.items()):
            raise ValueError("full_mapper_must_use_same_target_core_required")
        library = args.build / "build" / bundle.LIBRARY
    else:
        library = asynchronous.verified_adapter(SimpleNamespace(
            work=args.native_work, upstream=args.upstream, mapping_adapter=args.build))
        core_manifest = bundle.sha256(args.native_work / "build-report.json")
        libraries = {name: bundle.sha256(args.native_work / "prefix/lib" / name)
                     for name in bundle.CORE_LIBRARIES}
    output.mkdir(mode=0o700)
    (output / "lib").mkdir(mode=0o700)
    shutil.copyfile(library, output / "lib" / bundle.LIBRARY)
    shutil.copyfile(args.build / "build-report.json", output / "build-report.json")
    asynchronous.full.full_config(args.upstream, args.baseline / "config", output / "config")
    first, last = bundle.verify_configuration(output, args.baseline)
    value = {"schemaVersion": 1, "engine": bundle.ENGINE, "platform": args.platform,
             "sourceSha256": before, "baselineConfigurationSha256": first, "configurationSha256": last,
             "profileSha256": bundle.sha256(args.baseline / "private-profile.json"),
             "coreManifestSha256": core_manifest, "coreLibrariesSha256": libraries,
             "files": bundle.inventory(output), "targetExecutionValidated": False,
             "geometryValidated": False, "navigationValidated": False}
    asynchronous.full.save(output / bundle.MANIFEST, value)
    manifest_sha = bundle.sha256(output / bundle.MANIFEST)
    bundle.verify_bundle(output, manifest_sha, baseline=args.baseline, core_manifest=core_manifest,
                         core_libraries=libraries, platform=args.platform)
    if bundle.source_hashes() != before:
        raise ValueError("full_mapper_bundle_sources_changed")
    return {"result": "source_bound_full_mapper_prepared_not_target_executed", "manifestSha256": manifest_sha,
            "platform": args.platform}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("build", "native-work", "upstream", "baseline", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("cross-work", "core-root"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--platform", choices=tuple(bundle.PLATFORMS), required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.absolute())
    previous = os.umask(0o077)
    try:
        print(json.dumps(prepare(args)))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
