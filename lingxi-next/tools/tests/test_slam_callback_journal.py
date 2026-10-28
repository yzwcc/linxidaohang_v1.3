"""Only isolated synthetic bytes; no robot, sensor coordinates or ROS."""
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parents[1]))
import slam_callback_journal as journal


class CallbackJournal(unittest.TestCase):
    def create(self, path):
        writer = journal.Writer(path)
        writer.append("imu", 0, b"synthetic-imu")
        writer.append("cloud", 5, b"synthetic-cloud")
        return writer.finish()

    def test_exact_byte_order_counts_integer_times_and_committed_footer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private"
            summary = self.create(path)
            rows = list(journal.records(path))
            self.assertEqual(summary, journal.inspect(path))
            self.assertEqual(["imu", "cloud"], [row.kind for row in rows])
            self.assertEqual([0, 1], [row.sequence for row in rows])
            self.assertEqual([0, 5], [row.elapsed_ns for row in rows])
            self.assertEqual([b"synthetic-imu", b"synthetic-cloud"], [row.payload for row in rows])

    def test_existing_recording_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private"
            self.create(path)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                journal.Writer(path)
            self.assertEqual(before, path.read_bytes())

    def test_unfinished_capture_is_never_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private"
            writer = journal.Writer(path)
            writer.append("imu", 0, b"synthetic")
            writer.abort()
            with self.assertRaisesRegex(ValueError, "footer"):
                journal.inspect(path)

    def test_footer_payload_header_and_trailing_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private"
            self.create(path)
            valid = path.read_bytes()
            cases = [valid[:-1], valid + b"x", b"wrong" + valid[5:]]
            for offset in (len(journal.MAGIC) + journal.RECORD.size, len(valid) - 10):
                changed = bytearray(valid)
                changed[offset] ^= 1
                cases.append(bytes(changed))
            for index, payload in enumerate(cases):
                candidate = Path(directory) / str(index)
                candidate.write_bytes(payload)
                with self.assertRaises(ValueError):
                    journal.inspect(candidate)

    def test_bounds_and_nonmonotonic_time_rejected_before_write(self):
        for options in (
            (-1, 0, 1, 0, 0), (0, -1, 1, 0, 0), (0, True, 1, 0, 0),
            (0, 1, 0, 0, 0), (0, 0, 1, 1, 0),
            (0, journal.MAXIMUM_ELAPSED_NS + 1, 1, 0, 0),
            (journal.MAXIMUM_RECORDS, 0, 1, 0, 0),
            (0, 0, journal.MAXIMUM_PAYLOAD + 1, 0, 0),
            (0, 0, 1, 0, journal.MAXIMUM_TOTAL),
        ):
            with self.assertRaises(ValueError, msg=str(options)):
                journal.validate(*options)

    def test_excessive_length_header_does_not_request_unbounded_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic"
            path.write_bytes(journal.MAGIC + journal.RECORD.pack(
                1, 0, 0, journal.MAXIMUM_PAYLOAD + 1, bytes(32)))
            with self.assertRaisesRegex(ValueError, "bounded"):
                journal.inspect(path)

    def test_single_owner_and_closed_writer_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private"
            writer = journal.Writer(path)
            with patch.object(journal.threading, "get_ident", return_value=writer.owner + 1):
                with self.assertRaises(RuntimeError):
                    writer.append("imu", 0, b"synthetic")
            writer.finish()
            with self.assertRaises(RuntimeError):
                writer.append("imu", 0, b"synthetic")
            writer.abort()

    def test_unknown_kind_and_mutable_payload_are_not_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = journal.Writer(Path(directory) / "private")
            for kind, payload in (("other", b"synthetic"), ("imu", bytearray(b"synthetic"))):
                with self.assertRaises(ValueError):
                    writer.append(kind, 0, payload)
            self.assertEqual(0, writer.count)
            writer.abort()


if __name__ == "__main__":
    unittest.main()
