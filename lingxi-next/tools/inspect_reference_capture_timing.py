#!/usr/bin/env python3
"""Inspect timestamp evidence in a closed X2 sqlite3 recording without ROS.

Bag timestamps come from the recorder, not from the FAST-LIO input callbacks.
This cannot reconstruct internal queue state or prove the cause of an abort.
"""

import argparse
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import statistics
import struct

from check_reference_bag import CLOUD, IMU


TOPICS = {
    CLOUD: ("lidarInput", "sensor_msgs/msg/PointCloud2"),
    IMU: ("imuInput", "sensor_msgs/msg/Imu"),
    "/cloud_registered_body": ("registeredCloud", "sensor_msgs/msg/PointCloud2"),
    "/Odometry": ("odometry", "nav_msgs/msg/Odometry"),
}
NS_PER_MS = 1_000_000


def stamp_ns(data):
    # All four selected CDR types begin with std_msgs/Header and its Time.
    endian = {b"\0\0": ">", b"\0\1": "<"}.get(data[:2])
    if not endian or len(data) != 12:
        raise ValueError("invalid_plain_cdr_header")
    sec, ns = struct.unpack_from(endian + "iI", data, 4)
    if sec < 0 or ns >= 1_000_000_000:
        raise ValueError("invalid_message_stamp")
    return sec * 1_000_000_000 + ns


def ms(value):
    return round(value / NS_PER_MS, 3)


def sample_metrics(rows, start):
    if not rows:
        return None
    # Rows are in recorder insertion order, so resets are not sorted away.
    received, headers = zip(*rows)
    ages = [r - h for r, h in rows]

    def gaps(stamps):
        deltas = [b - a for a, b in zip(stamps, stamps[1:])]
        if not deltas:
            return None
        index = max(range(len(deltas)), key=deltas.__getitem__)
        return dict(medianMs=ms(statistics.median(deltas)), maxMs=ms(deltas[index]),
                    maxGapEndsAtMs=ms(received[index + 1] - start),
                    backwards=sum(d < 0 for d in deltas), duplicates=sum(d == 0 for d in deltas))

    return dict(messages=len(rows), firstRecordedMs=ms(min(received) - start),
                lastRecordedMs=ms(max(received) - start),
                headerGap=gaps(headers), recorderGap=gaps(received),
                recorderMinusHeaderMs=dict(minimum=ms(min(ages)), median=ms(statistics.median(ages)), maximum=ms(max(ages))))


def inspect(directory, tail_seconds):
    directory = directory.resolve(strict=True)
    if not (directory / "metadata.yaml").is_file():
        raise ValueError("closed_rosbag_metadata_required")
    shards = sorted(((int(m[1]), p) for p in directory.iterdir()
                     if (m := re.fullmatch(r"rosbag_(\d+)\.db3", p.name))), key=lambda item: item[0])
    if not shards or [i for i, _ in shards] != list(range(len(shards))):
        raise ValueError("expected_contiguous_rosbag_sqlite_shards")
    names = tuple(TOPICS)
    slots = ",".join("?" for _ in names)
    samples = defaultdict(list)
    for _, file in shards:
        if (file.is_symlink() or not file.is_file()
                or any(Path(str(file) + suffix).exists() for suffix in ("-wal", "-journal", "-shm"))):
            raise ValueError("closed_regular_sqlite_shard_required")
        with closing(sqlite3.connect(file.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            for name, kind, format_ in db.execute(f"SELECT name, type, serialization_format FROM topics WHERE name IN ({slots})", names):
                if (kind, format_) != (TOPICS[name][1], "cdr"):
                    raise ValueError("unexpected_reference_topic_serialization")
            for name, recorded, payload in db.execute(
                f"SELECT t.name, m.timestamp, substr(m.data,1,12) FROM messages m JOIN topics t ON m.topic_id=t.id "
                f"WHERE t.name IN ({slots}) ORDER BY m.id", names
            ):
                samples[name].append((recorded, stamp_ns(payload)))
    if not samples:
        raise ValueError("no_supported_capture_messages")
    start = min(t for rows in samples.values() for t, _ in rows)
    end = max(t for rows in samples.values() for t, _ in rows)
    tail_start = end - int(tail_seconds * 1_000_000_000)
    return dict(readOnly=True, queueStateMeasured=False, durationMs=ms(end-start), tailSeconds=tail_seconds,
                shards=len(shards), topics={label: dict(full=sample_metrics(samples[name], start),
                    tail=sample_metrics([row for row in samples[name] if row[0] >= tail_start], start))
                    for name, (label, _) in TOPICS.items()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bag", type=Path, help="Closed recording's rosbag directory (all split sqlite3 files)")
    parser.add_argument("--tail-seconds", type=float, default=5)
    args = parser.parse_args()
    if not 0 < args.tail_seconds <= 60:
        parser.error("tail-seconds must be in (0, 60]")
    print(json.dumps(inspect(args.bag, args.tail_seconds), allow_nan=False))
