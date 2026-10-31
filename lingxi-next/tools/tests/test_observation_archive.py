"""Isolated synthetic acquisition geometry, not physical calibration evidence."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    import numpy as np
    from scipy.spatial.transform import Rotation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-slam"))
    from lingxi_slam.observation_archive import ObservationArchive, inspect_observations, sha256


def pose(x=0, yaw=0):
    value = np.eye(4)
    value[0, 3] = x
    value[:3, :3] = Rotation.from_euler("z", yaw, degrees=True).as_matrix()
    return value


def archive_bytes(*, global_pose=None, local_pose=None, reference_pose=None, extrinsic_pose=None,
                  trajectory=None, source=None, extra=b""):
    global_pose = pose() if global_pose is None else global_pose
    local_pose = pose() if local_pose is None else local_pose
    source = np.array([[0, 1, 0, 0], [.5, 1, 0, 0], [1, 1, 0, 0]]) if source is None else source
    trajectory = np.array([[10, 0, 0, 0, 0, 0, 0, 1],
                           [11, 2, 0, 0, *Rotation.from_euler("z", 90, degrees=True).as_quat()]]) if trajectory is None else trajectory
    matrix = lambda value: np.asarray(value, dtype="<f8").tobytes()
    return (b"LXOBS002" + struct.pack("<4Q", 1, 1, len(source), 2) + struct.pack("<Q", 0)
            + matrix(global_pose) + struct.pack("<Q", 1) + struct.pack("<QddQQQ", 7, 10, 11, len(source), len(trajectory), 1)
            + matrix(pose() if reference_pose is None else reference_pose)
            + matrix(pose() if extrinsic_pose is None else extrinsic_pose) + matrix(local_pose)
            + matrix(trajectory) + matrix(source) + extra)


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python runtime")
class ObservationArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name).absolute() / "isolated-synthetic-observations.bin"

    def read(self, **changes):
        self.path.write_bytes(archive_bytes(**changes))
        return ObservationArchive(self.path, sha256(self.path))

    def test_moving_acquisition_origins_and_rotation_are_not_one_scan_origin(self):
        scan, = self.read()
        np.testing.assert_allclose(scan.origins_raw, [[0, 0, 0], [1, 0, 0], [2, 0, 0]], atol=1e-12)
        np.testing.assert_allclose(scan.endpoints_raw, [[1, 0, 0], [1 + 2**-.5, 2**-.5, 0], [2, 1, 0]], atol=1e-12)
        self.assertEqual((0, 7, 10, 0), (scan.ordinal, scan.frame_id, scan.stamp, scan.submap_id))
        self.assertFalse(scan.origins_raw.flags.writeable)
        self.assertFalse(scan.endpoints_raw.flags.writeable)

    def test_local_and_global_corrections_transform_both_ray_ends(self):
        original, = self.read()
        corrected, = self.read(global_pose=pose(10, 90), local_pose=pose(2))
        transform = pose(10, 90) @ pose(2)
        for first, second in ((original.origins_raw, corrected.origins_raw), (original.endpoints_raw, corrected.endpoints_raw)):
            np.testing.assert_allclose(second, first @ transform[:3, :3].T + transform[:3, 3], atol=1e-12)
        self.assertEqual(original.source_sha256, corrected.source_sha256)

    def test_nonidentity_lidar_from_imu_extrinsic_and_reference_compose_in_order(self):
        reference, extrinsic = pose(2, 20), pose()
        reference[:3, 3] = [2, 3, 4]
        extrinsic[:3, :3] = Rotation.from_euler("x", 30, degrees=True).as_matrix()
        extrinsic[:3, 3] = [.2, -.1, .3]
        global_pose, local_pose = pose(-2, -10), pose(3, 40)
        trajectory = np.array([[10, 2, 3, 4, *Rotation.from_euler("z", 20, degrees=True).as_quat()],
                               [11, 4, 5, 6, *Rotation.from_euler("z", 110, degrees=True).as_quat()]])
        scan, = self.read(global_pose=global_pose, local_pose=local_pose, reference_pose=reference,
                          extrinsic_pose=extrinsic, trajectory=trajectory)
        for i, fraction in enumerate((0, .5, 1)):
            at_return = pose(yaw=20 + 90 * fraction)
            at_return[:3, 3] = [2 + 2 * fraction, 3 + 2 * fraction, 4 + 2 * fraction]
            map_lidar = global_pose @ local_pose @ np.linalg.inv(reference) @ at_return @ np.linalg.inv(extrinsic)
            np.testing.assert_allclose(scan.origins_raw[i], (map_lidar @ [0, 0, 0, 1])[:3], atol=1e-12)
            np.testing.assert_allclose(scan.endpoints_raw[i], (map_lidar @ [1, 0, 0, 1])[:3], atol=1e-12)

    def test_summary_never_certifies_metric_scale_or_navigation(self):
        self.read()
        report = inspect_observations(self.path, sha256(self.path))
        self.assertEqual((1, 1, 3, 2), tuple(report[k] for k in ("submaps", "scans", "returns", "revision")))
        self.assertEqual("native-raw", report["coordinateUnits"])
        self.assertFalse(report["physicalScaleValidated"])
        self.assertFalse(report["navigationReady"])

    def test_no_extrapolation_or_clamping_outside_trajectory(self):
        for source in (np.array([[1.01, 1, 0, 0]]), np.array([[-.01, 1, 0, 0]])):
            with self.subTest(source=source), self.assertRaises(ValueError):
                list(self.read(source=source))

    def test_nonfinite_unordered_and_nonrigid_geometry_is_rejected(self):
        for changes in ({"global_pose": np.zeros((4, 4))}, {"local_pose": np.full((4, 4), np.nan)},
                        {"source": np.array([[.5, 1, 0, 0], [.1, 1, 0, 0]])},
                        {"trajectory": np.zeros((2, 8))}, {"source": np.array([[0, np.nan, 0, 0]])}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                list(self.read(**changes))

    def test_truncation_extra_bytes_and_count_mismatch_never_complete(self):
        good = archive_bytes()
        count_mismatch = good[:16] + struct.pack("<Q", 2) + good[24:]
        for data in (good[:-1], good + b"trailing", count_mismatch):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                self.path.write_bytes(data)
                list(ObservationArchive(self.path, sha256(self.path)))

    def test_digest_change_before_or_during_iteration_is_rejected(self):
        archive = self.read()
        iterator = iter(archive)
        next(iterator)
        self.path.write_bytes(archive_bytes(global_pose=pose(10)))
        with self.assertRaises(ValueError):
            next(iterator)
        with self.assertRaises(ValueError):
            ObservationArchive(self.path, archive.sha256)

    def test_huge_header_is_rejected_before_point_allocation(self):
        self.path.write_bytes(b"LXOBS002" + struct.pack("<4Q", 1, 1, 16777217, 1))
        with self.assertRaisesRegex(ValueError, "bounded_complete_observation_header"):
            ObservationArchive(self.path, sha256(self.path))

    def test_source_reference_cannot_be_replaced_with_a_later_frame_pose(self):
        with self.assertRaisesRegex(ValueError, "reference_must_match"):
            list(self.read(reference_pose=pose(1)))


if __name__ == "__main__":
    unittest.main()
