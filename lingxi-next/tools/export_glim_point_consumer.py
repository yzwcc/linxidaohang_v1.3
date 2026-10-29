"""Verify prepared consumer bytes/archive and export preparation-only aggregates."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile

import glim_point_supervised_target as binding
import prepare_glim_point_consumer as consumer
from verify_slam_capture import sha256


def verify_archive(archive, manifest, manifest_sha256, manifest_bytes):
    expected = {**manifest["files"], consumer.MANIFEST: {
        "sha256": manifest_sha256, "bytes": manifest_bytes, "executable": False}}
    seen = set()
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream:
            name = member.name.removeprefix("point-consumer/")
            if (member.name != "point-consumer/" + name or name not in expected or name in seen
                    or not member.isfile() or member.size != expected[name]["bytes"]
                    or member.mode != (0o700 if expected[name]["executable"] else 0o600)
                    or member.uid or member.gid or member.mtime):
                raise ValueError("exact_direct_deterministic_consumer_archive_required")
            digest = hashlib.sha256()
            with stream.extractfile(member) as payload:
                for block in iter(lambda: payload.read(65536), b""):
                    digest.update(block)
            if digest.hexdigest() != expected[name]["sha256"]:
                raise ValueError("consumer_archive_member_bytes_mismatch")
            seen.add(name)
    if seen != set(expected):
        raise ValueError("all_consumer_archive_members_required")
    return len(seen)


def evidence(preparation):
    report = json.loads((preparation / "preparation-report.json").read_text(encoding="utf-8"))
    bundle, archive = preparation / "bundle", preparation / "point-consumer-arm64.tar.gz"
    manifest = consumer.verify_bundle(bundle, report["manifestSha256"])
    consumer.verified_consumer(bundle / "consumer.mjs")
    if (type(report["schemaVersion"]) is not int or report["schemaVersion"] != 1
            or report["result"] != consumer.RESULT
            or type(report["npmExitCode"]) is not int or report["npmExitCode"] != 0
            or report["preparerSha256"] != sha256(consumer.__file__)
            or report["nodeArchiveSha256"] != consumer.NODE_ARCHIVE_SHA256
            or report["consumerBundleSha256"] != manifest["consumerBundleSha256"]
            or report["packageLockSha256"] != manifest["packageLockSha256"]
            or report["archiveSha256"] != sha256(archive)
            or report["archiveBytes"] != archive.stat().st_size
            or report["bundleFiles"] != len(manifest["files"]) + 1
            or report["installedPackageCount"] != len(manifest["installedPackages"])
            or report["boundaries"] != dict.fromkeys(consumer.BOUNDARIES, False)
            or any(value is not False for value in report["boundaries"].values())):
        raise ValueError("same_verified_preparation_only_consumer_required")
    verify_archive(archive, manifest, report["manifestSha256"], (bundle / consumer.MANIFEST).stat().st_size)
    return {
        "schemaVersion": 1, "date": "2026-10-29", "result": consumer.RESULT, "inputMode": "recorded",
        "privatePreparationReportSha256": sha256(preparation / "preparation-report.json"),
        "preparerSha256": report["preparerSha256"], "bindingSourceSha256": binding.source_hashes(),
        "manifestSha256": report["manifestSha256"], "archiveSha256": report["archiveSha256"],
        "archiveBytes": report["archiveBytes"], "bundleFiles": report["bundleFiles"],
        "installedPackageCount": report["installedPackageCount"],
        "nodeVersionPreparedNotExecuted": manifest["nodeVersion"],
        "nodeArchitectureFromElfHeader": "aarch64", "nodeArchiveSha256": report["nodeArchiveSha256"],
        "nodeBinarySha256": manifest["files"]["node/bin/node"]["sha256"],
        "consumerBundleSha256": report["consumerBundleSha256"], "packageLockSha256": report["packageLockSha256"],
        "npmExitCodeOnBuildHost": report["npmExitCode"],
        "observations": {
            "scriptsDisabledDuringDependencyInstall": True, "noNativeNodeAddonsIncluded": True,
            "allArchiveMemberBytesVerified": True, "recordedConsumerMatchesPreviousVerifiedBundle": True,
        },
        "boundaries": dict.fromkeys(consumer.BOUNDARIES, False),
        "acceptance": dict.fromkeys((
            "arm64NodeExecutionValidated", "arm64RecordedProducerConsumerValidated",
            "livePointRenderingValidated", "pointDisplayLatencyValidated", "mappingReady",
            "localizationReady", "navigationReady", "productAccepted",
        ), False),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = evidence(args.preparation.resolve(strict=True))
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"result": result["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
