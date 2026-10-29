"""Prepare a private ARM64 point-contract bundle, never execute its binaries."""
import argparse
import gzip
import json
import os
from pathlib import Path
import platform
import shutil
import tarfile

import build_glim_point_arm64 as builder
import glim_point_target_probe as probe


ROOT = Path(__file__).resolve().parents[1]
RESULT = "arm64_point_contract_bundle_prepared_not_target_executed"


def prepare(args):
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        raise ValueError("ordinary_user_linux_x86_preparation_host_required")
    output = builder.points.private_output(args.output)
    build = args.build.resolve(strict=True)
    if output == build or output in build.parents or build in output.parents:
        raise ValueError("separate_new_point_target_preparation_required")
    evidence = builder.evidence(build)
    published = builder.points.replay.load(ROOT / "docs/slam-native-point-arm64-build-evidence.json")
    if evidence != published or evidence["inputs"]["sourceCommits"] != probe.PINS:
        raise ValueError("same_independently_published_point_cross_build_required")
    output.mkdir(mode=0o700, parents=True)
    bundle = output / "bundle"
    sources = {
        "lib/" + probe.LIBRARY: build / "build" / probe.LIBRARY,
        "bin/point_output_contract": build / "build/point_output_contract",
        "tools/glim_point_target_probe.py": ROOT / "tools/glim_point_target_probe.py",
        "tools/slam_target_probe.py": ROOT / "tools/slam_target_probe.py",
    }
    manifest = {
        "schemaVersion": 1, "purpose": probe.PURPOSE, "inputClassification": "isolated_synthetic_contracts",
        "sourceCommits": probe.PINS, "scenarios": list(probe.SCENARIOS),
        "crossBuildReportSha256": probe.sha256(build / "build-report.json"),
        "crossBuildEvidenceSha256": probe.sha256(ROOT / "docs/slam-native-point-arm64-build-evidence.json"),
        "expectedCoreLibrariesSha256": evidence["inputs"]["referenceCoreLibrariesSha256"],
        "files": {}, "targetExecutionValidated": False, "navigationValidated": False, "deploymentApproved": False,
    }
    for name, source in sources.items():
        if source.is_symlink() or not source.is_file():
            raise ValueError("direct_point_target_preparation_source_required")
        destination = bundle / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        executable = name.startswith("bin/")
        destination.chmod(0o700 if executable else 0o600)
        if probe.sha256(source) != probe.sha256(destination):
            raise ValueError("identical_point_target_preparation_bytes_required")
        manifest["files"][name] = {
            "sha256": probe.sha256(destination), "bytes": destination.stat().st_size, "executable": executable}
    with (bundle / "point-manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write("\n")
    manifest_hash = probe.sha256(bundle / "point-manifest.json")
    probe.verify_bundle(bundle, manifest_hash)
    archive = output / "point-target-validation.tar.gz"
    with archive.open("xb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|") as target:
            for name in sorted((*probe.MEMBERS, "point-manifest.json")):
                path = bundle / name
                info = tarfile.TarInfo("point-target-validation/" + name)
                info.size, info.mode = path.stat().st_size, 0o700 if name.startswith("bin/") else 0o600
                with path.open("rb") as stream:
                    target.addfile(info, stream)
    if builder.evidence(build) != evidence:
        raise ValueError("point_cross_build_changed_during_preparation")
    result = {
        "schemaVersion": 1, "result": RESULT, "manifestSha256": manifest_hash,
        "archiveSha256": probe.sha256(archive), "archiveBytes": archive.stat().st_size,
        "bundleFiles": len(probe.MEMBERS) + 1, "preparedNativeScenarios": len(probe.SCENARIOS),
        "preparerSourceSha256": probe.sha256(__file__),
        "probeSourceSha256": probe.sha256(ROOT / "tools/glim_point_target_probe.py"),
        "crossBuildReportSha256": manifest["crossBuildReportSha256"],
        "nativeContractExecutionsOnTarget": 0, "robotContacted": False,
        "runtimePackageBuilt": False, "targetExecutionValidated": False, "deploymentApproved": False,
    }
    builder.points.replay.private_json(output / "preparation-report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("build", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps(prepare(args)))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
