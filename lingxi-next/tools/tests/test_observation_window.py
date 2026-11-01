"""Strict in-memory window contracts; no sensor, map admission or movement."""
import ctypes
import importlib.util
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-slam"))
    from lingxi_slam.observation_archive import ObservationArchive
    from lingxi_slam.observation_window import checked_header, decode_window, read_window
    from lingxi_slam.observed_mapping import ObservedMappingLibrary
    from test_observation_archive import archive_bytes, pose


@unittest.skipUnless(AVAILABLE, "requires numerical Python runtime")
class ObservationWindowTests(unittest.TestCase):
    def header(self, data):
        return (2, 4, 2, 1, 1, 3, len(data))

    def test_complete_geometry_reuses_archive_validation_without_file_io(self):
        data = archive_bytes()
        window = decode_window(self.header(data), data)
        reference, = ObservationArchive.from_bytes(data)
        scan, = window.observations
        self.assertEqual((4, 2, 7, 10), (scan.ordinal, scan.submap_id, scan.frame_id, scan.stamp))
        np.testing.assert_array_equal(reference.origins_raw, scan.origins_raw)
        self.assertEqual("delayed_finalized_submaps", window.input_kind)
        self.assertFalse(window.navigation_ready)
        self.assertFalse(scan.origins_raw.flags.writeable)
        self.assertFalse(hasattr(window, "received_at"))

    def test_correction_does_not_refresh_acquisition_stamp_or_source_identity(self):
        first, second = archive_bytes(), archive_bytes(global_pose=pose(3, 90))
        a, = decode_window(self.header(first), first).observations
        b, = decode_window(self.header(second), second).observations
        self.assertEqual((a.stamp, a.frame_id, a.source_sha256), (b.stamp, b.frame_id, b.source_sha256))
        self.assertFalse(np.allclose(a.origins_raw, b.origins_raw))

    def test_bad_headers_rejected_before_payload_allocation(self):
        for header in ((0, 1, 0, 0, 0, 0, 0), (1, 0, 0, 1, 9, 27, 2000),
                       (1, 0, 0, 1, 1, 262145, 2000), (1, 0, 0, 1, 1, 3, 16 * 1024**2 + 1),
                       (1, 4096, 0, 1, 1, 3, 2000), (1, 0, 1, 1, 1, 3, 2000)):
            with self.subTest(header=header), self.assertRaises(ValueError):
                checked_header(header)

    def test_truncation_trailing_bytes_and_wrong_revision_never_publish_partial_geometry(self):
        good = archive_bytes()
        for data in (good[:-1], good + b"trailing", b"BADMAGIC" + good[8:]):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                decode_window(self.header(data), data)
        with self.assertRaises(ValueError):
            decode_window((3, *self.header(good)[1:]), good)
        with self.assertRaises(ValueError):
            ObservationArchive.from_bytes(bytearray(good))

    def test_copy_race_retries_and_never_reads_failed_buffer(self):
        data, copies = archive_bytes(), []
        def call(header, buffer, capacity, expected):
            header[:] = self.header(data)
            if buffer is None:
                return 0
            copies.append(expected)
            if len(copies) == 1:
                return 1
            self.assertEqual(len(data), capacity)
            ctypes.memmove(buffer, data, len(data))
            return 0
        self.assertEqual(2, read_window(call).revision)
        self.assertEqual([2, 2], copies)
        self.assertIsNone(read_window(call, after_revision=2))
        with self.assertRaises(ValueError):
            read_window(call, after_revision=True)

    def test_bounded_retries_empty_and_native_failure(self):
        data = archive_bytes()
        def changing(header, buffer, capacity, expected):
            header[:] = self.header(data)
            return 0 if buffer is None else 1
        with self.assertRaisesRegex(RuntimeError, "retry_later"):
            read_window(changing)
        self.assertIsNone(read_window(lambda *args: 0))
        with self.assertRaises(RuntimeError):
            read_window(lambda *args: -1)

    def mapper(self):
        mapper = ObservedMappingLibrary.__new__(ObservedMappingLibrary)
        mapper.owner = threading.get_ident()
        mapper.opened, mapper.closed = True, False
        mapper.library = SimpleNamespace(lingxi_observation_window=Mock())
        mapper.check_health = Mock()
        return mapper

    def test_mapper_checks_worker_health_before_and_after_read(self):
        mapper = self.mapper()
        with patch("lingxi_slam.observed_mapping.read_window", return_value="synthetic") as read:
            self.assertEqual("synthetic", mapper.observation_window(after_revision=7))
            self.assertEqual(2, mapper.check_health.call_count)
            read.assert_called_once_with(mapper.library.lingxi_observation_window, after_revision=7)
            for outcomes in ((RuntimeError("failed worker"),), (None, RuntimeError("failed worker"))):
                mapper.check_health.side_effect = outcomes
                with self.assertRaisesRegex(RuntimeError, "failed worker"):
                    mapper.observation_window()

    def test_mapper_rejects_other_owner_unopened_and_closed_reads(self):
        mapper = self.mapper()
        for owner, opened, closed in ((-1, True, False), (mapper.owner, False, False),
                                      (mapper.owner, True, True)):
            mapper.owner, mapper.opened, mapper.closed = owner, opened, closed
            with self.assertRaises(RuntimeError):
                mapper.observation_window()
        mapper.check_health.assert_not_called()


if __name__ == "__main__":
    unittest.main()
