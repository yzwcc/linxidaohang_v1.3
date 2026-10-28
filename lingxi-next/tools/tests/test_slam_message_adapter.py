"""Synthetic comparison checks and current-source public evidence integrity."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
AVAILABLE = importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        import check_slam_message_adapter as checker
        from test_slam_worker import cloud, imu
        from lingxi_slam.messages import SensorProfile


@unittest.skipUnless(AVAILABLE, "requires isolated NumPy")
class MessageComparison(unittest.TestCase):
    def setUp(self):
        self.profile = SensorProfile(
            "synthetic_cloud", "synthetic_imu", "frame_relative_nanoseconds_hypothesis")

    def test_complete_cloud_and_imu_match_reference_decoder(self):
        self.assertEqual((10, 320), checker.compare_packet(cloud(), "cloud", self.profile))
        self.assertEqual((0, 56), checker.compare_packet(imu(), "imu", self.profile))

    def test_different_coordinates_or_time_pairing_are_not_accepted(self):
        original = checker.cloud_arrays
        for index in (0, 1):
            def mismatch(message):
                points, times, dropped = original(message)
                (points if index == 0 else times)[0] += 1
                return points, times, dropped
            with patch.object(checker, "cloud_arrays", side_effect=mismatch):
                with self.assertRaisesRegex(ValueError, "equivalence"):
                    checker.compare_packet(cloud(), "cloud", self.profile)

    def test_missing_points_and_unknown_message_kind_are_not_accepted(self):
        points, times, _ = checker.cloud_arrays(cloud())
        with patch.object(checker, "cloud_arrays", return_value=(points, times, 1)):
            with self.assertRaises(ValueError):
                checker.compare_packet(cloud(), "cloud", self.profile)
        with self.assertRaisesRegex(ValueError, "explicit_cloud_or_imu"):
            checker.compare_packet(imu(), "odometry", self.profile)

    def test_existing_or_outside_private_output_is_rejected_before_recording_access(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(checker, "verify") as verify:
            for output in (Path(directory), Path(directory) / "nonexistent"):
                with self.assertRaisesRegex(ValueError, "new_private"):
                    checker.run(Path(directory), output)
            verify.assert_not_called()


class MessageEvidence(unittest.TestCase):
    def test_public_aggregates_match_complete_capture_counts_and_current_sources(self):
        evidence = json.loads((ROOT / "docs/slam-message-adapter-evidence.json").read_text())
        capture_path = ROOT / "docs/slam-capture-evidence.json"
        capture = json.loads(capture_path.read_text())
        self.assertEqual("all_recorded_sensor_messages_match_frozen_decoder_not_live_acceptance",
                         evidence["result"])
        self.assertTrue(evidence["originalCapturesAndSourcesUnchanged"])
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        self.assertEqual(hashlib.sha256(capture_path.read_bytes()).hexdigest(),
                         evidence["captureManifestSha256"])
        for name, expected in capture["datasets"].items():
            run = evidence["datasets"][name]
            self.assertEqual({key: expected["topicCounts"][key] for key in ("cloud", "imu")},
                             run["comparedMessages"])
            self.assertTrue(run["allCoordinatesOffsetsAndImuValuesExactlyEqualToFrozenDecoder"])
            self.assertGreater(run["completePointsCompared"], run["comparedMessages"]["cloud"] * 10)
            self.assertEqual({
                "comparedMessages", "completePointsCompared", "maximumSnapshotPayloadBytes",
                "allCoordinatesOffsetsAndImuValuesExactlyEqualToFrozenDecoder",
            }, set(run))
        for source, digest in evidence["sourceSha256"].items():
            self.assertTrue((ROOT / source).is_relative_to(ROOT))
            self.assertEqual(digest, hashlib.sha256((ROOT / source).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
