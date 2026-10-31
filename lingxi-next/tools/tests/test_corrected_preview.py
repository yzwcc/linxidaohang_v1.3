"""Display-only revisions using clearly synthetic geometry."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-slam"))
from lingxi_slam.async_mapping import CorrectedMapSnapshot
from lingxi_slam.corrected_preview import CorrectedPreviewWriter


class CorrectedPreviewTests(unittest.TestCase):
    def test_coalesces_revisions_and_samples_without_changing_original_points(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "corrected-preview.json"
            writer = CorrectedPreviewWriter(file, "a" * 32, "synthetic")
            points = np.arange(60_000, dtype=np.float64).reshape(-1, 3)
            original = points.copy()
            points.flags.writeable = False
            for revision in (1, 3, 2):
                writer.offer(CorrectedMapSnapshot(revision, 2, 1, 0, 1, points))
            writer.close()
            value = json.loads(file.read_text())
            self.assertEqual(3, value["revision"])
            self.assertEqual("synthetic", value["inputMode"])
            self.assertEqual("lingxi_glim_map", value["coordinateFrame"])
            self.assertEqual(20_000, value["totalPoints"])
            self.assertEqual(24_000, len(value["points"]))
            self.assertEqual(points[0].tolist(), value["points"][:3])
            self.assertEqual(points[-1].tolist(), value["points"][-3:])
            np.testing.assert_array_equal(original, points)
            self.assertFalse(writer.thread.is_alive())
            self.assertFalse(file.with_suffix(".pending").exists())

    def test_writer_failure_is_observable_and_shutdown_does_not_hang(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = CorrectedPreviewWriter(Path(directory) / "absent" / "preview.json", "b" * 32, "synthetic")
            writer.offer(CorrectedMapSnapshot(1, 1, 0, 0, 0, np.ones((2, 3))))
            with self.assertRaisesRegex(RuntimeError, "write_failed"):
                writer.close()
            self.assertFalse(writer.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
