"""Physical sensor boundary contracts. Real tf2 is tested separately in Humble."""
from dataclasses import asdict
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-navigation"))
from lingxi_navigation.physical_bridge import MapEvidence, Observation, PhysicalNavigationBridge, Retirement
from lingxi_navigation.physical_ros import RosSensorEvidence, RosTopics, digest_footprint


class Clock:
    def __init__(self):
        self.steady, self.ros = 10., 100_000_000_000

    def __call__(self):
        return self.steady

    def advance(self, seconds):
        self.steady += seconds
        self.ros += round(seconds * 1e9)


def header(frame, stamp):
    return NS(frame_id=frame, stamp=NS(sec=stamp // 1_000_000_000, nanosec=stamp % 1_000_000_000))


def quaternion():
    return NS(x=0., y=0., z=0., w=1.)


def transform(parent, child, stamp):
    return NS(header=header(parent, stamp), child_frame_id=child,
              transform=NS(rotation=quaternion(), translation=NS(x=0., y=0., z=0.)))


def cloud(stamp, frame="lidar"):
    return NS(header=header(frame, stamp), fields=[NS(name=name, offset=index * 4, datatype=7, count=1)
        for index, name in enumerate(("x", "y", "z"))], width=1, height=1, point_step=12,
        row_step=12, is_bigendian=False, data=struct.pack("fff", 0., 0., .2))


def grid(stamp):
    return NS(header=header("map", stamp), info=NS(width=1, height=1, resolution=.05,
        origin=NS(position=NS(x=0., y=0.), orientation=quaternion())), data=[0])


def odometry(stamp):
    return NS(header=header("map", stamp), child_frame_id="base_link",
              pose=NS(pose=NS(position=NS(x=0., y=0.), orientation=quaternion())))


class BufferStub:
    """Only records tf2 calls; does not implement transform mathematics."""
    def __init__(self, **kwargs):
        self.transforms, self.queries = [], []
        self.available = True

    def set_transform(self, value, authority):
        self.transforms.append((value, False))

    def set_transform_static(self, value, authority):
        self.transforms.append((value, True))

    def can_transform(self, target, source, when):
        self.queries.append((target, source, when.nanoseconds))
        return self.available

    def clear(self):
        self.transforms.clear()


class SensorTests(unittest.TestCase):
    def setUp(self):
        names = ("nav_msgs", "nav_msgs.msg", "sensor_msgs", "sensor_msgs.msg", "tf2_msgs", "tf2_msgs.msg",
                 "rclpy", "rclpy.qos", "rclpy.duration", "rclpy.time", "tf2_ros")
        modules = {name: ModuleType(name) for name in names}
        modules["nav_msgs.msg"].OccupancyGrid = type("OccupancyGrid", (), {})
        modules["nav_msgs.msg"].Odometry = type("Odometry", (), {})
        modules["sensor_msgs.msg"].PointCloud2 = type("PointCloud2", (), {})
        modules["tf2_msgs.msg"].TFMessage = type("TFMessage", (), {})
        modules["rclpy.qos"].QoSProfile = lambda **kwargs: NS(durability="volatile", **kwargs) if "durability" not in kwargs else NS(**kwargs)
        modules["rclpy.qos"].DurabilityPolicy = NS(TRANSIENT_LOCAL="latched")
        modules["rclpy.qos"].ReliabilityPolicy = NS(BEST_EFFORT="best", RELIABLE="reliable")
        modules["rclpy.duration"].Duration = lambda **kwargs: NS(**kwargs)
        modules["rclpy.time"].Time = lambda **kwargs: NS(**kwargs)
        modules["tf2_ros"].Buffer = BufferStub
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = Clock()
        self.subscriptions, self.destroyed = [], []
        self.node = NS(get_clock=lambda: NS(now=lambda: NS(nanoseconds=self.clock.ros)),
                       create_subscription=self.subscribe, destroy_subscription=self.destroyed.append)
        self.identity = MapEvidence("slam-" + "a" * 32, "a" * 64, "b" * 64, digest_footprint({"radius": .3}))
        self.sensors = RosSensorEvidence(self.node, self.identity, RosTopics(), map_frame="map",
            sensor_frame="lidar", footprint={"radius": .3}, clock=self.clock)
        self.addCleanup(self.sensors.close)

    def subscribe(self, kind, topic, callback, qos):
        value = NS(topic=topic, callback=callback, qos=qos)
        self.subscriptions.append(value)
        return value

    def feed(self, stamp=None):
        stamp = self.clock.ros if stamp is None else stamp
        self.sensors._cloud(cloud(stamp))
        self.sensors._odom(odometry(stamp))
        for callback in (self.sensors._global, self.sensors._local, self.sensors._terrain):
            callback(grid(stamp))

    def unavailable(self):
        with self.assertRaisesRegex(RuntimeError, "evidence_unavailable"):
            self.sensors.observe()

    def test_qos_matches_best_effort_sensors_and_latched_static_tf(self):
        qos = {item.topic: item.qos for item in self.subscriptions}
        self.assertEqual("best", qos["/lidar/points"].reliability)
        self.assertEqual(1, qos["/localization/odometry"].depth)
        self.assertEqual("latched", qos["/tf_static"].durability)
        self.assertEqual("reliable", qos["/tf_static"].reliability)

    def test_acquisition_age_is_retained_and_never_claims_motion(self):
        self.feed(self.clock.ros - 200_000_000)
        value = self.sensors.observe()
        self.assertAlmostEqual(9.8, value.observed_at)
        self.assertFalse(value.physical_motion_sent)
        self.clock.advance(.31)
        self.unavailable()

    def test_old_future_zero_and_wrong_frame_samples_are_not_admitted(self):
        for stamp in (0, self.clock.ros - 600_000_000, self.clock.ros + 1):
            with self.subTest(stamp=stamp):
                self.feed(stamp)
                self.unavailable()
        self.feed()
        self.sensors._cloud(cloud(self.clock.ros + 1, "wrong_sensor"))
        self.unavailable()

    def test_duplicate_or_out_of_order_stamps_cannot_renew_receipt(self):
        self.feed()
        receipt = self.sensors.receipts["cloud"]
        self.clock.advance(.2)
        self.assertFalse(self.sensors._cloud(cloud(receipt[0])))
        self.assertFalse(self.sensors._cloud(cloud(receipt[0] - 1)))
        self.assertEqual(receipt, self.sensors.receipts["cloud"])
        self.clock.advance(.31)
        self.unavailable()

    def test_every_required_grid_expires_independently(self):
        for name in ("global", "local", "terrain"):
            self.feed()
            self.clock.advance(.51)
            old = self.sensors.receipts[name]
            self.feed()
            self.sensors.receipts[name] = old
            with self.subTest(name=name):
                self.unavailable()
            self.clock.advance(.01)

    def test_bridge_receives_oldest_acquisition_not_observe_time(self):
        self.feed(self.clock.ros - 350_000_000)
        value = self.sensors.observe()
        self.assertTrue(value.checks["localization"])
        self.assertGreater(self.clock() - value.observed_at, .3)
        sensors, identity = self.sensors, self.identity
        class Backend:
            def observe(self):
                current = sensors.observe()
                return Observation(identity, current.observed_at, current.pose,
                    dict(current.checks, nav2Lifecycle=True, velocityOutput=True), False)
        bridge = PhysicalNavigationBridge(Backend(), identity, clock=self.clock)
        self.assertFalse(bridge._observe())
        self.assertFalse(any(bridge.last_checks.values()))

    def test_uninitialised_clock_and_bounded_tf_inventory_fail_closed(self):
        self.clock.ros = 0
        self.assertFalse(self.sensors._cloud(cloud(1)))
        self.assertTrue(self.sensors.clock_failed)
        self.clock.ros = 100_000_000_000
        self.sensors.clock_failed = False
        self.sensors.tf_parents = {"child" + str(index): ("root", True, None) for index in range(256)}
        self.assertFalse(self.sensors._tf(NS(transforms=[transform("map", "base_link", self.clock.ros)])))
        self.assertTrue(self.sensors.tf_failed)

    def test_tf2_queries_both_cloud_and_odometry_exact_timestamps(self):
        self.feed(self.clock.ros - 100_000_000)
        self.sensors._odom(odometry(self.clock.ros))
        self.sensors.observe()
        self.assertEqual([
            ("map", "base_link", self.clock.ros - 100_000_000),
            ("base_link", "lidar", self.clock.ros - 100_000_000),
            ("map", "base_link", self.clock.ros), ("base_link", "lidar", self.clock.ros)],
            self.sensors.tf_buffer.queries)
        self.sensors.tf_buffer.available = False
        original = self.sensors.observe()
        self.clock.advance(.1)
        self.feed()
        retained = self.sensors.observe()
        self.assertEqual(original.observed_at, retained.observed_at)
        self.assertEqual(original.pose, retained.pose)
        self.clock.advance(.31)
        self.feed()
        self.unavailable()

    def test_invalid_new_sensor_revokes_aligned_pair_without_waiting_for_expiry(self):
        self.feed()
        self.sensors.observe()
        self.sensors.tf_buffer.available = False
        self.assertFalse(self.sensors._cloud(cloud(self.clock.ros + 1)))
        self.unavailable()

    def test_split_tf_static_zero_stamp_is_retained_separately(self):
        self.assertTrue(self.sensors._tf_static(NS(transforms=[transform("base_link", "lidar", 0)])))
        self.assertTrue(self.sensors._tf(NS(transforms=[transform("map", "odom", self.clock.ros)])))
        self.assertTrue(self.sensors._tf(NS(transforms=[transform("odom", "base_link", self.clock.ros)])))
        self.assertEqual([True, False, False], [static for _, static in self.sensors.tf_buffer.transforms])

    def test_invalid_tf_and_changed_static_calibration_latch_admission_closed(self):
        self.sensors._tf_static(NS(transforms=[transform("base_link", "lidar", 0)]))
        changed = transform("base_link", "lidar", 0)
        changed.transform.translation.z = .6
        self.assertFalse(self.sensors._tf_static(NS(transforms=[changed])))
        self.assertFalse(self.sensors._tf(NS(transforms=[transform("map", "base_link", self.clock.ros)])))
        self.feed()
        self.unavailable()

    def test_tf_cycle_parent_change_and_stale_dynamic_are_rejected(self):
        candidates = ([transform("a", "b", self.clock.ros), transform("b", "a", self.clock.ros)],
                      [transform("a", "b", self.clock.ros), transform("c", "b", self.clock.ros)],
                      [transform("map", "base_link", self.clock.ros - 600_000_000)])
        for items in candidates:
            self.sensors.tf_failed = False
            self.sensors.tf_parents.clear()
            self.assertFalse(self.sensors._tf(NS(transforms=items)))
            self.assertTrue(self.sensors.tf_failed)

    def test_clock_pause_jump_and_rollback_latch_until_new_owner(self):
        for kind in ("pause", "jump", "rollback", "steady_rollback"):
            self.sensors.clock_failed = False
            self.sensors.anchor = None
            self.sensors.last_clock = None
            self.feed()
            if kind == "pause":
                self.clock.steady += .1
            elif kind == "jump":
                self.clock.ros += 1_000_000_000
            elif kind == "rollback":
                self.clock.ros -= 1
            else:
                self.clock.steady -= .001
            with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, "clock_discontinuity"):
                self.sensors.observe()
            self.assertTrue(self.sensors.clock_failed)
            with self.assertRaisesRegex(RuntimeError, "clock_unavailable"):
                self.sensors.observe()

    def test_partial_subscription_failure_destroys_already_created_handles(self):
        handles = []
        def create(*args):
            if len(handles) == 3:
                raise RuntimeError("synthetic_creation_failure")
            value = object()
            handles.append(value)
            return value
        node = NS(get_clock=self.node.get_clock, create_subscription=create, destroy_subscription=self.destroyed.append)
        with self.assertRaisesRegex(RuntimeError, "synthetic_creation_failure"):
            RosSensorEvidence(node, self.identity, RosTopics(), footprint={"radius": .3}, clock=self.clock)
        self.assertTrue(all(handle in self.destroyed for handle in handles))

    def test_running_bridge_retires_once_when_sensor_expires_and_does_not_restart(self):
        sensors, clock, identity = self.sensors, self.clock, self.identity
        self.feed()
        class Backend:
            def __init__(self):
                self.retired, self.started, self.queue = [], [], []
            def tick(self):
                pass
            def events(self):
                result, self.queue = self.queue, []
                return result
            def observe(self):
                item = sensors.observe()
                checks = dict(item.checks, nav2Lifecycle=True, velocityOutput=True)
                return Observation(identity, item.observed_at, item.pose, checks, False)
            def start(self, ticket, goal):
                self.started.append(ticket)
                self.queue.append((ticket, "accepted"))
            def retire(self, ticket, nonce):
                self.retired.append((ticket, nonce))
            def retirement(self, nonce):
                return Retirement(nonce, identity, self.retired[-1][0], clock(), True)
        backend = Backend()
        bridge = PhysicalNavigationBridge(backend, identity, clock=clock)
        base = dict(session=bridge.session, expectedEpoch=None, taskId="test", mapId=identity.mapId, evidence=asdict(identity))
        bridge.request(dict(base, id="1" * 32, action="stop"))
        bridge.tick()
        bridge.request(dict(base, id="2" * 32, action="start", expectedEpoch=None,
                            pointId="p", goal=dict(x=1., y=0., yaw=0.)))
        bridge.tick()
        self.assertEqual("running", bridge.phase)
        clock.advance(.51)
        bridge.tick()
        self.assertTrue(bridge.requires_stop)
        self.assertEqual("navigation_evidence_expired", bridge.detail)
        self.feed()
        bridge.tick()
        self.assertTrue(bridge.requires_stop)
        self.assertEqual(1, len(backend.started))
        self.assertEqual(2, len(backend.retired))


if __name__ == "__main__":
    unittest.main()
