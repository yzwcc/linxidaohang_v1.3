"""Display cache uses deskewed returns without changing localization artifacts."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-slam"))
    from lingxi_slam.saved_scan_preview import build, reduce_points
    from lingxi_slam.observation_archive import sha256
    from test_observation_archive import archive_bytes, pose


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python runtime")
class SavedScanPreviewTests(unittest.TestCase):
    def test_returns_are_selected_not_averaged_and_default_height_retains_detail(self):
        points = np.array([[1.001, 0, .5], [1.002, 0, .5], [2, 0, .5]])
        np.testing.assert_array_equal(reduce_points(points), points[[0, 2]])
        inside = np.column_stack([np.arange(70000) * .1, np.zeros(70000), np.full(70000, .5)])
        outside = np.column_stack([np.arange(40000) * .1, np.zeros(40000), np.full(40000, 2.)])
        result = reduce_points(np.concatenate([outside, inside]))
        self.assertEqual((80000, 3), result.shape)
        self.assertEqual(60000, int(np.count_nonzero(result[:, 2] == .5)))
        self.assertEqual((0, 3), reduce_points(np.empty((0, 3))).shape)

    def test_cache_uses_final_registered_motion_compensated_geometry_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory)
            archive = saved / "private-observations.bin"
            archive.write_bytes(archive_bytes(global_pose=pose(10, 90), local_pose=pose(2)))
            receipt = {"inputMode": "synthetic", "counts": {"savedFrames": 1}, "mapInventory": {"test": 1}}
            ready = saved / "map-ready.json"
            ready.write_text(json.dumps(receipt))
            binding = {"mapReceiptSha256": sha256(ready), "inputMode": "synthetic", "mapInventory": {"test": 1},
                       "observations": {"archiveSha256": sha256(archive)}}
            (saved / "observations-ready.json").write_text(json.dumps(binding))
            sources = {p.name: p.read_bytes() for p in saved.iterdir()}
            self.assertEqual({"points": 3, "sourceReturns": 3, "defaultZPoints": 0}, build(saved))
            cloud = json.loads((saved / "scan-preview.json").read_text())
            expected = [[10, 3, 0], [10 - 2**-.5, 3 + 2**-.5, 0], [9, 4, 0]]
            np.testing.assert_allclose(np.array(cloud["points"]).reshape(-1, 3), expected, atol=1e-6)
            self.assertEqual(binding["mapReceiptSha256"], cloud["mapReceiptSha256"])
            self.assertTrue(all((saved / name).read_bytes() == data for name, data in sources.items()))
            archive.write_bytes(archive_bytes(global_pose=pose(20)))
            with self.assertRaises(ValueError):
                build(saved)
            self.assertEqual(cloud, json.loads((saved / "scan-preview.json").read_text()))


if __name__ == "__main__":
    unittest.main()
