from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inspect_reference_capture_timing import CLOUD, IMU, TOPICS, inspect, stamp_ns


def cdr(sec, ns, endian="<"):
    return (b"\0\1\0\0" if endian == "<" else b"\0\0\0\0") + struct.pack(endian + "iI", sec, ns)


def shard(bag, index, messages):
    with closing(sqlite3.connect(bag / f"rosbag_{index}.db3")) as db:
        db.executescript("CREATE TABLE topics (id INTEGER, name TEXT, type TEXT, serialization_format TEXT);"
                         "CREATE TABLE messages (id INTEGER PRIMARY KEY, topic_id INTEGER, timestamp INTEGER, data BLOB);")
        # Topic IDs are different in the two shards, so name-based joining is exercised.
        ids = {name: (index + 1) * 10 + i for i, name in enumerate(TOPICS)}
        db.executemany("INSERT INTO topics VALUES (?, ?, ?, 'cdr')",
                       [(ids[name], name, kind) for name, (_, kind) in TOPICS.items()])
        db.executemany("INSERT INTO messages(topic_id, timestamp, data) VALUES (?, ?, ?)",
                       [(ids[name], 100_000_000_000 + stamp * 1_000_000, cdr(100, header * 1_000_000))
                        for name, stamp, header in messages])
        db.commit()


class ReferenceCaptureTimingTests(unittest.TestCase):
    def test_cdr_header_is_exact_in_both_supported_byte_orders(self):
        self.assertEqual(stamp_ns(cdr(2, 500, "<")), 2_000_000_500)
        self.assertEqual(stamp_ns(cdr(2, 500, ">")), 2_000_000_500)
        for data in (b"", b"\0\x09" + cdr(1, 0)[2:], cdr(1, 1_000_000_000)):
            with self.assertRaises(ValueError):
                stamp_ns(data)

    def test_split_bag_retains_header_resets_and_reports_missing_stream_without_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bag = Path(tmp)
            (bag / "metadata.yaml").touch()
            shard(bag, 0, [(CLOUD, 20, 0), (IMU, 24, 10), (CLOUD, 120, 100), ("/cloud_registered_body", 155, 100)])
            shard(bag, 1, [(CLOUD, 220, 200), (IMU, 250, 240), (CLOUD, 320, 190)])
            before = {p.name: p.read_bytes() for p in bag.iterdir()}
            report = inspect(bag, .15)
            self.assertEqual({p.name: p.read_bytes() for p in bag.iterdir()}, before)
            self.assertFalse(report["queueStateMeasured"])
            self.assertEqual(report["shards"], 2)
            lidar = report["topics"]["lidarInput"]
            self.assertEqual(lidar["full"]["headerGap"]["backwards"], 1)
            self.assertEqual(lidar["full"]["recorderGap"]["maxMs"], 100)
            self.assertEqual(lidar["tail"]["messages"], 2)
            self.assertEqual(report["topics"]["imuInput"]["full"]["headerGap"]["maxMs"], 230)
            self.assertEqual(report["topics"]["odometry"], {"full": None, "tail": None})

    def test_requires_finalized_metadata_and_rejects_active_sqlite_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            bag = Path(tmp)
            shard(bag, 0, [(CLOUD, 20, 0)])
            with self.assertRaisesRegex(ValueError, "closed_rosbag_metadata_required"):
                inspect(bag, 5)
            (bag / "metadata.yaml").touch()
            (bag / "rosbag_0.db3-wal").touch()
            with self.assertRaisesRegex(ValueError, "closed_regular_sqlite_shard_required"):
                inspect(bag, 5)
