"""Isolated synthetic binding records; no field-map acceptance is represented."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-slam"))
    from lingxi_slam.observation_binding import bind_observations, verify_observations
    from test_observation_archive import archive_bytes, pose


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python runtime")
class ObservationBindingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.saved = Path(self.directory.name).absolute()
        self.inventory = {"num_submaps": 1, "num_all_frames": 1, "artifactInventorySha256": "a" * 64}
        self.provenance = {"schemaVersion": 1, "profileSha256": "b" * 64, "timingHypothesis": "header_is_scan_start",
                           "configurationSha256": {"isolated-synthetic.json": "c" * 64}}
        self.receipt = {"schemaVersion": 3, "mappingEngine": "glim-full-async-v1", "inputMode": "synthetic",
            "freshProcessReloadPassed": True, "navigationReady": False, "sensorProvenance": self.provenance,
            "mapInventory": self.inventory, "counts": {"submaps": 1, "savedFrames": 1}}
        self.write_receipt()
        (self.saved / "private-observations.bin").write_bytes(archive_bytes())

    def write_receipt(self):
        (self.saved / "map-ready.json").write_text(json.dumps(self.receipt), encoding="utf-8")

    def bind(self):
        return bind_observations(self.saved, self.inventory, self.provenance, "d" * 64)

    def verify(self):
        return verify_observations(self.saved, self.inventory, self.provenance, "d" * 64)

    def test_source_binding_is_additive_and_does_not_rewrite_original_receipt(self):
        original = (self.saved / "map-ready.json").read_bytes()
        binding = self.bind()
        self.assertEqual(binding, self.verify())
        self.assertEqual(original, (self.saved / "map-ready.json").read_bytes())
        self.assertFalse(binding["observations"]["navigationReady"])
        self.assertFalse(binding["observations"]["physicalScaleValidated"])
        self.assertEqual("synthetic", binding["inputMode"])

    def test_pending_receipt_binding_verifies_after_atomic_ready_publication(self):
        self.receipt["observationImplementationSha256"] = "d" * 64
        self.write_receipt()
        ready, pending = self.saved / "map-ready.json", self.saved / "map-ready.pending"
        ready.rename(pending)
        binding = bind_observations(self.saved, self.inventory, self.provenance, "d" * 64, pending=True)
        self.assertFalse(ready.exists())
        self.assertTrue((self.saved / "observations-ready.json").exists())
        pending.rename(ready)
        self.assertEqual(binding, self.verify())

    def test_declared_implementation_cannot_be_bound_to_another_build(self):
        self.receipt["observationImplementationSha256"] = "e" * 64
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, "same_reloaded_map"):
            self.bind()

    def test_same_sources_with_changed_global_pose_need_new_binding(self):
        self.bind()
        (self.saved / "private-observations.bin").write_bytes(archive_bytes(global_pose=pose(10)))
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_receipt_map_inventory_sensor_or_build_is_rejected(self):
        self.bind()
        for field, value in (("navigationReady", True), ("schemaVersion", 2), ("counts", {"submaps": 1, "savedFrames": 2})):
            original = self.receipt[field]
            self.receipt[field] = value
            self.write_receipt()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify()
            self.receipt[field] = original
            self.write_receipt()
        for inventory, provenance, digest in (({**self.inventory, "artifactInventorySha256": "e" * 64}, self.provenance, "d" * 64),
                (self.inventory, {**self.provenance, "profileSha256": "f" * 64}, "d" * 64),
                (self.inventory, self.provenance, "0" * 64)):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                verify_observations(self.saved, inventory, provenance, digest)

    def test_missing_archive_cannot_retrofit_old_map_and_binding_cannot_overwrite(self):
        path = self.saved / "private-observations.bin"
        data = path.read_bytes()
        path.unlink()
        with self.assertRaises((OSError, ValueError)):
            self.bind()
        self.assertFalse((self.saved / "observations-ready.json").exists())
        path.write_bytes(data)
        self.bind()
        with self.assertRaisesRegex(ValueError, "new_observation_binding"):
            self.bind()


if __name__ == "__main__":
    unittest.main()
