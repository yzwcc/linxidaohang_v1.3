"""Extract a checksum-pinned private consumer without executing Node or SLAM."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile

import export_glim_point_consumer as exporter
import prepare_glim_point_consumer as consumer
from verify_slam_capture import sha256


RESULT = "identical_private_arm64_consumer_extracted_not_executed"


def extract(archive, output, archive_sha256, manifest_sha256):
    archive, output = archive.resolve(strict=True), output.resolve()
    if (output.exists() or output in archive.parents or not archive.is_file()
            or sha256(archive) != archive_sha256 or not 0 < archive.stat().st_size <= 128 * 1024 * 1024):
        raise ValueError("new_private_output_and_identical_bounded_consumer_archive_required")
    with tarfile.open(archive, "r:gz") as stream:
        member = stream.getmember("point-consumer/" + consumer.MANIFEST)
        if not member.isfile() or not 0 < member.size <= 8 * 1024 * 1024:
            raise ValueError("bounded_direct_consumer_manifest_required")
        with stream.extractfile(member) as payload:
            data = payload.read()
    import hashlib
    if hashlib.sha256(data).hexdigest() != manifest_sha256:
        raise ValueError("identical_consumer_manifest_bytes_required")
    manifest = json.loads(data)
    entries = manifest["files"]
    if not 1 <= len(entries) < 50_000:
        raise ValueError("bounded_consumer_inventory_required")
    for name, entry in entries.items():
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
                or path.as_posix() != name or name == consumer.MANIFEST
                or set(entry) != {"sha256", "bytes", "executable"}
                or type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= 256 * 1024 * 1024
                or entry["executable"] is not (name == "node/bin/node")):
            raise ValueError("direct_relative_bounded_consumer_members_required")
    if sum(entry["bytes"] for entry in entries.values()) > 512 * 1024 * 1024:
        raise ValueError("bounded_expanded_consumer_required")
    exporter.verify_archive(archive, manifest, manifest_sha256, len(data))
    output.mkdir(mode=0o700, parents=True)
    # No generic archive extraction: every destination is an already-verified member.
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream:
            name = member.name.removeprefix("point-consumer/")
            destination = output / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with stream.extractfile(member) as payload, destination.open("xb") as target:
                shutil.copyfileobj(payload, target)
            destination.chmod(0o700 if name == "node/bin/node" else 0o600)
    consumer.verify_bundle(output, manifest_sha256)
    if sha256(archive) != archive_sha256:
        raise ValueError("consumer_archive_changed_during_extraction")
    return {
        "result": RESULT, "archiveSha256": archive_sha256, "manifestSha256": manifest_sha256,
        "bundleFiles": len(entries) + 1, "nodeExecuted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("archive-sha256", "manifest-sha256"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps(extract(args.archive, args.output, args.archive_sha256, args.manifest_sha256)))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
