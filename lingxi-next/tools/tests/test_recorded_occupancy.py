"""Synthetic acquisition fixtures; optional real OctoMap execution, no robot IO."""
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    import numpy as np
    sys.path[:0] = [str(ROOT / "robot-navigation"), str(ROOT / "robot-slam")]
    from lingxi_navigation.recorded_occupancy import (OfflineGeometry, RaySelection, prepare_recorded_clearance)
    from lingxi_navigation.occupancy import (ClearanceProjection, DeclaredObservationBatch, OctomapProjector)
    from lingxi_navigation.navfn import NavfnPlanner
    from lingxi_slam.observation_archive import sha256
    from lingxi_slam.observation_binding import bind_observations
    from test_observation_archive import archive_bytes, pose


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python runtime")
class RecordedOccupancyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.saved = Path(self.directory.name).absolute() / ("a" * 32)
        self.saved.mkdir()
        self.inventory = {"num_submaps": 1, "num_all_frames": 1, "artifactInventorySha256": "a" * 64}
        self.provenance = {"schemaVersion": 1, "profileSha256": "b" * 64,
            "timingHypothesis": "header_is_scan_start", "configurationSha256": {"fixture.json": "c" * 64}}
        self.receipt = {"schemaVersion": 3, "session": self.saved.name, "mappingEngine": "glim-full-async-v1",
            "inputMode": "recorded", "freshProcessReloadPassed": True, "navigationReady": False,
            "sensorProvenance": self.provenance, "mapInventory": self.inventory,
            "counts": {"submaps": 1, "savedFrames": 1}}
        self.write_sources()
        self.geometry = OfflineGeometry(1., tuple(map(float, np.eye(4).ravel())))

    def write_sources(self, **changes):
        (self.saved / "map-ready.json").write_text(json.dumps(self.receipt), encoding="utf-8")
        (self.saved / "private-observations.bin").write_bytes(archive_bytes(**changes))
        binding = self.saved / "observations-ready.json"
        if binding.exists():
            binding.unlink()
        bind_observations(self.saved, self.inventory, self.provenance, "d" * 64)
        self.receipt_hash, self.binding_hash = sha256(self.saved / "map-ready.json"), sha256(binding)

    def prepare(self, **changes):
        return prepare_recorded_clearance(self.saved, **{ "receipt_sha256": self.receipt_hash,
            "binding_sha256": self.binding_hash, "implementation_sha256": "d" * 64,
            "inspect_map": lambda path: self.inventory, "geometry": self.geometry, **changes})

    def test_preserves_individual_origins_and_does_not_certify_declared_metres(self):
        value = self.prepare()
        self.assertIs(type(value.batch), DeclaredObservationBatch)
        self.assertEqual([0., 1., 2.], [ray.origin_metres[0] for ray in value.batch.scans[0].returns])
        self.assertEqual((3, 3), (value.source_returns, value.selected_returns))
        summary = value.summary()
        self.assertEqual("declared-metres", summary["units"])
        for name in ("physicalScaleValidated", "groundFrameValidated", "groundSupportValidated",
                     "obstacleCompletenessValidated", "navigationReady"):
            self.assertIs(summary[name], False)
        self.assertFalse(hasattr(value.batch, "metric_evidence_sha256"))

    def test_scale_rotation_and_ground_offset_apply_equally_to_both_ray_ends(self):
        original = self.prepare()
        matrix = pose(4, 90)
        matrix[2, 3] = 1.2
        declaration = OfflineGeometry(.5, tuple(map(float, matrix.ravel())))
        transformed = self.prepare(geometry=declaration)
        for old, new in zip(original.batch.scans[0].returns, transformed.batch.scans[0].returns):
            for name in ("origin_metres", "endpoint_metres"):
                expected = matrix[:3, :3] @ (np.array(getattr(old, name)) * .5) + matrix[:3, 3]
                np.testing.assert_allclose(getattr(new, name), expected, atol=1e-12)
        self.assertNotEqual(original.batch.sha256, transformed.batch.sha256)

    def test_selection_is_deterministic_budgeted_and_preserves_measured_pairs(self):
        source = np.column_stack((np.linspace(0, 1, 10000), np.full(10000, 1.), np.zeros((10000, 2))))
        self.write_sources(source=source)
        spec = RaySelection(maximum_rays=4096, maximum_per_scan=100)
        first, second = self.prepare(selection=spec), self.prepare(selection=spec)
        self.assertEqual((10000, 100), (first.source_returns, first.selected_returns))
        self.assertEqual(first.batch.sha256, second.batch.sha256)
        self.assertEqual(9900, first.summary()["omittedReturns"])
        original_indices = spec.indices(10000, 1)
        np.testing.assert_allclose([ray.origin_metres[0] for ray in first.batch.scans[0].returns],
                                   source[original_indices, 0] * 2, atol=1e-12)
        self.assertTrue(np.all(np.diff(original_indices) > 0))
        for scans in (1, 313, 1190, 4096):
            self.assertLessEqual(len(spec.indices(65536, scans)) * scans, spec.maximum_rays)

    def test_changed_corrected_pose_or_ground_declaration_needs_new_derived_identity(self):
        original = self.prepare()
        source_hash = original.batch.scans[0].source_sha256
        self.write_sources(global_pose=pose(2, 90))
        corrected = self.prepare()
        self.assertEqual(source_hash, corrected.batch.scans[0].source_sha256)
        self.assertNotEqual(original.source_geometry_sha256, corrected.source_geometry_sha256)
        self.assertNotEqual(original.batch.sha256, corrected.batch.sha256)
        self.assertNotEqual(original.batch.scans[0].returns, corrected.batch.scans[0].returns)

    def test_complete_archive_digest_and_actual_map_inventory_are_required(self):
        with self.assertRaises(ValueError):
            self.prepare(inspect_map=lambda path: {**self.inventory, "artifactInventorySha256": "e" * 64})
        for changes in ({"receipt_sha256": "e" * 64}, {"binding_sha256": "f" * 64},
                        {"implementation_sha256": "0" * 64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(**changes)
        archive = self.saved / "private-observations.bin"
        archive.write_bytes(archive.read_bytes() + b"trailing")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_change_during_conversion_never_returns_partial_batch(self):
        calls = []
        def inspect(path):
            calls.append(path)
            if len(calls) == 2:
                (self.saved / "private-observations.bin").write_bytes(archive_bytes(global_pose=pose(3)))
            return self.inventory
        with self.assertRaises(ValueError):
            self.prepare(inspect_map=inspect)
        self.assertEqual(2, len(calls))

    def test_not_a_retrofit_for_old_live_or_synthetic_maps(self):
        for mode in ("live", "synthetic"):
            self.receipt["inputMode"] = mode
            self.write_sources()
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "recorded_map_session"):
                self.prepare()

    def test_invalid_nonrigid_mirrored_and_nonfinite_declarations_are_rejected(self):
        for matrix in (np.zeros((4, 4)), np.diag([-1., 1, 1, 1]), np.full((4, 4), np.nan)):
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                OfflineGeometry(1., tuple(map(float, matrix.ravel())))
        for scale in (True, 0, -1., float("nan"), 1001):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                replace(self.geometry, metres_per_raw_unit=scale)
        for spec in ({"maximum_rays": 0}, {"maximum_per_scan": 0}, {"maximum_rays": True}):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                RaySelection(**spec)

    @unittest.skipUnless(os.environ.get("LINGXI_TEST_OCTOMAP_LIBRARY"), "requires real pinned OctoMap")
    def test_real_octomap_keeps_declared_grid_separate_from_metric_planning(self):
        projector = OctomapProjector(Path(os.environ["LINGXI_TEST_OCTOMAP_LIBRARY"]),
            os.environ["LINGXI_TEST_OCTOMAP_SHA256"], json.loads(os.environ["LINGXI_TEST_OCTOMAP_DEPENDENCIES"]))
        prepared = self.prepare()
        projection = ClearanceProjection(64, 64, .1, -1., -1.)
        result = projector.project_declared(prepared.batch, projection)
        self.assertEqual(4096, len(result.costs))
        self.assertEqual((3, 1), result.statistics[:2])
        self.assertEqual(4096, sum(result.statistics[3:6]))
        self.assertFalse(result.navigation_ready)
        self.assertFalse(result.physical_scale_validated)
        self.assertFalse(hasattr(result, "costmap"))
        with self.assertRaisesRegex(ValueError, "not_offline_declaration"):
            projector.project(prepared.batch, projection)
        with self.assertRaisesRegex(ValueError, "costmap_and_bounded"):
            NavfnPlanner.plan(type("Planner", (), {"owner": __import__("threading").get_ident()})(), result, (0, 0), (1, 0))
        self.assertEqual(result.sha256, projector.project_declared(prepared.batch, projection).sha256)


if __name__ == "__main__":
    unittest.main()
