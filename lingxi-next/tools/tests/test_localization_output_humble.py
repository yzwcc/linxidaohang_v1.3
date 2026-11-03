"""Actual private Humble transport with synthetic poses/rays; no physical IO."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from test_localization_output import SyntheticLiveFixture


@unittest.skipUnless(os.environ.get('LINGXI_VERIFY_LOCALIZATION_OUTPUT_ROS') == '1', 'explicit isolated Humble required')
class HumbleOutputTests(SyntheticLiveFixture, unittest.TestCase):
    def setUp(self):
        if set(os.listdir('/sys/class/net')) != {'lo'} or os.environ.get('ROS_LOCALHOST_ONLY') != '1':
            raise RuntimeError('isolated_loopback_humble_required')
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from nav_msgs.msg import Odometry
        from sensor_msgs.msg import PointCloud2
        from tf2_msgs.msg import TFMessage
        self.prepare()
        self.context = Context()
        rclpy.init(args=[], context=self.context)
        self.addCleanup(self.context.try_shutdown)
        self.node = rclpy.create_node('synthetic_scan_output_test', context=self.context,
            use_global_arguments=False, enable_rosout=False, start_parameter_services=False)
        self.addCleanup(self.node.destroy_node)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.addCleanup(lambda: self.executor.shutdown(timeout_sec=1.))
        self.output.setup(self.node)
        self.addCleanup(self.output.close)
        self.samples = dict(odometry=[], points=[], tf=[])
        for kind, message in (('odometry', Odometry), ('points', PointCloud2), ('tf', TFMessage)):
            self.node.create_subscription(message, '/' + self.output.namespace + '/' + kind,
                lambda msg, key=kind: self.samples[key].append(msg), 10)
        self.spin(.4)

    def spin(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.executor.spin_once(timeout_sec=.005)

    def send(self):
        stamp = self.node.get_clock().now().nanoseconds - 110_000_000
        self.offer(stamp=stamp)
        self.offer(2, stamp + 100_000_000, .05, .1)
        self.spin(.15)
        self.assertTrue(all(self.samples.values()))
        return stamp + 100_000_000

    def test_original_time_pose_cloud_and_private_tf_reach_actual_ros_and_align(self):
        import numpy as np
        from scipy.spatial.transform import Rotation
        from rclpy.time import Time
        from tf2_ros import Buffer
        self.extrinsic[:3, :3] = Rotation.from_euler('xyz', (.3, -.2, .4)).as_matrix()
        expected = self.send()
        odom, cloud, transforms = (self.samples[name][-1] for name in ('odometry', 'points', 'tf'))
        for message in (odom, cloud, *transforms.transforms):
            self.assertEqual(expected, message.header.stamp.sec * 10**9 + message.header.stamp.nanosec)
        self.assertEqual(('lingxi_saved_map', 'base_link'), (odom.header.frame_id, odom.child_frame_id))
        self.assertEqual('lidar_chest_front', cloud.header.frame_id)
        np.testing.assert_allclose(self.points, np.frombuffer(cloud.data, dtype='<f8').reshape(-1, 3), atol=1e-12)
        self.assertEqual([('lingxi_saved_map', 'base_link'), ('base_link', 'lidar_chest_front')],
            [(value.header.frame_id, value.child_frame_id) for value in transforms.transforms])
        buffer = Buffer()
        for value in transforms.transforms:
            buffer.set_transform(value, 'synthetic_same_scan')
        transform = buffer.lookup_transform('lingxi_saved_map', 'lidar_chest_front', Time(nanoseconds=expected))
        translation, quaternion = transform.transform.translation, transform.transform.rotation
        sensor = np.eye(4)
        sensor[:3, 3] = [translation.x, translation.y, translation.z]
        sensor[:3, :3] = Rotation.from_quat([quaternion.x, quaternion.y, quaternion.z, quaternion.w]).as_matrix()
        sample = self.output.snapshot()
        np.testing.assert_allclose(sample.map_from_base @ sample.base_from_sensor, sensor, atol=1e-12)
        registered = np.asarray(self.session.state()['mapFromSensor'])
        registered[:3, 3] *= self.session.base_scale
        np.testing.assert_allclose(registered, sensor, atol=1e-12)
        self.assertAlmostEqual(.5 * np.cos(.1), odom.twist.twist.linear.x)
        self.assertAlmostEqual(1., odom.twist.twist.angular.z)
        self.assertEqual(1e6, odom.pose.covariance[0])
        count = self.output.publications
        self.spin(.1)
        self.assertEqual(count, self.output.publications)
        self.assertFalse(self.output.summary()['physicalMotionSent'])

    def test_ros_clock_freeze_loss_and_late_result_do_not_republish_or_resume(self):
        from rclpy.parameter import Parameter
        self.send()
        count = self.output.publications
        self.output.node.set_parameters([Parameter('use_sim_time', value=True)])
        self.session.phase = 'lost'
        self.spin(.08)
        self.assertIsNone(self.output.snapshot())
        self.session.phase = 'tracking'
        self.offer(3, self.node.get_clock().now().nanoseconds)
        self.spin(.08)
        self.assertEqual(count, self.output.publications)
        self.output.close(); self.output.close()
        self.assertEqual([self.node], self.executor.get_nodes())
        self.assertFalse(self.output.summary()['failed'])

    def test_expired_or_future_sensor_time_is_not_replaced_by_current_ros_time(self):
        now = self.node.get_clock().now().nanoseconds
        self.offer(stamp=now - 600_000_000)
        self.offer(2, now - 500_000_000)
        self.spin(.1)
        self.assertFalse(any(self.samples.values()))
        self.offer(3, now + 600_000_000)
        self.offer(4, now + 700_000_000)
        self.spin(.1)
        self.assertFalse(any(self.samples.values()))
        self.assertEqual(0, self.output.publications)

    def test_missing_executor_and_partial_startup_leave_no_publishers_or_nodes(self):
        from types import SimpleNamespace
        import rclpy
        from live_localization_ros import LiveLocalizationRosOutput
        output = LiveLocalizationRosOutput(self.session)
        with self.assertRaisesRegex(ValueError, 'existing_sensor_executor'):
            output.setup(SimpleNamespace(executor=None))
        self.assertTrue(output.closed)
        self.assertTrue(output.failed)
        self.assertCountEqual([self.node, self.output.node], self.executor.get_nodes())

        for stage in ('publisher', 'attach'):
            with self.subTest(stage=stage):
                output = LiveLocalizationRosOutput(self.session)
                created = rclpy.create_node('partial_output', namespace=output.namespace, context=self.context,
                    use_global_arguments=False, enable_rosout=False, start_parameter_services=False)
                publisher = created.create_publisher
                calls = []
                def create(*args):
                    if calls:
                        raise RuntimeError('synthetic_publisher_failure')
                    calls.append(publisher(*args))
                    return calls[-1]
                with patch('rclpy.create_node', return_value=created):
                    if stage == 'publisher':
                        with patch.object(created, 'create_publisher', side_effect=create), self.assertRaisesRegex(
                                RuntimeError, 'synthetic_publisher_failure'):
                            output.setup(self.node)
                    else:
                        with patch.object(self.executor, 'add_node', return_value=False), self.assertRaises(RuntimeError):
                            output.setup(self.node)
                self.assertTrue(output.failed)
                self.assertTrue(output.closed)
                self.assertIsNone(output.snapshot())
                self.assertCountEqual([self.node, self.output.node], self.executor.get_nodes())
                self.assertEqual((), tuple(created.publishers))
                output.close()

    def test_cleanup_failure_still_removes_all_ros_resources_and_forbids_restart(self):
        node, original = self.output.node, self.output.node.destroy_timer
        def failed(timer):
            original(timer)
            raise RuntimeError('synthetic_timer_cleanup_failure')
        with patch.object(node, 'destroy_timer', side_effect=failed), self.assertRaisesRegex(
                RuntimeError, 'localization_ros_output_cleanup_failed'):
            self.output.close()
        self.assertTrue(self.output.failed)
        self.assertEqual([self.node], self.executor.get_nodes())
        self.assertEqual((), tuple(node.publishers))
        with self.assertRaisesRegex(RuntimeError, 'cannot_restart'):
            self.output.setup(self.node)


if __name__ == '__main__':
    unittest.main()
