"""Synthetic timing, bounded kinematic coverage and passive ROS-owner wiring."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "robot-runtime/python")]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if AVAILABLE:
    import numpy as np
    from lingxi_slam.base_pose import BasePoseUnavailable
    from lingxi_slam.base_pose_input import CHAIN, DYNAMIC_CHILDREN, ScanTimeIndex, TfCoverage
    from lingxi_slam.messages import SensorProfile
    from live_base_pose import LiveBasePose
    from test_slam_worker import cloud, imu


@unittest.skipUnless(AVAILABLE, "requires NumPy and SciPy")
class LiveBaseInput(unittest.TestCase):
    EPOCH = 1_700_000_000_000_000_000

    def profile(self):
        return SensorProfile("synthetic_cloud", "synthetic_imu", "frame_relative_nanoseconds_hypothesis")

    def transform(self, child, stamp=None):
        stamp = self.EPOCH if stamp is None else stamp
        return NS(header=NS(frame_id=CHAIN[CHAIN.index(child) - 1],
                            stamp=NS(sec=stamp // 10**9, nanosec=stamp % 10**9)), child_frame_id=child,
                  transform=NS(translation=NS(x=.1, y=0., z=0.), rotation=NS(x=0., y=0., z=0., w=1.)))

    def coverage(self, *, maximum_samples=256):
        result = TfCoverage(maximum_samples=maximum_samples)
        for child in CHAIN[1:]:
            if child in DYNAMIC_CHILDREN:
                for stamp in (self.EPOCH, self.EPOCH + 10_000_000):
                    result.observe(self.transform(child, stamp), static=False)
            else:
                result.observe(self.transform(child, 0), static=True)
        return result

    def test_original_metadata_matches_native_start_and_end_formulas_for_padded_endian_clouds(self):
        for hypothesis in ("header_is_scan_start", "header_is_scan_end"):
            for big in (False, True):
                index = ScanTimeIndex(self.profile(), hypothesis)
                index.observe("cloud", cloud(40, big=big, organized=True))
                index.observe("imu", imu())
                algorithm = 1000.04 - (.018 if hypothesis.endswith("end") else 0.)
                value = index.consume(self.EPOCH + 40_000_000, algorithm)
                self.assertEqual(18_000_000, value.maximum_offset_ns)
                self.assertEqual(self.EPOCH + (22_000_000 if hypothesis.endswith("end") else 40_000_000),
                                 value.effective_ns)
                self.assertEqual(0, len(index.frames))

    def test_scan_index_capacity_missing_origin_mismatch_and_shutdown_do_not_fabricate_time(self):
        index = ScanTimeIndex(self.profile(), "header_is_scan_start", maximum_frames=2)
        for stamp in (10, 20, 30):
            index.observe("cloud", cloud(stamp))
        self.assertEqual(2, len(index.frames))
        with self.assertRaises(BasePoseUnavailable):
            index.consume(self.EPOCH + 10_000_000, 1000.01)
        index.observe("imu", imu())
        with self.assertRaises(BasePoseUnavailable):
            index.consume(self.EPOCH + 20_000_000, 1000.03)
        index.close()
        self.assertEqual(0, len(index.frames))
        index.observe("cloud", cloud(50))
        self.assertEqual(0, len(index.frames))

    def test_invalidation_retires_all_pending_metadata(self):
        index = ScanTimeIndex(self.profile(), "header_is_scan_start")
        index.observe("imu", imu())
        index.observe("cloud", cloud())
        index.invalidate()
        with self.assertRaises(BasePoseUnavailable):
            index.consume(self.EPOCH + 40_000_000, 1000.04)
        self.assertEqual(0, len(index.frames))

    def test_coverage_requires_all_fixed_edges_and_integer_bracketing_without_extrapolation(self):
        value = self.coverage()
        for stamp in (self.EPOCH, self.EPOCH + 5_000_000, self.EPOCH + 10_000_000):
            self.assertTrue(value.covers(stamp))
        for stamp in (self.EPOCH - 1, self.EPOCH + 10_000_001):
            self.assertFalse(value.covers(stamp))
        value.static.pop("pelvis")
        self.assertFalse(value.covers(self.EPOCH))

    def test_large_tf_gaps_and_evicted_history_remain_unavailable(self):
        value = self.coverage(maximum_samples=2)
        for child in DYNAMIC_CHILDREN:
            value.observe(self.transform(child, self.EPOCH + 100_000_000), static=False)
        self.assertFalse(value.covers(self.EPOCH))
        self.assertFalse(value.covers(self.EPOCH + 50_000_000))
        self.assertTrue(all(len(rows) <= 2 for rows in value.dynamic.values()))
        value.close()
        self.assertFalse(value.covers(self.EPOCH + 100_000_000))
        self.assertEqual({}, value.dynamic)

    def test_wrong_parent_type_quaternion_static_change_or_duplicate_conflict_is_rejected(self):
        for kind in ("parent", "type", "quaternion", "static_change", "duplicate"):
            value = self.coverage()
            child = "pelvis" if kind == "static_change" else "waist_yaw_link"
            message, static = self.transform(child), child not in DYNAMIC_CHILDREN
            if kind == "parent":
                message.header.frame_id = "other"
            elif kind == "type":
                static = not static
            elif kind == "quaternion":
                message.transform.rotation.w = 2.
            else:
                message.transform.translation.x = 3.
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                value.observe(message, static=static)

    def test_buffer_failure_does_not_commit_coverage_and_unrelated_frames_are_ignored(self):
        value, calls = TfCoverage(), []
        message = self.transform("pelvis")
        def failed():
            raise RuntimeError("synthetic tf buffer failure")
        with self.assertRaises(RuntimeError):
            value.observe(message, static=True, apply=failed)
        self.assertEqual({}, value.static)
        message.child_frame_id = "unrelated_camera"
        self.assertFalse(value.observe(message, static=True, apply=lambda: calls.append(True)))
        self.assertEqual([], calls)

    def owner(self):
        session = NS(base_scale=1., binding=NS(input_mode="live"), revoked=[], projected=[])
        session.revoke_base = lambda reason: session.revoked.append(reason)
        session.project_base = lambda *args, **kwargs: session.projected.append((args, kwargs))
        return session, LiveBasePose(session, self.profile(), "header_is_scan_start")

    def test_owner_carries_original_time_to_frame_projection_and_closes_all_caches(self):
        session, owner = self.owner()
        owner.projector = NS(lookup=lambda stamp: None)
        owner.sensor("imu", imu())
        owner.sensor("cloud", cloud())
        owner.frame(self.EPOCH + 40_000_000, 1000.04, np.eye(4), None)
        self.assertEqual(self.EPOCH + 40_000_000, session.projected[0][0][0].effective_ns)
        owner.close()
        self.assertEqual("input_closed", session.revoked[-1])
        self.assertEqual(0, owner.summary()["pendingScanTimes"])
        self.assertEqual(0, owner.summary()["retainedDynamicTransforms"])
        owner.sensor("cloud", cloud(100))
        self.assertEqual(0, len(owner.times.frames))

    def test_unseeded_lookup_observations_survive_close_without_creating_pose(self):
        session, owner = self.owner()
        calls = []
        owner.projector = NS(lookup=lambda stamp: calls.append(stamp) or object())
        owner.sensor("imu", imu())
        owner.sensor("cloud", cloud())
        owner.frame(self.EPOCH + 40_000_000, 1000.04, None, None)
        owner.close()
        self.assertEqual([], session.projected)
        self.assertEqual([self.EPOCH + 40_000_000], calls)
        observed = owner.summary()["observations"]
        self.assertEqual(1, observed["timedFrames"])
        self.assertEqual(1, observed["tfLookupsAvailable"])
        self.assertEqual(1, observed["imuMessages"])
        self.assertEqual(1, observed["cloudMessages"])
        owner.sensor("cloud", cloud(100))
        self.assertEqual(observed, owner.summary()["observations"])
        observed["cloudMessages"] = 99
        self.assertEqual(1, owner.summary()["observations"]["cloudMessages"])

    def test_missing_or_invalid_lookup_never_fabricates_unseeded_pose(self):
        for failure in (False, True):
            session, owner = self.owner()
            def lookup(stamp):
                if failure:
                    raise ValueError("synthetic invalid lookup")
                return None
            owner.projector = NS(lookup=lookup)
            owner.sensor("imu", imu())
            owner.sensor("cloud", cloud())
            owner.frame(self.EPOCH + 40_000_000, 1000.04, None, None)
            self.assertEqual([], session.projected)
            observations = owner.summary()["observations"]
            self.assertEqual(1, observations["lookupErrors" if failure else "tfLookupsUnavailable"])

    def test_owner_tf_input_failure_revokes_without_sending_controls(self):
        session, owner = self.owner()
        owner.buffer = NS(set_transform=lambda *args: None, set_transform_static=lambda *args: None)
        invalid = self.transform("pelvis")
        invalid.transform.rotation.w = 0.
        owner.transforms(NS(transforms=[invalid]), True)
        self.assertTrue(owner.coverage.failed)
        self.assertEqual("invalid_kinematic_tf", session.revoked[-1])
        owner.sensor("cloud", NS())
        self.assertTrue(owner.times.failed)
        self.assertEqual("invalid_scan_time", session.revoked[-1])

    def test_setup_reuses_node_only_two_tf_subscriptions_and_five_second_buffer(self):
        session, owner = self.owner()
        calls, options = [], []
        class Buffer:
            def __init__(self, **kwargs):
                options.append(kwargs)
            def lookup_transform(self, *args, **kwargs):
                raise AssertionError("no lookup during setup")
        modules = {"rclpy.duration": NS(Duration=lambda **kwargs: kwargs),
                   "rclpy.qos": NS(QoSProfile=lambda **kwargs: kwargs, HistoryPolicy=NS(KEEP_LAST="keep"),
                       ReliabilityPolicy=NS(RELIABLE="reliable", BEST_EFFORT="best"),
                       DurabilityPolicy=NS(TRANSIENT_LOCAL="transient", VOLATILE="volatile")),
                   "tf2_msgs.msg": NS(TFMessage=object), "tf2_ros": NS(Buffer=Buffer)}
        with patch.dict(sys.modules, modules):
            cleanup = owner.setup(NS(create_subscription=lambda *args: calls.append(args)))
        self.assertEqual(["/tf", "/tf_static"], [row[1] for row in calls])
        self.assertEqual({"cache_time": {"seconds": 5.}}, options[0])
        self.assertEqual("transient", calls[1][3]["durability"])
        cleanup()
        self.assertTrue(owner.summary()["closed"])


if __name__ == "__main__":
    unittest.main()
