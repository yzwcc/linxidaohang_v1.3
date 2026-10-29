"""Clearly synthetic accounting and bounded-storage checks, no native load."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import run_glim_point_output_trial as trial

try:
    import numpy as np
    from lingxi_slam.point_output import PointPacket
except ImportError:
    np = None


def summary_fixture():
    return {
        "packets": 2, "copiedPoints": 4, "copiedPointsPerPacketMinMax": [2, 2],
        "firstNativeFrameId": 1, "lastNativeFrameId": 2,
        "nativeCounts": {"callbacks": 3, "initializationSkips": 1, "copies": 2,
                         "takes": 2, "discardedOnClose": 0},
        "allPacketBytesSha256": "0" * 64, "maximumRetainedPackets": 2,
        "coordinateFrame": "lingxi_glim_odom", "poseFrame": "lidar_not_robot_base",
        "sourceFrame": "IMU", "kind": "native_deskewed_corrected_odometry",
        "initializationFrameExcluded": True, "globalMapOrLoopClosure": False,
    }


class AccountingTests(unittest.TestCase):
    def test_complete_summary_is_accepted(self):
        self.assertEqual(summary_fixture(), trial.verified_points(summary_fixture(), 3))

    def test_loss_overflow_wrong_frame_and_false_deskew_claims_are_rejected(self):
        changes = (
            ("packets", 1), ("packets", True), ("copiedPoints", 5),
            ("copiedPointsPerPacketMinMax", [0, 2]), ("copiedPointsPerPacketMinMax", [2, 8001]),
            ("firstNativeFrameId", 0), ("lastNativeFrameId", 3), ("maximumRetainedPackets", 3),
            ("poseFrame", "base"), ("coordinateFrame", "map"), ("sourceFrame", "LIDAR"),
            ("initializationFrameExcluded", False), ("globalMapOrLoopClosure", True),
        )
        for key, value in changes:
            points = summary_fixture()
            points[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                trial.verified_points(points, 3)

    def test_native_counts_are_exact_integers_and_cannot_hide_discards(self):
        for key in summary_fixture()["nativeCounts"]:
            points = summary_fixture()
            points["nativeCounts"][key] += 1
            with self.subTest(key=key), self.assertRaises(ValueError):
                trial.verified_points(points, 3)
        points = summary_fixture()
        points["nativeCounts"]["initializationSkips"] = True
        with self.assertRaises(ValueError):
            trial.verified_points(points, 3)

    def test_complete_byte_digest_required(self):
        for value in ("", "0" * 63, "g" * 64, 123):
            points = summary_fixture()
            points["allPacketBytesSha256"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                trial.verified_points(points, 3)

    def test_existing_output_is_never_reused(self):
        with self.assertRaisesRegex(ValueError, "new_ignored"):
            trial.private_output(ROOT)


@unittest.skipIf(np is None, "Numerical storage contracts run in isolated Linux")
class SummaryTests(unittest.TestCase):
    def packet(self, frame_id):
        return PointPacket(frame_id, 1000. + frame_id * .1, 3,
                           np.ones((2, 3)) * frame_id, np.eye(4))

    def closed_summary(self, count):
        summary = trial.PacketSummary()
        summary.begin()
        for frame_id in range(1, count + 1):
            summary.accept(self.packet(frame_id))
        output = Mock(closed=True)
        output.counts.return_value = {
            "callbacks": count + 1, "initializationSkips": 1,
            "copies": count, "takes": count, "discardedOnClose": 0,
        }
        summary.finish(output)
        return summary

    def test_only_first_and_latest_are_retained_and_report_has_no_xyz(self):
        summary = self.closed_summary(100)
        self.assertEqual(1, summary.first.native_frame_id)
        self.assertEqual(100, summary.latest.native_frame_id)
        points = summary.report(101)
        self.assertEqual(200, points["copiedPoints"])
        trial.verified_points(points, 101)
        self.assertFalse(any(isinstance(value, np.ndarray) for value in points.values()))

    def test_same_packets_produce_same_stream_digest(self):
        self.assertEqual(self.closed_summary(2).report(3)["allPacketBytesSha256"],
                         self.closed_summary(2).report(3)["allPacketBytesSha256"])
        self.assertNotEqual(self.closed_summary(2).report(3)["allPacketBytesSha256"],
                            self.closed_summary(3).report(4)["allPacketBytesSha256"])

    def test_sequence_gap_cannot_be_silent(self):
        summary = trial.PacketSummary()
        summary.begin()
        with self.assertRaisesRegex(ValueError, "contiguous"):
            summary.accept(self.packet(2))

    def test_closed_summary_cannot_accept_new_packet(self):
        summary = self.closed_summary(2)
        with self.assertRaisesRegex(RuntimeError, "active_same_thread"):
            summary.accept(self.packet(3))

    def test_expected_pose_count_must_include_excluded_initialization(self):
        summary = self.closed_summary(2)
        with self.assertRaisesRegex(ValueError, "all_post_initialization"):
            summary.report(2)

    def test_finish_requires_native_close(self):
        summary = trial.PacketSummary()
        summary.begin()
        with self.assertRaisesRegex(ValueError, "closed_before"):
            summary.finish(Mock(closed=False))


if __name__ == "__main__":
    unittest.main()
