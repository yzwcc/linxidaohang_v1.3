from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.map_provenance import sensor_provenance


VALID = {
    "schemaVersion": 1,
    "profileSha256": "a" * 64,
    "timingHypothesis": "header_is_scan_start",
    "configurationSha256": {"lidar.json": "b" * 64},
}


class SensorProvenance(unittest.TestCase):
    def test_returns_an_owned_sorted_configuration_copy(self):
        value = {**VALID, "configurationSha256": {"z.json": "c" * 64, "a.json": "b" * 64}}
        result = sensor_provenance(value)
        self.assertEqual(["a.json", "z.json"], list(result["configurationSha256"]))
        self.assertIsNot(result, value)
        self.assertIsNot(result["configurationSha256"], value["configurationSha256"])
        value["configurationSha256"]["new.json"] = "d" * 64
        self.assertNotIn("new.json", result["configurationSha256"])

    def test_rejects_missing_unknown_and_unbounded_fields(self):
        for value in (
            {**VALID, "unexpected": True},
            {key: item for key, item in VALID.items() if key != "profileSha256"},
            {**VALID, "profileSha256": "A" * 64},
            {**VALID, "timingHypothesis": "guess"},
            {**VALID, "configurationSha256": {}},
            {**VALID, "configurationSha256": {"../secret": "b" * 64}},
            {**VALID, "configurationSha256": {"lidar.json": True}},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sensor_provenance(value)


if __name__ == "__main__":
    unittest.main()
