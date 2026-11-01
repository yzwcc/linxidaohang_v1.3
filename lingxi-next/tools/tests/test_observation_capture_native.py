"""Optional actual GLIM callback/retention checks; all inputs are isolated synthetic data."""
import importlib.util
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
LIBRARY = Path(os.environ.get("LINGXI_OBSERVATION_LIBRARY", "/nonexistent-optional-observer"))
WORK = Path(os.environ.get("LINGXI_OBSERVATION_WORK", "/nonexistent-optional-core"))
ROOT = Path(__file__).resolve().parents[2]
if AVAILABLE:
    import numpy as np
    sys.path.insert(0, str(ROOT / "robot-slam"))
    from lingxi_slam.observation_archive import ObservationArchive, sha256
    from lingxi_slam.observation_window import decode_window


@unittest.skipUnless(AVAILABLE and sys.platform == "linux" and LIBRARY.is_file() and WORK.is_dir(),
                     "requires explicit actual observed mapper and isolated Linux core")
class NativeObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.binary = cls.root / "isolated-native-observations"
        command = ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread",
                   "-I" + str(ROOT / "tools/glim-observations"), "-isystem", str(WORK / "prefix/include"),
                   "-isystem", str(WORK / "sysroot/usr/include/eigen3"),
                   "-isystem", str(WORK / "sysroot/usr/include"),
                   str(ROOT / "tools/tests/native/test_observation_capture.cpp"), str(LIBRARY),
                   "-L" + str(WORK / "prefix/lib"), "-lglim", "-Wl,-rpath," + str(LIBRARY.parent),
                   "-o", str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.window_binary = cls.root / "isolated-native-window"
        command = [str(value).replace("test_observation_capture.cpp", "test_observation_window.cpp")
                   .replace(str(cls.binary), str(cls.window_binary)) for value in command]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def execute(self, mode):
        target = self.root / (mode + ".bin")
        result = subprocess.run([str(self.binary), mode, str(target)],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return target, result.stdout

    def test_real_callbacks_preserve_sources_after_point_drop_and_transform_both_ray_ends(self):
        first, _ = self.execute("base")
        second, _ = self.execute("corrected")
        original, = ObservationArchive(first, sha256(first))
        corrected, = ObservationArchive(second, sha256(second))
        np.testing.assert_allclose(original.origins_raw, [[2, 0, 0], [3, 0, 0], [4, 0, 0]], atol=1e-12)
        rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        for a, b in ((original.origins_raw, corrected.origins_raw), (original.endpoints_raw, corrected.endpoints_raw)):
            np.testing.assert_allclose(b, (a - [10, 0, 0]) @ rotation.T + [20, 0, 0], atol=1e-12)
        self.assertEqual(original.source_sha256, corrected.source_sha256)

    def test_missing_or_unbracketed_trajectory_never_fabricates_an_origin(self):
        for mode in ("unbracketed", "missing-trajectory"):
            with self.subTest(mode=mode):
                path, output = self.execute(mode)
                self.assertFalse(path.exists())
                self.assertIn("invalid acquisition rejected", output)

    def test_original_lio_trajectory_covers_scan_when_smoothed_interval_is_short(self):
        path, _ = self.execute("fallback")
        scan, = ObservationArchive(path, sha256(path))
        self.assertEqual("original_lio_imu", scan.trajectory_kind)
        np.testing.assert_allclose(scan.origins_raw, [[2, 0, 0], [3, 0, 0], [4, 0, 0]], atol=1e-12)

    def test_acquisition_reference_is_not_a_later_fixed_lag_frame_pose(self):
        path, _ = self.execute("reference-shift")
        scan, = ObservationArchive(path, sha256(path))
        np.testing.assert_allclose(scan.origins_raw, [[2, 0, 0], [3, 0, 0], [4, 0, 0]], atol=1e-12)

    def test_native_lidar_from_imu_rotation_and_lever_arm_are_retained(self):
        path, _ = self.execute("extrinsic")
        scan, = ObservationArchive(path, sha256(path))
        origins = np.array([[1, -3, 2], [3 + 2**.5, -2 * 2**.5, 2], [7, -1, 2]])
        directions = np.array([[1, 0, 0], [2**-.5, 2**-.5, 0], [0, 1, 0]])
        np.testing.assert_allclose(scan.origins_raw, origins, atol=1e-12)
        np.testing.assert_allclose(scan.endpoints_raw, origins + directions, atol=1e-12)

    def test_continuous_windows_are_bounded_coherent_corrected_and_match_final_archive(self):
        for mode, first, count, returns in (("rollover", 4, 8, 24), ("point-limit", 8, 4, 262144),
                                           ("joint-limits", 8, 4, 262144)):
            with self.subTest(mode=mode):
                path = self.root / (mode + ".window")
                result = subprocess.run([str(self.window_binary), mode, str(path)],
                                        capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise RuntimeError(result.stderr)
                def read(suffix=""):
                    data = Path(str(path) + suffix).read_bytes()
                    return decode_window(struct.unpack("<7Q", data[:56]), data[56:])
                initial, window, corrected = read(".first"), read(), read(".corrected")
                self.assertEqual((1, 0, 3), (initial.revision, initial.first_ordinal, len(initial.observations)))
                self.assertEqual((4, first, count), (window.revision, window.first_ordinal, len(window.observations)))
                self.assertEqual(returns, sum(len(scan.origins_raw) for scan in window.observations))
                self.assertEqual(5, corrected.revision)
                archive_path = Path(str(path) + ".archive")
                archive = tuple(ObservationArchive(archive_path, sha256(archive_path)))
                for before, after, saved in zip(window.observations, corrected.observations, archive[first:]):
                    self.assertEqual((before.stamp, before.frame_id, before.source_sha256),
                                     (after.stamp, after.frame_id, after.source_sha256))
                    self.assertEqual((saved.ordinal, saved.submap_id, saved.source_sha256),
                                     (after.ordinal, after.submap_id, after.source_sha256))
                    np.testing.assert_array_equal(saved.origins_raw, after.origins_raw)
                    np.testing.assert_array_equal(saved.endpoints_raw, after.endpoints_raw)
                self.assertFalse(corrected.navigation_ready)


if __name__ == "__main__":
    unittest.main()
