"""Real owned Nav2 nodes, synthetic sensors and in-memory AIMDK. No robot IO."""
import copy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import struct
import sys
import time
import traceback
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
import test_physical_ros as fixtures
from test_nav2_producer import profile
from test_localization_guard import BINDING, snapshot
import test_physical_evidence_humble as evidence_fixtures
from lingxi_navigation.localization_guard import LocalizationGuard, LocalizationMonitor
from lingxi_navigation.nav2_producer import Nav2Generation, Nav2MapSelection, OwnedNav2Backend, spin_callbacks
from lingxi_navigation.physical_bridge import BridgeError, MapEvidence, PhysicalNavigationBridge
from lingxi_navigation.physical_maps import load_map_selection
from lingxi_navigation.owned_inputs import OwnedRosInputs, descriptor
from lingxi_navigation.physical_ros import AimdkVelocityOutput, FileMapActivator, digest_footprint


@unittest.skipUnless(os.environ.get('LINGXI_VERIFY_NAV2_PRODUCER_ROS') == '1',
                     'explicit isolated Humble required')
class ActualOwnedNav2Tests(unittest.TestCase):
    def setUp(self):
        if (set(os.listdir('/sys/class/net')) != {'lo'} or os.environ.get('ROS_DISTRO') != 'humble'
                or os.environ.get('ROS_LOCALHOST_ONLY') != '1'
                or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'):
            raise RuntimeError('isolated_loopback_humble_test_required')
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from nav_msgs.msg import OccupancyGrid, Odometry
        from sensor_msgs.msg import PointCloud2
        from tf2_msgs.msg import TFMessage
        self.root = Path(os.environ['LINGXI_NAV2_TEST_OUTPUT'])
        self.root.mkdir(mode=0o700)
        rclpy.init(args=[])
        self.addCleanup(rclpy.try_shutdown)
        self.node = rclpy.create_node('controlled_synthetic_nav2_producer_test', use_global_arguments=False)
        self.addCleanup(self.node.destroy_node)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.addCleanup(lambda: self.executor.shutdown(timeout_sec=1.))
        footprint = self.root / 'footprint.json'
        footprint.write_text('{"testOnlyRadius":0.3}', encoding='utf-8')
        self.identity = MapEvidence('slam-' + 'a' * 32, 'a' * 64, 'b' * 64, digest_footprint(footprint))
        self.profile = replace(profile(self.identity),
            **OwnedRosInputs('lingxi_offline_' + 'd' * 32).profile_options())
        self.localization_value = snapshot(self.identity)
        self.localization_value['state']['mapFromSensor'][0][3] = 0.
        self.localization_value['state']['mapFromSensor'][1][3] = 0.
        self.localization_value['state']['basePose']['mapFromBase'][0][3] = 0.
        self.localization_value['state']['basePose']['mapFromBase'][1][3] = 0.
        self.localization_value['inputs'] = descriptor(self.localization_value['state'], 'lingxi_offline_' + 'd' * 32)
        self.monitor = LocalizationMonitor('/synthetic-localization-not-a-service.sock', self.identity, BINDING,
            exchange=lambda: copy.deepcopy(self.localization_value), require_inputs=True)
        self.addCleanup(self.monitor.close)
        self.localization = LocalizationGuard(self.monitor)
        self.localization_enabled, self.published_frames = True, 7
        sensor_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        tf_qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE)
        latched_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.publishers = {}
        for name, kind, topic, qos in (
            ('cloud', PointCloud2, self.profile.cloud_topic, sensor_qos),
            ('odom', Odometry, self.profile.odometry_topic, sensor_qos),
            ('terrain', OccupancyGrid, self.profile.terrain_topic, latched_qos),
            ('tf', TFMessage, self.profile.tf_topic, tf_qos),
            ('tf_static', TFMessage, self.profile.tf_static_topic, latched_qos)):
            self.publishers[name] = self.node.create_publisher(kind, topic, qos)
        self.publishers['tf_static'].publish(TFMessage(transforms=[
            evidence_fixtures.ActualPhysicalEvidenceTests.transform(self.profile.base_frame, self.profile.sensor_frame, 0)]))
        old, _, self.messages, _, Node = fixtures.PhysicalRosTests()._aimdk_fake_modules()
        def restore():
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value
        self.addCleanup(restore)
        self.output_node = Node()
        self.output = AimdkVelocityOutput(self.output_node, self.identity)
        self.addCleanup(self.output.close)
        self.generations, self.trace = [], []
        def factory(ticket):
            generation = Nav2Generation(self.profile, ticket, self.root, footprint, self.executor)
            self.generations.append(generation)
            generation.connect(self.output, self.profile.limits,
                validate_observation=self.localization.validate_observation)
            return generation
        self.backend = OwnedNav2Backend(self.identity, self.output, factory, self.spin, localization=self.localization)
        self.backend_errors = []
        command = self.output.command
        def recorded_command(value, limits, observation):
            prior = self.output.last_velocity
            origin = self.output.armed_at if self.output.last_command_at is None else self.output.last_command_at
            source_origin = (self.output.armed_at if self.output.last_command_generated is None
                             else self.output.last_command_generated)
            try:
                return command(value, limits, observation)
            except Exception:
                self.backend_errors.append(repr(dict(previous=prior, command=asdict(value),
                    elapsed=time.monotonic() - origin, sourceElapsed=value.generated_at - source_origin)))
                raise
        self.output.command = recorded_command
        tick = self.backend.tick
        def recorded_tick():
            try:
                tick()
            except Exception:
                self.backend_errors.append(traceback.format_exc())
                sensors = self.backend.generation.sensors
                if sensors is not None:
                    steady, ros = time.monotonic(), self.node.get_clock().now().nanoseconds
                    self.backend_errors.append(repr(dict(tfFailed=sensors.tf_failed,
                        clockFailed=sensors.clock_failed, tfReady=sensors._tf_ready(),
                        ages={key: (steady - value[1], (ros - value[0]) / 1e9)
                              for key, value in sensors.receipts.items()})))
                raise
        self.backend.tick = recorded_tick
        self.addCleanup(self.backend.close)
        self.bridge = PhysicalNavigationBridge(self.backend, self.identity)
        observe = self.bridge._observe
        def recorded_observe():
            ready = observe()
            if not ready and self.bridge.phase in ('requested', 'running'):
                try:
                    value = self.backend.observe()
                    self.backend_errors.append(repr(dict(checks=value.checks,
                        acquisitionAge=time.monotonic() - value.observed_at)))
                except Exception:
                    self.backend_errors.append(traceback.format_exc())
            return ready
        self.bridge._observe = recorded_observe
        self.number = 0
        self.last_publish = 0.
        self.sensors_enabled = True

    def publish(self):
        from nav_msgs.msg import OccupancyGrid, Odometry
        from sensor_msgs.msg import PointCloud2, PointField
        from tf2_msgs.msg import TFMessage
        now = self.node.get_clock().now().nanoseconds
        def stamp(message, frame, ns):
            message.header.frame_id = frame
            message.header.stamp.sec, message.header.stamp.nanosec = divmod(ns, 1_000_000_000)
        cloud = PointCloud2()
        stamp(cloud, self.profile.sensor_frame, now - 20_000_000)
        cloud.width = cloud.height = 1
        cloud.point_step = cloud.row_step = 12
        cloud.fields = [PointField(name=name, offset=index * 4, datatype=PointField.FLOAT32, count=1)
            for index, name in enumerate(('x', 'y', 'z'))]
        cloud.data = struct.pack('fff', 2., 0., .2)
        odom = Odometry()
        scan_ns = now - 10_000_000
        stamp(odom, self.profile.map_frame, scan_ns)
        odom.child_frame_id = self.profile.base_frame
        odom.pose.pose.orientation.w = 1.
        grid = OccupancyGrid()
        stamp(grid, self.profile.map_frame, now)
        grid.info.width, grid.info.height, grid.info.resolution = 160, 160, .05
        grid.info.origin.position.x = grid.info.origin.position.y = -4.
        grid.info.origin.orientation.w = 1.
        grid.data = [0] * (160 * 160)
        self.publishers['cloud'].publish(cloud)
        self.publishers['odom'].publish(odom)
        self.publishers['terrain'].publish(grid)
        for ns in (now - 40_000_000, now):
            self.publishers['tf'].publish(TFMessage(transforms=[
                evidence_fixtures.ActualPhysicalEvidenceTests.transform(self.profile.map_frame, self.profile.base_frame, ns)]))
        self.published_frames += 1
        self.localization_value['state']['lastFrame'] = self.published_frames
        self.localization_value['state']['basePose'].update(lastFrame=self.published_frames,
            scanHeaderNs=str(scan_ns), effectiveScanNs=str(scan_ns))

    def spin(self):
        message = self.messages.McCommonState()
        message.input_source.name, message.fsm_state.current_state = 'lingxi-next', 2
        self.output._state_message(message)
        now = time.monotonic()
        if self.sensors_enabled and now - self.last_publish >= .025:
            self.last_publish = now
            self.publish()
        spin_callbacks(self.executor)
        self.localization_value['state']['state'] = 'tracking' if self.localization_enabled else 'lost'
        if self.monitor.thread is None:
            self.monitor.refresh()

    def until(self, predicate, seconds=35.):
        deadline = time.monotonic() + seconds
        while True:
            self.bridge.tick()
            if predicate():
                return
            if (time.monotonic() >= deadline
                    or (self.bridge.phase == 'failed' and self.bridge.retiring is None)):
                self.fail('actual_owned_nav2_timeout: ' + str(self.bridge.state(poll=False))
                          + '\n'.join(self.backend_errors))
            time.sleep(.002)

    def command(self, action):
        self.number += 1
        if action == 'activate_map':
            return self.bridge.request(dict(id=f'{self.number:032x}', action=action, session=self.bridge.session,
                mapId=self.identity.mapId, evidence=asdict(self.identity)))
        ticket = self.bridge.ticket
        request = dict(id=f'{self.number:032x}', action=action, session=self.bridge.session,
            taskId=ticket.task_id if ticket else 'tour', expectedEpoch=ticket.epoch if ticket else None,
            mapId=self.identity.mapId, evidence=asdict(self.identity))
        if action == 'start':
            request.update(pointId='test-point', goal=dict(x=1., y=0., yaw=0.))
        elif self.bridge.point_id is not None:
            request['pointId'] = self.bridge.point_id
        return self.bridge.request(request)

    def record(self, stage):
        self.trace.append(dict(stage=stage, state=self.bridge.state(poll=False),
            attemptedOutputCount=len(self.output_node.publisher.messages)))

    def nonzero_count(self):
        return sum(any(getattr(message, name) != 0. for name in
                   ('forward_velocity', 'lateral_velocity', 'angular_velocity'))
                   for message in self.output_node.publisher.messages)

    def test_real_launch_guard_activation_pause_resume_localization_and_sensor_loss(self):
        self.command('stop')
        self.until(lambda: self.bridge.phase == 'idle' and not self.bridge.requires_stop)
        self.assertEqual([], self.generations)
        self.assertFalse(self.bridge.state(poll=False)['navigationReady'])
        self.assertEqual(0, self.nonzero_count())
        self.command('activate_map')
        self.until(lambda: self.bridge.state(poll=False)['navigationReady'])
        self.assertEqual(1, len(self.generations))
        self.assertFalse(self.output.armed)
        self.assertFalse(self.output.physical_motion_sent)
        self.record('real_nav2_prepared_without_motion')
        self.command('start')
        self.until(lambda: self.bridge.phase == 'running' and self.nonzero_count() >= 10)
        first = self.bridge.ticket
        self.record('real_action_and_twist_in_memory_only')
        self.command('pause')
        self.until(lambda: self.bridge.phase == 'paused' and len(self.generations) == 2)
        self.assertTrue(self.generations[0].quiescent())
        self.assertFalse(self.generations[0].process._live_group())
        self.assertFalse(self.output.armed)
        self.record('paused_after_owned_process_group_exit')
        self.until(lambda: self.bridge.state(poll=False)['navigationReady'])
        count = self.nonzero_count()
        self.command('resume')
        self.assertNotEqual(first.epoch, self.bridge.ticket.epoch)
        self.until(lambda: self.bridge.phase == 'running' and self.nonzero_count() >= count + 10)
        self.record('resumed_with_fresh_namespace')
        self.localization_enabled = False
        self.until(lambda: self.bridge.phase == 'failed' and self.bridge.retiring is None, seconds=5.)
        self.assertTrue(self.sensors_enabled)
        self.assertFalse(self.output.armed)
        self.assertTrue(self.generations[1].quiescent())
        self.assertEqual(0., self.output_node.publisher.messages[-1].forward_velocity)
        self.record('localization_loss_stops_with_sensors_still_publishing')
        count = self.nonzero_count()
        self.localization_enabled = True
        for _ in range(20):
            self.bridge.tick()
            time.sleep(.01)
        self.assertIsNotNone(self.monitor.current())
        self.assertIsNone(self.localization.current())
        self.assertEqual(count, self.nonzero_count())
        self.assertEqual(2, len(self.generations))
        with self.assertRaises(BridgeError):
            self.command('resume')
        self.command('stop')
        self.until(lambda: self.bridge.phase == 'idle' and not self.bridge.requires_stop)
        self.assertEqual(2, len(self.generations))
        self.command('activate_map')
        self.until(lambda: self.bridge.state(poll=False)['navigationReady'])
        self.command('start')
        self.until(lambda: self.bridge.phase == 'running' and self.nonzero_count() >= count + 10)
        self.record('explicit_stop_activation_and_new_task_required_after_localization_loss')
        self.sensors_enabled = False
        self.until(lambda: self.bridge.phase == 'failed' and self.bridge.retiring is None, seconds=5.)
        self.assertTrue(self.bridge.requires_stop)
        self.assertFalse(self.output.armed)
        self.assertTrue(self.generations[2].quiescent())
        self.assertEqual(0., self.output_node.publisher.messages[-1].forward_velocity)
        for _ in range(10):
            self.bridge.tick()
        self.assertEqual(3, len(self.generations))
        self.record('sensor_expiry_no_automatic_restart')
        self.verify_map_switch()
        (self.root / 'result.json').write_text(json.dumps(dict(schema=1, testOnly=True,
            robotConnected=False, vendorPublisher=False, nav2ProcessesExecuted=True,
            syntheticSensors=True, syntheticLocalization=True, localizationSocketExecuted=False,
            physicalCalibrationValidated=False, trace=self.trace), indent=2), encoding='utf-8')

    def verify_map_switch(self):
        self.localization.activation = None
        self.sensors_enabled = True
        self.command('stop')
        self.until(lambda: self.bridge.phase == 'idle' and not self.bridge.requires_stop)
        self.assertIsNone(self.backend.generation)
        old_identity, count = self.identity, self.nonzero_count()
        catalog = self.root / 'catalog'
        directory = catalog / 'second-map'
        directory.mkdir(parents=True)
        (directory / 'map-ready.json').write_text(json.dumps(dict(schemaVersion=3, session='c' * 32,
            freshProcessReloadPassed=True, inputMode='recorded', mappingEngine='glim-full-async-v1',
            savedPointsSha256=BINDING['points_sha256'], sensorProvenance=dict(profileSha256=BINDING['profile_sha256']))),
            encoding='utf-8')
        (directory / 'calibration.json').write_text('{"testOnly":true}', encoding='utf-8')
        footprint = self.root / 'footprint.json'
        (directory / 'footprint.json').write_bytes(footprint.read_bytes())
        activator = FileMapActivator(catalog)
        self.identity = activator.evidence('slam-' + 'c' * 32)
        self.profile = replace(self.profile, limits=replace(self.profile.limits, identity=self.identity),
            **OwnedRosInputs('lingxi_offline_' + 'e' * 32).profile_options())
        self.localization_value['runtime']['selectedMap'] = dict(mapId=self.identity.mapId,
            receiptSha256=self.identity.receiptSha256)
        self.localization_value['state']['binding'].update(map_id=self.identity.mapId,
            receipt_sha256=self.identity.receiptSha256)
        self.localization_value['inputs'] = descriptor(self.localization_value['state'], 'lingxi_offline_' + 'e' * 32)
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        for key, topic in (('cloud', self.profile.cloud_topic), ('odom', self.profile.odometry_topic),
                           ('terrain', self.profile.terrain_topic), ('tf', self.profile.tf_topic),
                           ('tf_static', self.profile.tf_static_topic)):
            old = self.publishers[key]
            message_type = old.msg_type
            self.node.destroy_publisher(old)
            qos = QoSProfile(depth=1 if key != 'tf' else 100,
                reliability=ReliabilityPolicy.BEST_EFFORT if key in ('cloud', 'odom') else ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL if key in ('terrain', 'tf_static') else DurabilityPolicy.VOLATILE)
            self.publishers[key] = self.node.create_publisher(message_type, topic, qos)
        from tf2_msgs.msg import TFMessage
        self.publishers['tf_static'].publish(TFMessage(transforms=[
            evidence_fixtures.ActualPhysicalEvidenceTests.transform(self.profile.base_frame, self.profile.sensor_frame, 0)]))
        profile_file = self.root / 'map-switch-profile.json'
        limits = self.profile.limits
        profile_file.write_text(json.dumps(dict(schema=1, footprintSha256=self.identity.footprintSha256,
            clearanceRadiusMetres=.3, stoppingMarginMetres=.1, forward=limits.forward,
            lateral=limits.lateral, angular=limits.angular, acceleration=limits.acceleration,
            deceleration=limits.deceleration, angularAcceleration=limits.angular_acceleration,
            commandAge=limits.command_age, firstCommandTimeout=limits.first_command_timeout)), encoding='utf-8')
        def reader(endpoint, identity, binding, **kwargs):
            self.monitor = LocalizationMonitor(endpoint, identity, binding,
                exchange=lambda: copy.deepcopy(self.localization_value), **kwargs)
            self.addCleanup(self.monitor.close)
            return self.monitor

        def loader(map_id):
            with patch('lingxi_navigation.physical_maps.LocalizationMonitor', side_effect=reader):
                selection = load_map_selection(activator, map_id, footprint=footprint,
                    profile_file=profile_file, profile_options={}, localization_endpoint='/synthetic-only.sock',
                    run_root=self.root, executor=self.executor, output=self.output, operation_gate=None,
                    activate=True, input_mode='owned-localization')
            self.localization = selection.localization
            def factory(ticket):
                generation = selection.factory(ticket)
                self.generations.append(generation)
                return generation
            return Nav2MapSelection(selection.identity, selection.localization, factory)

        self.backend.map_loader = loader
        self.number += 1
        self.assertIsNone(self.bridge.request(dict(id=f'{self.number:032x}', action='activate_map',
            session=self.bridge.session, mapId=self.identity.mapId)))
        self.assertEqual(self.identity, self.output.identity)
        self.assertNotEqual(old_identity, self.output.identity)
        self.assertIsNone(self.output.completed_retirement)
        self.assertFalse(self.output.armed)
        self.assertEqual(count, self.nonzero_count())
        self.until(lambda: self.bridge.phase == 'idle' and not self.bridge.requires_stop)
        self.assertEqual(self.identity, self.output.completed_retirement.identity)
        self.assertIsNone(self.output.completed_retirement.ticket)
        self.until(lambda: self.bridge.state(poll=False)['navigationReady'])
        self.assertEqual(self.identity, self.generations[-1].identity)
        self.record('new_map_fresh_stop_and_real_nav2_ready_without_motion')
        self.command('start')
        self.until(lambda: self.bridge.phase == 'running' and self.nonzero_count() >= count + 10)
        self.record('new_map_real_action_and_twist_in_memory_only')
        self.command('stop')
        self.until(lambda: self.bridge.phase == 'idle' and not self.bridge.requires_stop)
        warmup = self.backend.generation
        self.assertIsNotNone(warmup)
        count = self.nonzero_count()
        self.command('activate_map')
        self.assertTrue(warmup.quiescent())
        self.assertFalse(warmup.process._live_group())
        self.assertIsNone(self.output.ticket)
        self.until(lambda: self.bridge.phase == 'idle' and not self.bridge.requires_stop)
        self.assertIsNone(self.output.completed_retirement.ticket)
        self.assertEqual(count, self.nonzero_count())
        self.record('same_map_reconfirmation_retires_old_ticket_and_unused_warmup')


if __name__ == '__main__':
    unittest.main()
