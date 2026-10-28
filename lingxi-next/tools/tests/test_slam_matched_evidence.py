"""Synthetic public aggregation checks; no original data or live target."""
import importlib.util
from pathlib import Path
import platform
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[2]
AVAILABLE = (platform.system() == "Linux" and importlib.util.find_spec("numpy") is not None
             and importlib.util.find_spec("scipy") is not None
             and importlib.util.find_spec("json5") is not None)
if AVAILABLE:
    sys.path.insert(0, str(ROOT / "tools"))
    import numpy as np
    import export_glim_matched_evidence as evidence
    from slam_callback_journal import Writer


@unittest.skipUnless(AVAILABLE, "requires isolated Linux scientific dependencies")
class MatchedEvidence(unittest.TestCase):
    def test_comparison_flags_require_actual_booleans_not_numeric_equivalents(self):
        expected = {"flag": True, "physicalAccuracyValidated": False, "number": 2., "values": [1., 2.]}
        self.assertTrue(evidence.matching_comparison(expected, expected.copy()))
        for change in ({"flag": 1}, {"physicalAccuracyValidated": 0}, {"flag": False},
                       {"extra": 1}, {"number": float("nan")}):
            self.assertFalse(evidence.matching_comparison({**expected, **change}, expected))

    def test_numeric_recomputation_tolerance_does_not_relax_structure(self):
        expected = {"flag": True, "values": [1., 2.]}
        self.assertTrue(evidence.matching_comparison({"flag": True, "values": [1. + 1e-12, 2.]}, expected))
        self.assertFalse(evidence.matching_comparison({"flag": True, "values": [1.]}, expected))
        self.assertFalse(evidence.matching_comparison({"values": [1., 2.]}, expected))

    def journal(self, directory):
        path = Path(directory) / "synthetic"
        writer = Writer(path)
        for index, (kind, header) in enumerate((("imu", 10), ("cloud", 20), ("imu", 30))):
            writer.append(kind, index, str(header).encode())
        writer.finish()
        return path

    def test_every_cdr_header_matches_the_actual_observed_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.journal(directory)
            arrays = {"cloud_headers_ns": np.array([20]), "imu_headers_ns": np.array([10, 30])}
            summary = evidence.audit_callbacks(path, arrays, decoder=lambda kind, payload: int(payload))
            self.assertEqual({"cloud": 1, "imu": 2}, summary["counts"])
            self.assertEqual(3, summary["records"])
            self.assertNotIn("headers_ns", str(summary))

    def test_missing_or_extra_callback_headers_cannot_pass_by_count_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.journal(directory)
            for headers in ([10], [10, 31], [10, 30, 40]):
                arrays = {"cloud_headers_ns": np.array([20]), "imu_headers_ns": np.array(headers)}
                with self.assertRaises(ValueError):
                    evidence.audit_callbacks(path, arrays, decoder=lambda kind, payload: int(payload))


if __name__ == "__main__":
    unittest.main()
