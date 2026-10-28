"""Verify finalized private capture copies without ROS or network access."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from slam_bag_audit import CLOUD, IMU, ODOMETRY


TOPICS = {
    "cloud": CLOUD, "imu": IMU, "odometry": ODOMETRY,
    "tf": "/tf", "tf_static": "/tf_static",
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def filename(value):
    if (not isinstance(value, str) or not value or value in (".", "..")
            or any(character in value for character in ("/", "\\", ":", "\x00"))):
        raise ValueError("plain_filename_required")
    return value


def validate_manifest(manifest):
    if type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] != 1:
        raise ValueError("unsupported_capture_manifest")
    datasets = manifest.get("datasets")
    if not isinstance(datasets, dict) or set(datasets) != {"static", "dynamic"}:
        raise ValueError("static_and_dynamic_datasets_required")
    for dataset in datasets.values():
        filename(dataset["directoryName"])
        checksums = dataset["sha256"]
        if (not isinstance(checksums, dict) or "metadata.yaml" not in checksums
                or not any(name.endswith(".db3") for name in checksums)
                or any(name != "metadata.yaml" and not name.endswith(".db3") for name in checksums)):
            raise ValueError("metadata_and_database_checksums_required")
        for name, digest in checksums.items():
            filename(name)
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("invalid_sha256")
        counts = dataset["topicCounts"]
        if (set(counts) != set(TOPICS)
                or any(type(count) is not int or count <= 0 for count in counts.values())):
            raise ValueError("positive_expected_topic_counts_required")
        for field in ("databaseBytes", "messageCount"):
            if type(dataset[field]) is not int or dataset[field] <= 0:
                raise ValueError("positive_expected_size_and_count_required")
        if sum(counts.values()) != dataset["messageCount"]:
            raise ValueError("manifest_message_count_mismatch")


def verify(manifest, directory):
    validate_manifest(manifest)
    root = Path(directory).resolve(strict=True)
    result = {
        "purpose": "private_recorded_copy_integrity_not_slam",
        "datasets": {}, "rawDataPublished": False, "navigationValidated": False,
    }
    for label, expected in manifest["datasets"].items():
        bag = root / expected["directoryName"]
        if bag.is_symlink() or not bag.is_dir():
            raise ValueError("direct_existing_bag_directory_required")
        actual_files = {path.name for path in bag.iterdir() if path.is_file()}
        if actual_files != set(expected["sha256"]):
            raise ValueError("unexpected_or_missing_bag_files")
        counts = {name: 0 for name in TOPICS}
        total_bytes = 0
        for name, digest in expected["sha256"].items():
            path = bag / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("direct_existing_bag_file_required")
            if sha256(path) != digest:
                raise ValueError("capture_checksum_mismatch_" + label + "_" + name)
            if not name.endswith(".db3"):
                continue
            total_bytes += path.stat().st_size
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as database:
                if database.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise ValueError("invalid_capture_database")
                observed = dict(database.execute(
                    "SELECT topics.name, count(*) FROM messages "
                    "JOIN topics ON messages.topic_id = topics.id GROUP BY topics.name"
                ))
                if set(observed) - set(TOPICS.values()):
                    raise ValueError("unexpected_recorded_topics")
                if sum(observed.values()) != database.execute("SELECT count(*) FROM messages").fetchone()[0]:
                    raise ValueError("messages_without_topic_metadata")
                for alias, topic in TOPICS.items():
                    counts[alias] += observed.get(topic, 0)
        if total_bytes != expected["databaseBytes"] or counts != expected["topicCounts"]:
            raise ValueError("capture_size_or_topic_count_mismatch")
        result["datasets"][label] = {
            "filesVerified": len(expected["sha256"]), "databaseBytes": total_bytes,
            "messageCount": sum(counts.values()), "topicCounts": counts,
        }
    result["result"] = "all_declared_files_and_topic_counts_match"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    print(json.dumps(verify(manifest, args.data_root), indent=2))


if __name__ == "__main__":
    main()
