"""Actual pinned GICP on synthetic geometry; no robot or private input needed."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
sys.path.insert(0, str(ROOT / "tools"))
AVAILABLE = all(importlib.util.find_spec(name) is not None
                for name in ("numpy", "scipy", "small_gicp"))
if AVAILABLE:
    import numpy as np
    from lingxi_slam.seeded_localization import (
        RegistrationConfig, SeededMapRegistration, rigid_pose, sensor_scan_from_world)
if AVAILABLE and sys.platform == "linux":
    import run_seeded_localization_trial as trial


def room():
    rng = np.random.default_rng(48)
    surfaces = []
    for axis, offset in ((0, -4.), (0, 5.), (1, -3.), (1, 6.), (2, -2.), (2, 3.)):
        points = rng.uniform([-4, -3, -2], [5, 6, 3], (900, 3))
        points[:, axis] = offset
        surfaces.append(points)
    return np.concatenate(surfaces)


def pose():
    value = np.eye(4)
    angle = .24
    value[:3, :3] = [[np.cos(angle), -np.sin(angle), 0],
                     [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
    value[:3, 3] = [1.2, -.8, .3]
    return value


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy/SciPy and pinned small-gicp")
class SeededRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.points, cls.truth = room(), pose()
        cls.scan = sensor_scan_from_world(cls.points, cls.truth)
        cls.seed = cls.truth.copy()
        cls.seed[:3, 3] += [.18, -.12, .08]

    def test_known_transform_direction_and_quality_with_perturbed_seed(self):
        result = SeededMapRegistration(self.points).register(self.scan, self.seed)
        self.assertTrue(result.accepted, (result.reasons, result.metrics))
        np.testing.assert_allclose(result.map_from_sensor, self.truth, atol=.004, rtol=0)
        self.assertGreater(result.metrics["overlap"], .99)
        self.assertLess(result.metrics["inlierRmsRaw"], .004)
        self.assertFalse(result.map_from_sensor.flags.writeable)

    def test_partial_scan_and_noise_still_recover_known_pose(self):
        scan = self.scan[::2] + np.random.default_rng(3).normal(0, .008, self.scan[::2].shape)
        result = SeededMapRegistration(self.points).register(scan, self.seed)
        self.assertTrue(result.accepted, (result.reasons, result.metrics))
        np.testing.assert_allclose(result.map_from_sensor, self.truth, atol=.012, rtol=0)

    def test_wrong_seed_no_overlap_has_no_usable_pose(self):
        seed = self.seed.copy()
        seed[:3, 3] += 100
        result = SeededMapRegistration(self.points).register(self.scan, seed)
        self.assertFalse(result.accepted)
        self.assertIsNone(result.map_from_sensor)
        self.assertIn("insufficient_scan_map_overlap", result.reasons)

    def test_plane_and_line_are_rejected_even_when_alignment_is_exact(self):
        for kind in ("plane", "line"):
            points = np.random.default_rng(7).uniform(-4, 4, (1500, 3))
            points[:, 2] = 0
            if kind == "line":
                points[:, 1] = 0
                points[:, 0] *= 3
            with self.subTest(kind=kind):
                result = SeededMapRegistration(points).register(points, np.eye(4))
                self.assertFalse(result.accepted)
                self.assertIsNone(result.map_from_sensor)
                self.assertIn("degenerate_registration_geometry", result.reasons)

    def test_large_outlier_fraction_is_not_hidden_by_inlier_only_residual(self):
        scan = np.concatenate((self.scan, self.scan + 100))
        result = SeededMapRegistration(self.points).register(scan, self.seed)
        self.assertFalse(result.accepted)
        self.assertIn("insufficient_scan_map_overlap", result.reasons)
        self.assertIsNone(result.map_from_sensor)

    def test_excessive_correction_rejected_and_next_good_call_has_no_stale_result(self):
        config = replace(RegistrationConfig(), maximum_seed_translation_raw=.05)
        registration = SeededMapRegistration(self.points, config)
        bad = registration.register(self.scan, self.seed)
        self.assertFalse(bad.accepted)
        self.assertIn("excessive_seed_correction", bad.reasons)
        self.assertIsNone(bad.map_from_sensor)
        good = registration.register(self.scan, self.truth)
        self.assertTrue(good.accepted, good.reasons)
        bad_seed = self.truth.copy()
        bad_seed[0, 3] += 100
        self.assertIsNone(registration.register(self.scan, bad_seed).map_from_sensor)

    def test_world_to_local_conversion_does_not_transform_world_points_twice(self):
        np.testing.assert_allclose(
            self.scan @ self.truth[:3, :3].T + self.truth[:3, 3], self.points, atol=1e-12)
        np.testing.assert_array_equal(rigid_pose(self.truth), self.truth)

    def test_invalid_poses_and_clouds_rejected_before_native_alignment(self):
        registration = SeededMapRegistration(self.points)
        invalid = [np.zeros((4, 4)), np.eye(3), np.full((4, 4), np.nan), np.eye(4) * 2]
        reflection = np.eye(4)
        reflection[0, 0] = -1
        invalid += [reflection, np.eye(4).astype(str)]
        for seed in invalid:
            with self.subTest(seed=seed.shape), self.assertRaises(ValueError):
                registration.register(self.scan, seed)
        for scan in (np.ones((2, 3)), np.ones((100, 2)), np.full((100, 3), np.nan),
                     np.full((100, 3), np.inf), np.full((100, 3), 1e10),
                     np.ones((100, 3), dtype=bool), np.ones((100, 3)).astype(str),
                     np.ones((100001, 3))):
            with self.subTest(shape=scan.shape), self.assertRaises(ValueError):
                registration.register(scan, self.seed)

    def test_configuration_version_and_downsampled_capacity(self):
        for args in ({"voxel_raw": 0}, {"minimum_overlap": 1.1}, {"maximum_iterations": True},
                     {"maximum_iterations": 101}, {"minimum_points": 2},
                     {"maximum_scan_points": 31}, {"maximum_seed_rotation_radians": 4},
                     {"minimum_geometry_ratio": float("nan")},
                     {"maximum_inlier_rms_raw": 2}, {"minimum_points": 100, "maximum_scan_points": 64}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                RegistrationConfig(**args)
        with patch("lingxi_slam.seeded_localization.version", return_value="9.9"), self.assertRaises(RuntimeError):
            SeededMapRegistration(self.points)
        with self.assertRaisesRegex(ValueError, "after_registration_downsampling"):
            SeededMapRegistration(np.zeros((100, 3)))

    def test_caller_arrays_preserved_and_map_copy_is_owned(self):
        points, scan, seed = self.points.copy(), self.scan.copy(), self.seed.copy()
        registration = SeededMapRegistration(points)
        points[:] = 1e4
        registration.register(scan, seed)
        np.testing.assert_array_equal(registration.map_points, self.points)
        np.testing.assert_array_equal(scan, self.scan)
        np.testing.assert_array_equal(seed, self.seed)

    def test_single_owner_required(self):
        registration, errors = SeededMapRegistration(self.points), []

        def call():
            try:
                registration.register(self.scan, self.seed)
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=call)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(["single_registration_owner_required"], errors)

    def test_native_convergence_flag_alone_cannot_accept_invalid_geometry(self):
        registration = SeededMapRegistration(self.points)
        base = {
            "T_target_source": self.truth, "converged": True, "num_inliers": 100,
            "iterations": 2, "H": np.eye(6),
        }
        for changed in ({"converged": False}, {"num_inliers": 0}, {"H": np.zeros((6, 6))},
                        {"H": np.full((6, 6), np.nan)}, {"T_target_source": np.eye(4) * 2}):
            with self.subTest(changed=list(changed)), patch.object(
                    registration.native, "align", return_value=SimpleNamespace(**{**base, **changed})):
                result = registration.register(self.scan, self.seed)
                self.assertFalse(result.accepted)
                self.assertIsNone(result.map_from_sensor)

    def test_rotation_correction_is_bounded_independently_of_translation(self):
        config = replace(RegistrationConfig(), maximum_seed_rotation_radians=.001)
        seed = self.truth.copy()
        angle = .04
        rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                             [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        seed[:3, :3] = rotation @ seed[:3, :3]
        result = SeededMapRegistration(self.points, config).register(self.scan, seed)
        self.assertFalse(result.accepted)
        self.assertIn("excessive_seed_correction", result.reasons)


@unittest.skipUnless(AVAILABLE and sys.platform == "linux", "requires isolated Linux trial dependencies")
class RecordedTrialTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        saved = root / "saved"
        saved.mkdir()
        points = room()
        points.astype("<f8").tofile(saved / "private-saved-points.f64")
        inventory = {"synthetic": "inventory helper tested separately"}
        receipt = {
            "schemaVersion": 1, "inputMode": "recorded", "counts": {"points": len(points)},
            "freshProcessReloadPassed": True, "navigationReady": False, "localizationReady": False,
            "geometricAccuracyValidated": False, "mapInventory": inventory,
            "savedPointsSha256": trial.digest(saved / "private-saved-points.f64"),
        }
        (saved / "map-ready.json").write_text(json.dumps(receipt), encoding="utf-8")
        scan_path = root / "scan.npz"
        np.savez(scan_path, points=points, world_lidar_pose=pose(),
                 frame_id=np.array(1), algorithm_stamp=np.array(1000.))
        return saved, scan_path, inventory

    def test_complete_aggregate_has_no_pose_coordinates_and_rejects_remote_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            saved, scan_path, inventory = self.fixture(directory)
            before = trial.digest(scan_path)
            with patch.object(trial, "inventory_map", return_value=inventory):
                result = trial.run(saved, [scan_path])
            self.assertEqual(before, trial.digest(scan_path))
            trials = result["scans"][0]["trials"]
            self.assertEqual([True, True, False], [value["acceptedByTrialGate"] for value in trials])
            self.assertFalse(result["boundaries"]["localizationReady"])
            self.assertFalse(result["boundaries"]["navigationReady"])
            self.assertTrue(result["boundaries"]["possibleMappingSequenceReuse"])
            self.assertNotIn("world_lidar_pose", json.dumps(result))
            self.assertNotIn(str(saved), json.dumps(result))
            json.dumps(result, allow_nan=False)

    def test_changed_points_or_receipt_readiness_cannot_enter_trial(self):
        for kind in ("points", "readiness", "inventory", "count"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                saved, _, inventory = self.fixture(directory)
                if kind == "points":
                    with (saved / "private-saved-points.f64").open("ab") as stream:
                        stream.write(b"x")
                else:
                    path = saved / "map-ready.json"
                    receipt = json.loads(path.read_text())
                    if kind == "readiness":
                        receipt["localizationReady"] = True
                    elif kind == "inventory":
                        receipt["mapInventory"] = {}
                    else:
                        receipt["counts"]["points"] = True
                    path.write_text(json.dumps(receipt))
                with patch.object(trial, "inventory_map", return_value=inventory):
                    with self.assertRaises(ValueError):
                        trial.load_saved_points(saved, RegistrationConfig())

    def test_npz_invalid_layout_object_data_and_invalid_frame_are_rejected(self):
        for kind in ("extra", "object", "frame", "stamp"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                _, path, _ = self.fixture(directory)
                fields = {"points": room(), "world_lidar_pose": pose(),
                          "frame_id": np.array(1), "algorithm_stamp": np.array(1000.)}
                if kind == "extra":
                    fields["unexpected"] = np.array([1])
                elif kind == "object":
                    fields["points"] = np.array([object()])
                elif kind == "frame":
                    fields["frame_id"] = np.array(True)
                else:
                    fields["algorithm_stamp"] = np.array(float("nan"))
                np.savez(path, **fields)
                with self.assertRaises(ValueError):
                    trial.load_recorded_scan(path, RegistrationConfig())

    def test_existing_output_rejected_before_input_or_native_access(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.json"
            output.write_text("preserve")
            with patch.object(sys, "argv", [
                    "trial", "--saved-map", directory, "--scan", directory,
                    "--acknowledge-recording-reuse", "--output", str(output)]):
                with patch.object(trial, "run") as run:
                    with self.assertRaisesRegex(ValueError, "preserved"):
                        trial.main()
                    run.assert_not_called()
            self.assertEqual("preserve", output.read_text())

    def test_scan_count_is_bounded_before_file_access(self):
        for paths in ([], [Path("unused")] * 9):
            with self.assertRaisesRegex(ValueError, "one_to_eight"):
                trial.run(Path("unused"), paths)


if __name__ == "__main__":
    unittest.main()
