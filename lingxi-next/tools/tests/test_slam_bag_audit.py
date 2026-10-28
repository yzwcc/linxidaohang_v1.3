"""Explicit synthetic fixtures for the offline input auditor."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("slam_bag_audit", ROOT / "tools/slam_bag_audit.py")
bag = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
    spec.loader.exec_module(bag)


def odometry(second, position):
    return NS(
        header=NS(stamp=NS(sec=second, nanosec=0), frame_id="synthetic_odom"),
        child_frame_id="synthetic_body",
        twist=NS(twist=NS(linear=NS(x=0.0, y=0.0, z=0.0))),
        pose=NS(pose=NS(position=NS(x=position[0], y=position[1], z=position[2]))),
    )


class OfflineEvidence(unittest.TestCase):
    def test_empty_bag_does_not_claim_ready_or_complete_inputs(self):
        report = bag.RecordedInputs().report()
        self.assertEqual(5, len(report["bag"]["missingTopics"]))
        self.assertIsNone(report["bag"]["observedDurationSeconds"])
        for field in ("slamExecuted", "mappingValidated", "relocalizationValidated", "navigationValidated"):
            self.assertFalse(report["validation"][field])
        self.assertEqual(0, report["validation"]["motionCommandsSent"])

    def test_recorded_frequency_does_not_use_auditor_execution_speed(self):
        inputs = bag.RecordedInputs()
        inputs.observe(bag.ODOMETRY, odometry(1000, (0, 0, 0)), 1000100000000)
        inputs.observe(bag.ODOMETRY, odometry(1001, (1, 0, 0)), 1001100000000)
        report = inputs.report()
        stream = report["streams"]["odometry"]
        self.assertEqual(1, stream["recordedArrivalHz"])
        self.assertNotIn("arrivalHz", stream)
        self.assertAlmostEqual(0.1, stream["absoluteBagStampToHeaderSeconds"]["max"], places=6)
        self.assertNotIn("absoluteWallClockOffsetSeconds", stream)

    def test_pose_aggregate_is_not_ground_truth_or_a_raw_trajectory(self):
        inputs = bag.RecordedInputs()
        for index, position in enumerate(((0, 0, 0), (3, 0, 0), (0, 0, 0))):
            inputs.observe(bag.ODOMETRY, odometry(1000 + index, position), (1000 + index) * 10**9)
        report = inputs.report()
        motion = report["odometry"]["reportedPoseMotion"]
        self.assertEqual(6, motion["accumulatedPositionStepRawUnits"])
        self.assertEqual(0, motion["firstToLastPositionDistanceRawUnits"])
        self.assertFalse(motion["isGroundTruth"])
        self.assertFalse(motion["physicalUnitValidated"])
        self.assertFalse(report["bag"]["containsRawCoordinates"])
        self.assertNotIn("first_position", str(report))

    def test_invalid_pose_does_not_bridge_a_missing_segment(self):
        inputs = bag.RecordedInputs()
        for index, position in enumerate(((0, 0, 0), (float("nan"), 0, 0), (10, 0, 0))):
            inputs.observe(bag.ODOMETRY, odometry(1000 + index, position), (1000 + index) * 10**9)
        report = inputs.report()["odometry"]["reportedPoseMotion"]
        self.assertEqual(0, report["accumulatedPositionStepRawUnits"])
        self.assertEqual(1, report["invalidPoseSamples"])

    def test_invalid_recording_timestamp_is_rejected(self):
        for stamp in (True, 0, -1, 1.5):
            with self.assertRaisesRegex(ValueError, "invalid_bag_timestamp"):
                bag.RecordedInputs().observe(bag.ODOMETRY, odometry(1, (0, 0, 0)), stamp)

    def test_unrelated_topic_is_not_decoded_or_added(self):
        inputs = bag.RecordedInputs()
        inputs.observe("/synthetic_unrelated_audio", object(), 10**9)
        self.assertTrue(all(count == 0 for count in inputs.report()["bag"]["messageCounts"].values()))

    def test_missing_bag_is_rejected_before_importing_ros(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "existing_finalized_bag"):
                bag.run(directory)


if __name__ == "__main__":
    unittest.main()
