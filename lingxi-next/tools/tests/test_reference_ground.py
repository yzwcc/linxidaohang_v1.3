import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "robot-runtime" / "python"))
from reference_ground import SEEDS, consensus, read_saved_export_reference


def ready(*, degrees=0, height=.9, area=1., points=500):
    angle = math.radians(degrees)
    return {"state": "ready", "measured_ground_plane": [math.sin(angle), 0., math.cos(angle), 0.],
            "base_height_m": height, "evidence": {"session": "test"}, "transform": [[height]],
            "quality": {"occupied_area_m2": area, "support_points": points, "residual_p95_m": .01}}


def pending(reason, evidence):
    return {"state": "pending", "transform": None, "reason": reason, "evidence": evidence}


class ReferenceGroundTests(unittest.TestCase):
    def test_keeps_an_original_transform_from_the_agreeing_pair(self):
        outlier = ready(height=1.03, area=30.)
        first = ready(height=.9, area=4.)
        second = ready(height=.91, area=6.)
        estimate = Mock(side_effect=[outlier, first, second])
        seed = Mock()
        result = consensus(estimate, seed, pending, ("same inputs",), {"config": "unchanged"})
        self.assertEqual([call.args[0] for call in seed.call_args_list], list(SEEDS))
        self.assertEqual(estimate.call_count, 3)
        estimate.assert_called_with("same inputs", config="unchanged")
        self.assertEqual(result["transform"], second["transform"])
        self.assertEqual(result["quality"]["lingxi_ground_consensus"]["agreed"], 2)
        self.assertNotIn("lingxi_ground_consensus", second["quality"])

    def test_rejects_single_pass_incompatible_normals_and_ambiguous_chain(self):
        absent = pending("地面未确认", {})
        for trials in ([absent, ready(), absent], [ready(), ready(degrees=2), absent],
                       [ready(height=.9), ready(height=.92), ready(height=.94)]):
            with self.subTest(trials=trials):
                result = consensus(Mock(side_effect=trials), Mock(), pending, (), {})
                self.assertEqual(result["state"], "pending")
                self.assertIsNone(result["transform"])
        self.assertIs(consensus(Mock(return_value=absent), Mock(), pending, (), {}), absent)

    def test_worker_is_isolated_and_fails_closed(self):
        data = {"reference": ready(), "context": {}}
        with patch("reference_ground.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(data))) as run:
            self.assertEqual(read_saved_export_reference(Path("/pinned"), {"id": "test"}), data)
            self.assertEqual(run.call_args.kwargs["env"]["OMP_NUM_THREADS"], "1")
            self.assertEqual(run.call_args.kwargs["env"]["OPENBLAS_NUM_THREADS"], "1")
            self.assertTrue(run.call_args.kwargs["check"])
        for result in ("not json", '{"reference":{"state":"unknown"},"context":{}}'):
            with patch("reference_ground.subprocess.run", return_value=SimpleNamespace(stdout=result)):
                with self.assertRaisesRegex(RuntimeError, "reference_ground_worker_failed"):
                    read_saved_export_reference(Path("/pinned"), {})
        with patch("reference_ground.subprocess.run", side_effect=subprocess.TimeoutExpired("worker", 120)):
            with self.assertRaisesRegex(RuntimeError, "reference_ground_worker_failed"):
                read_saved_export_reference(Path("/pinned"), {})
