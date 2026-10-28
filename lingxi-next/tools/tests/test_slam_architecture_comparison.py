"""Synthetic exact-frame comparisons; no real recordings or map coordinates."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ModuleNotFoundError:
    np = None


ROOT = Path(__file__).parents[2]
if np is not None and sys.platform == "linux":
    spec = importlib.util.spec_from_file_location(
        "architecture_comparison", ROOT / "tools/compare_glim_architectures.py")
    compare = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(compare)


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy environment")
class CommonFrameComparison(unittest.TestCase):
    def stamps(self, count=4):
        return np.arange(count, dtype=np.int64) + 1_790_000_000_000_000_000

    def poses(self, count=4):
        result = np.repeat(np.eye(4)[None, :, :], count, axis=0)
        result[:, 0, 3] = np.arange(count)
        return result

    def test_nanoseconds_above_double_precision_are_not_rounded(self):
        stamps = self.stamps()
        common, left, right = compare.common_frames(stamps[:3], stamps[1:])
        np.testing.assert_array_equal(common, stamps[1:3])
        np.testing.assert_array_equal(left, [1, 2])
        np.testing.assert_array_equal(right, [0, 1])

    def test_tail_exclusions_and_internal_gaps_are_reported(self):
        first = self.stamps(6)
        second = first[[1, 3, 4]]
        result = compare.compare_poses(first, self.poses(6), second, self.poses(6)[[1, 3, 4]])
        self.assertEqual(3, result["matching"]["commonFrames"])
        self.assertEqual(3, result["matching"]["excludedReferenceFrames"])
        self.assertEqual(1, result["matching"]["referenceInternalGaps"])
        self.assertEqual(0, result["matching"]["candidateInternalGaps"])
        self.assertEqual(0, result["unadjusted"]["positionDifferenceRawUnits"]["maximum"])

    def test_bad_stamps_and_no_overlap_are_rejected(self):
        for stamps in (self.stamps().astype(float), self.stamps()[::-1],
                       np.array([1, 1], dtype=np.int64), np.array([-1, 0], dtype=np.int64)):
            with self.assertRaisesRegex(ValueError, "integer_header"):
                compare.common_frames(stamps, self.stamps())
        with self.assertRaisesRegex(ValueError, "two_exact_common"):
            compare.common_frames(self.stamps(), self.stamps() + 10)

    def test_only_initial_rigid_offset_is_removed_not_later_drift_or_scale(self):
        poses = self.poses()
        shifted = poses.copy()
        shifted[:, 0, 3] = 10 + np.arange(4) * 2
        result = compare.compare_poses(self.stamps(), poses, self.stamps(), shifted)
        self.assertEqual(13, result["unadjusted"]["positionDifferenceRawUnits"]["maximum"])
        self.assertEqual(3, result["firstCommonPoseRigidAlignmentOnly"]["positionDifferenceRawUnits"]["maximum"])
        self.assertEqual(1, result["relativeBetweenConsecutiveCommonFrames"]["positionDifferenceRawUnits"]["maximum"])
        self.assertFalse(result["scaleFitted"])
        self.assertFalse(result["bestFitTrajectoryAlignmentUsed"])

    def test_initial_rotation_is_removed_with_one_rigid_transform(self):
        poses = self.poses()
        transform = np.eye(4)
        transform[:2, :2] = [[0, -1], [1, 0]]
        transform[:3, 3] = [4, 5, 6]
        result = compare.compare_poses(self.stamps(), poses, self.stamps(), transform @ poses)
        self.assertAlmostEqual(np.pi / 2, result["unadjusted"]["orientationDifferenceRadians"]["maximum"])
        self.assertAlmostEqual(0, result["firstCommonPoseRigidAlignmentOnly"]["positionDifferenceRawUnits"]["maximum"])
        self.assertAlmostEqual(0, result["relativeBetweenConsecutiveCommonFrames"]["orientationDifferenceRadians"]["maximum"])

    def test_nonfinite_and_invalid_pose_matrices_are_rejected(self):
        for mode in ("nan", "rotation", "shape"):
            poses = self.poses()
            if mode == "nan":
                poses[1, 0, 3] = np.nan
            elif mode == "rotation":
                poses[1, 0, 0] = 2
            else:
                poses = poses[:, :3, :]
            with self.assertRaises(ValueError):
                compare.compare_poses(self.stamps(), poses, self.stamps(), poses)

    def metadata(self, ids=(0, 1), times=(1000, 1001), submap=0):
        matrix = "1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1\n"
        text = f"id: {submap}\n"
        for name in ("T_world_origin:", "T_origin_endpoint_L:", "T_origin_endpoint_R:", "T_lidar_imu:"):
            text += name + "\n" + matrix
        text += f"imu_bias: 0 0 0 0 0 0\nframe_id: 2\nnum_frames: {len(ids)}\n"
        for index, (identifier, stamp) in enumerate(zip(ids, times)):
            text += f"frame_{index}\nid: {identifier}\nstamp: {stamp:.9f}\n"
            text += "T_odom_lidar:\n" + matrix + "T_world_lidar:\n" + matrix
            text += "v_world_imu: 0 0 0\n"
        return text

    def test_complete_submap_membership_is_read_without_coordinates_in_output(self):
        members, times = compare.submap_members(self.metadata(), 0)
        self.assertEqual([0, 1], members)
        self.assertEqual([1000, 1001], times)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "000000").mkdir()
            (root / "000000/data.txt").write_text(self.metadata())
            self.assertEqual([2], compare.read_membership(root, 1, np.array([1000, 1001])))

    def test_truncated_extra_wrong_submap_and_nonfinite_metadata_are_rejected(self):
        for text in (self.metadata()[:-10], self.metadata() + "extra",
                     self.metadata(submap=1), self.metadata().replace("v_world_imu: 0", "v_world_imu: nan", 1),
                     self.metadata().replace("frame_1", "frame_3", 1)):
            with self.assertRaises(ValueError):
                compare.submap_members(text, 0)

    def test_saved_frames_cannot_be_duplicated_omitted_or_time_shifted(self):
        for ids, times in (((0, 0), (1000, 1001)), ((0, 2), (1000, 1001)), ((0, 1), (1000, 1002))):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "000000").mkdir()
                (root / "000000/data.txt").write_text(self.metadata(ids=ids, times=times))
                with self.assertRaisesRegex(ValueError, "every_saved_frame"):
                    compare.read_membership(root, 1, np.array([1000, 1001]))

    def test_saved_trajectory_quaternion_is_normalized_only_for_text_rounding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traj.txt"
            path.write_text("1000 0 0 0 0 0 0.707107 0.707107\n1001 1 0 0 0 0 0 1\n")
            poses = compare.saved_lidar_poses(path, np.array([1000, 1001]))
            np.testing.assert_allclose(poses[0, :2, :2], [[0, -1], [1, 0]], atol=1e-10)
            path.write_text("1000 0 0 0 0 0 0 2\n1001 1 0 0 0 0 0 1\n")
            with self.assertRaisesRegex(ValueError, "unit_quaternion"):
                compare.saved_lidar_poses(path, np.array([1000, 1001]))

    def test_saved_nine_decimal_timestamp_tolerance_does_not_reassociate_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traj.txt"
            path.write_text("1000 0 0 0 0 0 0 1\n1001 1 0 0 0 0 0 1\n")
            compare.saved_lidar_poses(path, np.array([1000 + 0.49e-9, 1001]))
            with self.assertRaisesRegex(ValueError, "time_order"):
                compare.saved_lidar_poses(path, np.array([1000 + 2e-9, 1001]))

    def test_partition_boundary_differences_do_not_invent_a_cause(self):
        result = compare.partition_comparison([2, 3, 4], [2, 2, 3, 2])
        self.assertEqual(1, result["identicalInternalBoundaryPrefixCount"])
        self.assertEqual(1, result["sharedInternalBoundaries"])
        self.assertFalse(result["runtimeSplitTriggerDirectlyObserved"])
        self.assertFalse(result["splitTriggerTraceAvailable"])
        self.assertFalse(result["identicalPartition"])
        self.assertTrue(result["firstDifferentInternalSubmapsBothHaveFewerThan50Frames"])
        self.assertEqual("adaptive_voxel_criterion_required_for_both_short_internal_submaps",
                         result["sourceRuleInferenceNotRuntimeTrace"])
        for first, second in (([2, 3], [2, 4]), ([0, 2], [2]), ([], [])):
            with self.assertRaises(ValueError):
                compare.partition_comparison(first, second)

    def test_final_remainder_or_large_submap_does_not_prove_adaptive_trigger(self):
        for first, second in (([100], [50, 50]), ([50, 50], [51, 49]), ([50, 50], [50, 50])):
            result = compare.partition_comparison(first, second)
            self.assertFalse(result["firstDifferentInternalSubmapsBothHaveFewerThan50Frames"])
            self.assertEqual("not_determined_from_frame_counts", result["sourceRuleInferenceNotRuntimeTrace"])

    def test_same_hypothesis_requires_identical_full_frames_and_algorithm_times(self):
        first = {"stamps": self.stamps(), "algorithm": np.arange(4, dtype=float),
                 "online": self.poses(), "saved": self.poses(), "members": [4],
                 "scanCounters": np.ones((4, 2))}
        second = {**first, "algorithm": first["algorithm"] + 0.001}
        with self.assertRaisesRegex(ValueError, "domains_and_times"):
            compare.compare_runs(first, second, True)
        result = compare.compare_runs(first, second, False)
        self.assertTrue(result["differentHypothesisPosesReferToDifferentScanInstants"])
        self.assertNotIn("savedSubmapPartition", result)

    def test_metrics_do_not_export_added_private_fields_or_source_poses(self):
        first = {"stamps": self.stamps(), "algorithm": np.arange(4, dtype=float),
                 "online": self.poses(), "saved": self.poses(), "members": [4],
                 "scanCounters": np.ones((4, 2)),
                 "privateProfile": "synthetic_private_marker", "credential": "synthetic_not_a_secret"}
        text = json.dumps(compare.compare_runs(first, first, True))
        for token in ("synthetic_private_marker", "synthetic_not_a_secret", "privateProfile",
                      "header_stamps_ns", '"poses"', '"online"', '"saved"', "credential"):
            self.assertNotIn(token, text)

    def test_point_count_difference_is_separate_from_point_identity(self):
        first = np.array([[100, 0], [101, 1], [102, 1]], dtype=float)
        second = first.copy()
        second[1, 0] += 3
        result = compare.compare_scan_counters(first, second)
        self.assertEqual(1, result["preparedPointCountDifferentScans"])
        self.assertEqual(3, result["maximumAbsolutePreparedPointCountDifference"])
        self.assertEqual(2, result["firstPreparedCountDifferenceProcessedOrdinalOneBased"])
        self.assertEqual(0, result["marginalizedFrameCountDifferentScans"])
        self.assertTrue(result["equalCountsAreNotProofOfIdenticalPreparedPoints"])
        self.assertIsNone(compare.compare_scan_counters(first, first)["firstPreparedCountDifferenceProcessedOrdinalOneBased"])
        for value in (second[:2], second * 0.5, np.full((3, 2), np.nan)):
            with self.assertRaisesRegex(ValueError, "scan_counter_domain"):
                compare.compare_scan_counters(first, value)

    def test_changed_historical_artifact_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.txt"
            path.write_text("first")
            digest = compare.sha256(path)
            path.write_text("second")
            with self.assertRaisesRegex(ValueError, "digest_mismatch"):
                compare.require_hash(path, digest)

    def test_existing_evidence_is_preserved_before_any_computation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(compare, "export") as exporter:
            output = Path(directory) / "report.json"
            output.write_text("existing")
            arguments = ["compare", "--host-cache", directory, "--target-results", directory,
                         "--target-bundle", directory, "--upstream-root", directory, "--output", str(output)]
            with patch.object(sys, "argv", arguments):
                with self.assertRaisesRegex(ValueError, "existing_comparison"):
                    compare.main()
            self.assertEqual("existing", output.read_text())
            exporter.assert_not_called()


class ComparisonBoundaries(unittest.TestCase):
    def test_no_robot_process_network_ros_or_native_execution_interfaces(self):
        tree = ast.parse((ROOT / "tools/compare_glim_architectures.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module]
                for name in names:
                    self.assertNotIn(name.split(".")[0], {"subprocess", "ctypes", "rclpy", "socket", "paramiko", "requests"})
        text = (ROOT / "tools/compare_glim_architectures.py").read_text()
        for token in ("cmd_vel", "McLocomotion", "create_publisher", "np.save", "tofile("):
            self.assertNotIn(token, text)

    def test_public_comparison_keeps_all_runs_and_acceptance_false(self):
        evidence = json.loads((ROOT / "docs/slam-architecture-comparison-evidence.json").read_text())
        self.assertEqual(hashlib.sha256((ROOT / "tools/compare_glim_architectures.py").read_bytes()).hexdigest(),
                         evidence["comparatorSourceSha256"])
        self.assertTrue(evidence["allHistoricalMapAndTrajectoryDigestsUnchanged"])
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        old = json.loads((ROOT / "docs/slam-arm64-mapping-evidence.json").read_text())
        self.assertEqual(set(old["runs"]), set(evidence["crossArchitecture"]))
        for label, run in evidence["crossArchitecture"].items():
            matching = run["onlineLidarPosesNotFinalSmoothed"]["matching"]
            self.assertEqual(old["runs"][label]["mapping"]["savedFrames"], matching["commonFrames"])
            self.assertEqual(0, matching["excludedReferenceFrames"])
            self.assertEqual(0, matching["excludedCandidateFrames"])
            self.assertEqual(0, run["algorithmTimeDifferenceSeconds"]["maximum"])
            self.assertFalse(run["savedSubmapPartition"]["runtimeSplitTriggerDirectlyObserved"])
        self.assertFalse(evidence["crossArchitecture"]["dynamicEnd"]["savedSubmapPartition"]["identicalPartition"])
        for token in ('"header_stamps_ns"', '"T_lidar_imu"', '"poses"', '"password"', '"hostname"'):
            self.assertNotIn(token, json.dumps(evidence))


if __name__ == "__main__":
    unittest.main()
