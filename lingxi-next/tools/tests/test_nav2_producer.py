"""Offline ownership/configuration contracts; no ROS or physical output."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
import test_physical_ros as fixtures
from test_physical_bridge import FakeClock, evidence, request_id
from lingxi_navigation.nav2_producer import Nav2Generation, Nav2MapSelection, PhysicalNav2Profile, OwnedNav2Backend, load_profile, prepare_producer, spin_callbacks
from lingxi_navigation.physical_bridge import CHECKS, BridgeError, Observation, PhysicalNavigationBridge, Ticket
from lingxi_navigation.physical_ros import AimdkVelocityOutput
from lingxi_navigation.physical_velocity import VelocityLimits, controller_namespace


def profile(identity=None):
    return PhysicalNav2Profile(VelocityLimits(identity or evidence(), .2, 0., .3, .3, .4, .6), .3, .1,
        'map', 'base_link', 'lidar', '/test/points', '/test/odometry', '/test/terrain')


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.profile = profile()
        self.ticket = Ticket('preparing', 'd' * 32)

    def test_physical_profile_preserves_real_time_frames_support_and_owned_topics(self):
        value = self.profile.parameters(self.ticket, self.root)
        for name in ('global_costmap', 'local_costmap'):
            data = value[name][name]['ros__parameters']
            self.assertEqual('map', data['global_frame'])
            self.assertFalse(data['use_sim_time'])
            self.assertEqual(['static_layer', 'inflation_layer'], data['plugins'])
            self.assertEqual('/test/terrain', data['static_layer']['map_topic'])
            self.assertEqual(10., data['publish_frequency'])
        smoother = value['velocity_smoother']['ros__parameters']
        self.assertEqual([.3, .3, .6], smoother['max_accel'])
        self.assertEqual([-.4, -.4, -.6], smoother['max_decel'])
        self.assertEqual('/test/odometry', smoother['odom_topic'])
        self.assertEqual(.15, smoother['velocity_timeout'])
        self.assertNotIn('/aima/', json.dumps(value))

    def test_executor_drains_bursts_with_callback_and_steady_time_bounds(self):
        executor = NS(spin_once=Mock())
        spin_callbacks(executor, clock=lambda: 1.)
        self.assertEqual(64, executor.spin_once.call_count)
        executor.spin_once.assert_called_with(timeout_sec=0.)
        executor.spin_once.reset_mock()
        clock = Mock(side_effect=(1., 1.001, 1.006))
        spin_callbacks(executor, clock=clock)
        self.assertEqual(2, executor.spin_once.call_count)

    def test_profile_rejects_invalid_frame_topics_lateral_and_braking(self):
        for changes in (dict(base_frame='map'), dict(cloud_topic='relative'),
            dict(tf_topic='/test/points'), dict(map_frame='map//bad'),
            dict(limits=replace(self.profile.limits, lateral=.1)),
            dict(limits=replace(self.profile.limits, deceleration=.01))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.profile, **changes)

    def test_profile_requires_exact_footprint_bound_configuration(self):
        limits = self.profile.limits
        value = dict(schema=1, footprintSha256=limits.identity.footprintSha256,
            clearanceRadiusMetres=.3, stoppingMarginMetres=.1, forward=.2, lateral=0., angular=.3,
            acceleration=.3, deceleration=.4, angularAcceleration=.6, commandAge=.15, firstCommandTimeout=1.)
        path = self.root / 'profile.json'
        kwargs = dict(map_frame='map', base_frame='base_link', sensor_frame='lidar',
            cloud_topic='/test/points', odometry_topic='/test/odometry', terrain_topic='/test/terrain')
        path.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(self.profile, load_profile(path, evidence(), **kwargs))
        for bad in (dict(value, footprintSha256='d' * 64), dict(value, schema=True), dict(value, extra=1)):
            path.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_profile(path, evidence(), **kwargs)
        path.write_text('{"schema":1,' + json.dumps(value)[1:], encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            load_profile(path, evidence(), **kwargs)

    def test_manifest_checks_all_launch_inputs_and_rejects_overwrite(self):
        root = self.root / self.ticket.epoch
        manifest = prepare_producer(self.profile, self.ticket, root)
        verify = runpy.run_path(str(root / 'nav2_physical.launch.py'))['verify_preparation']
        self.assertEqual(manifest, verify(root))
        self.assertEqual(controller_namespace(self.ticket), manifest['namespace'])
        parameters = json.loads((root / 'nav2-parameters.yaml').read_text(encoding='utf-8'))
        self.assertEqual({controller_namespace(self.ticket)[1:]}, set(parameters))
        with self.assertRaisesRegex(ValueError, 'new_generation'):
            prepare_producer(self.profile, self.ticket, root)
        with (root / 'navigate.xml').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'file_identity'):
            verify(root)


class FakeGeneration:
    def __init__(self, ticket, identity, clock, output):
        self.ticket, self.identity, self.clock, self.output = ticket, identity, clock, output
        self.claimed = self.closing = self.ready = self.terminated = self.crashed = False
        self.start_error = None
        self.start_calls, self.queue = [], []
        self.driver = NS(generation=None, events=self.events, cancel=self.cancel,
                         action_retired=lambda _: False)
        self.controller = NS(retire=lambda: None)
        self.backend = self

    def observe(self):
        if not self.ready:
            raise RuntimeError('current_ros_sensor_evidence_unavailable')
        return Observation(self.identity, self.clock(), dict(x=0., y=0., yaw=0.),
            dict.fromkeys(CHECKS, True), self.output.physical_motion_sent)

    def start(self, ticket, goal):
        self.start_calls.append((ticket, goal))
        if self.start_error:
            raise self.start_error
        if not self.output.arm(ticket, self.identity):
            raise RuntimeError('output_arm_failed')
        self.driver.generation = NS(ticket=ticket)
        self.queue.append((ticket, 'accepted'))

    def tick(self):
        if self.crashed:
            raise RuntimeError('owned_nav2_producer_exited')

    def events(self):
        result, self.queue = tuple(self.queue), []
        return result

    def cancel(self, ticket):
        pass

    def begin_close(self):
        self.closing = True

    def quiescent(self):
        return self.closing and self.terminated

    def release_operation(self):
        if not self.quiescent():
            raise RuntimeError('synthetic_generation_not_quiescent')


class CleanupTests(unittest.TestCase):
    def generation(self):
        value = Nav2Generation.__new__(Nav2Generation)
        value.closing = False
        value.operation_lease = Mock()
        value.node, value.executor = Mock(), Mock()
        value.controller, value.driver, value.sensors, value.process = Mock(), Mock(), Mock(), Mock()
        value.process.quiescent.return_value = True
        return value

    def test_ros_failure_still_closes_other_sources_and_signals_whole_process(self):
        value = self.generation()
        controller, driver, sensors, process, node = (
            value.controller, value.driver, value.sensors, value.process, value.node)
        controller.retire.side_effect = RuntimeError('synthetic_controller_cleanup_failure')
        with self.assertRaisesRegex(RuntimeError, 'ros_cleanup_unconfirmed'):
            value.begin_close()
        driver.close.assert_called_once()
        sensors.close.assert_called_once()
        process.stop.assert_called_once()
        self.assertFalse(value.quiescent())
        process.quiescent.assert_called_once()
        node.destroy_node.assert_not_called()
        controller.retire.side_effect = None
        value.close()
        self.assertTrue(value.quiescent())
        node.destroy_node.assert_called_once()

    def test_persistent_ros_cleanup_failure_is_bounded_and_not_reported_quiescent(self):
        value = self.generation()
        value.controller.retire.side_effect = RuntimeError('synthetic_cleanup_failure')
        with patch('lingxi_navigation.nav2_producer.time.monotonic', side_effect=[0., 3.]), \
                self.assertRaisesRegex(RuntimeError, 'owned_nav2_cleanup_unconfirmed'):
            value.close()
        value.process.stop.assert_called_once()
        value.process.quiescent.assert_called_once()
        value.operation_lease.close.assert_not_called()

    def test_operation_lease_is_not_released_until_generation_is_quiescent(self):
        value = self.generation()
        lease = value.operation_lease
        with self.assertRaisesRegex(RuntimeError, 'quiescent_nav2_operation'):
            value.release_operation()
        lease.close.assert_not_called()
        value.close()
        lease.close.assert_called_once()
        self.assertIsNone(value.operation_lease)

    def test_busy_operation_gate_does_not_create_any_ros_node(self):
        gate = NS(acquire=Mock(side_effect=RuntimeError('synthetic_operation_busy')))
        ros = NS(create_node=Mock())
        with patch.dict(sys.modules, {'rclpy': ros}), self.assertRaisesRegex(RuntimeError, 'operation_busy'):
            Nav2Generation(profile(), Ticket('preparing', 'd' * 32), Path('/unused'),
                           Path('/unused'), Mock(), operation_gate=gate)
        ros.create_node.assert_not_called()

    def test_partial_process_creation_failure_releases_all_existing_ros_resources(self):
        node, executor, sensors, driver = Mock(), Mock(), Mock(), Mock()
        with patch.dict(sys.modules, {'rclpy': NS(create_node=Mock(return_value=node))}), \
                patch('lingxi_navigation.physical_ros.RosSensorEvidence', return_value=sensors), \
                patch('lingxi_navigation.nav2_action.Nav2ActionDriver', return_value=driver), \
                patch('lingxi_navigation.nav2_producer.Nav2Process', side_effect=RuntimeError('synthetic_launch_failure')), \
                self.assertRaisesRegex(RuntimeError, 'synthetic_launch_failure'):
            Nav2Generation(profile(), Ticket('preparing', 'd' * 32), Path('/unused'), Path('/unused'), executor)
        sensors.close.assert_called_once()
        driver.close.assert_called_once()
        node.destroy_node.assert_called_once()
        executor.remove_node.assert_called_once_with(node)

    def test_backend_close_fences_output_even_when_process_cleanup_fails(self):
        output, generation = Mock(), Mock()
        backend = OwnedNav2Backend(evidence(), output, Mock(), Mock())
        backend.generation = generation
        output.fence_controller.side_effect = RuntimeError('synthetic_output_failure')
        with self.assertRaisesRegex(RuntimeError, 'synthetic_output_failure'):
            backend.close()
        generation.close.assert_called_once_with(release_operation=False)
        generation.release_operation.assert_not_called()
        self.assertIs(generation, backend.generation)
        self.assertTrue(backend.closed)
        output.fence_controller.side_effect = None
        backend.close()
        generation.release_operation.assert_called_once()
        self.assertIsNone(backend.generation)

    def test_entrypoint_cleanup_attempts_every_resource_and_reports_failed_shutdown(self):
        close = runpy.run_path(str(ROOT / 'robot-navigation/bridge/physical_nav_bridge.py'))['_close_runtime']
        calls = []
        def call(name, fail=False):
            def run(**kwargs):
                calls.append(name)
                if fail:
                    raise RuntimeError('synthetic_cleanup_failure')
            return run
        rclpy, node = NS(try_shutdown=call('ros')), NS(destroy_node=call('node'))
        executor = NS(shutdown=Mock(return_value=False))
        output, backend, socket = (NS(close=call(name, name == 'backend'))
                                  for name in ('output', 'backend', 'socket'))
        with patch('sys.stderr'):
            errors = close(rclpy, node, executor, output, backend, socket)
        self.assertEqual(['socket', 'backend', 'output', 'node', 'ros'], calls)
        executor.shutdown.assert_called_once_with(timeout_sec=1.)
        self.assertEqual(2, len(errors))

    def test_shutdown_without_fresh_output_retirement_retains_operation_lease(self):
        output, generation = Mock(), Mock()
        output.complete_retirement.return_value = None
        backend = OwnedNav2Backend(evidence(), output, Mock(), Mock())
        backend.generation = generation
        with patch('lingxi_navigation.nav2_producer.time.monotonic', side_effect=(0., 3.)), \
                self.assertRaisesRegex(RuntimeError, 'shutdown_output_retirement_unconfirmed'):
            backend.close()
        generation.close.assert_called_once_with(release_operation=False)
        generation.release_operation.assert_not_called()
        self.assertIs(generation, backend.generation)


class OwnedBackendTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.identity = FakeClock(), evidence()
        old, _, self.messages, _, Node = fixtures.PhysicalRosTests()._aimdk_fake_modules()
        def restore():
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value
        self.addCleanup(restore)
        self.node = Node()
        self.output = AimdkVelocityOutput(self.node, self.identity, clock=self.clock)
        self.addCleanup(self.output.close)
        for _ in range(4):
            self.output.poll()
        self.refresh()
        self.generations = []
        def factory(ticket):
            value = FakeGeneration(ticket, self.identity, self.clock, self.output)
            self.generations.append(value)
            return value
        self.backend = OwnedNav2Backend(self.identity, self.output, factory, lambda: None,
                                       clock=self.clock, startup_timeout=2.)
        self.bridge = PhysicalNavigationBridge(self.backend, self.identity, clock=self.clock)
        self.number = 0

    def refresh(self, advance=.01):
        self.clock.advance(advance)
        message = self.messages.McCommonState()
        message.input_source.name, message.fsm_state.current_state = 'lingxi-next', 2
        self.output._state_message(message)

    def command(self, action):
        self.number += 1
        value = dict(id=request_id(self.number), action=action, session=self.bridge.session,
            taskId=self.bridge.ticket.task_id if self.bridge.ticket else 'tour',
            expectedEpoch=self.bridge.ticket.epoch if self.bridge.ticket else None,
            mapId=self.identity.mapId, evidence=asdict(self.identity))
        if action == 'start':
            value.update(pointId='a', goal=dict(x=1., y=0., yaw=0.))
        elif self.bridge.point_id is not None:
            value['pointId'] = self.bridge.point_id
        return self.bridge.request(value)

    def boot(self):
        self.command('stop')
        self.refresh()
        self.bridge.tick()
        self.assertEqual('idle', self.bridge.phase)
        self.assertEqual(1, len(self.generations))
        self.generations[-1].ready = True
        self.bridge.tick()

    def finish_retirement(self):
        self.refresh(.31)
        self.bridge.tick()
        if self.backend.generation:
            self.backend.generation.terminated = True
        self.refresh()
        self.bridge.tick()

    def test_no_process_before_explicit_stop_and_no_goal_during_startup(self):
        self.bridge.tick()
        self.assertFalse(self.generations)
        self.command('stop')
        self.refresh()
        self.bridge.tick()
        self.assertFalse(self.bridge.state()['navigationReady'])
        with self.assertRaisesRegex(BridgeError, 'admission_not_ready'):
            self.command('start')
        self.assertFalse(self.generations[0].start_calls)
        self.assertFalse(self.output.armed)

    def test_pause_resume_tears_down_old_producer_before_fresh_epoch(self):
        self.boot()
        self.command('start')
        self.bridge.tick()
        old = self.bridge.ticket
        self.assertEqual(self.generations[0].ticket.epoch, old.epoch)
        self.command('pause')
        self.refresh(.31)
        self.bridge.tick()
        self.assertEqual('stopping', self.bridge.phase)
        self.assertEqual(1, len(self.generations))
        self.assertFalse(self.output.armed)
        self.generations[0].terminated = True
        self.refresh()
        self.bridge.tick()
        self.assertEqual('paused', self.bridge.phase)
        self.assertEqual(2, len(self.generations))
        self.generations[-1].ready = True
        self.generations[-1].queue.append((old, 'accepted'))
        self.bridge.tick()
        self.assertEqual('paused', self.bridge.phase)
        self.command('resume')
        self.assertNotEqual(old.epoch, self.bridge.ticket.epoch)
        self.assertEqual('requested', self.bridge.phase)

    def test_retirement_keeps_operation_lease_until_output_proof_and_process_cleanup(self):
        self.boot()
        self.command('start')
        self.bridge.tick()
        generation = self.generations[0]
        generation.release_operation = Mock(wraps=generation.release_operation)
        self.command('pause')
        self.refresh(.31)
        self.bridge.tick()
        generation.release_operation.assert_not_called()
        generation.terminated = True
        with patch.object(self.output, 'complete_retirement', return_value=None):
            self.refresh()
            self.bridge.tick()
        generation.release_operation.assert_not_called()
        self.refresh()
        self.bridge.tick()
        generation.release_operation.assert_called_once()

    def test_failed_prearm_start_has_own_ticket_and_new_stop_receipt(self):
        self.boot()
        self.generations[0].start_error = RuntimeError('dds_reader_construction_failed')
        with self.assertRaisesRegex(BridgeError, 'start_unconfirmed'):
            self.command('start')
        self.assertEqual(self.bridge.ticket, self.output.ticket)
        self.assertFalse(self.output.armed)
        self.finish_retirement()
        self.assertEqual('failed', self.bridge.phase)
        self.assertTrue(self.bridge.requires_stop)
        self.assertEqual(1, len(self.generations))
        self.command('stop')
        self.refresh()
        self.bridge.tick()
        self.assertEqual('idle', self.bridge.phase)
        self.assertEqual(2, len(self.generations))

    def test_initial_receipt_not_relabelled_even_after_long_warmup(self):
        self.boot()
        proof = self.output.completed_retirement
        self.refresh(1.)
        self.command('start')
        self.assertIsNone(self.output.retirement(proof.nonce))
        self.assertIsNone(proof.ticket)
        self.assertEqual(self.bridge.ticket, self.output.ticket)

    def test_missing_startup_evidence_times_out_without_automatic_retry(self):
        self.command('stop')
        self.refresh()
        self.bridge.tick()
        self.refresh(2.01)
        self.bridge.tick()
        self.assertTrue(self.bridge.requires_stop)
        self.assertEqual('stopping', self.bridge.phase)
        self.finish_retirement()
        self.assertEqual('failed', self.bridge.phase)
        self.assertEqual(1, len(self.generations))

    def test_crash_during_action_revokes_output_before_cleanup(self):
        self.boot()
        self.command('start')
        self.bridge.tick()
        self.generations[0].crashed = True
        self.bridge.tick()
        self.assertFalse(self.output.armed)
        self.assertEqual(0., self.node.publisher.messages[-1].forward_velocity)
        self.assertTrue(self.bridge.requires_stop)
        self.finish_retirement()
        self.assertEqual('failed', self.bridge.phase)

    def test_map_activation_refuses_to_fake_loader_or_initial_pose(self):
        with self.assertRaisesRegex(RuntimeError, 'not_integrated'):
            self.backend.activate_map(self.identity.mapId, dict(x=0., y=0., yaw=0.))

    def install_localization(self):
        from test_localization_guard import BINDING, snapshot
        from lingxi_navigation.localization_guard import LocalizationMonitor, LocalizationGuard
        self.localization_value = snapshot(self.identity)
        monitor = LocalizationMonitor('/unused-test.sock', self.identity, BINDING,
            clock=self.clock, exchange=lambda: self.localization_value)
        self.addCleanup(monitor.close)
        monitor.refresh()
        self.backend.localization = LocalizationGuard(monitor)
        return monitor

    def test_saved_map_handoff_required_before_any_producer_preparation(self):
        self.install_localization()
        self.command('stop')
        self.refresh()
        self.bridge.tick()
        self.assertFalse(self.generations)
        self.backend.activate_map(self.identity.mapId, None)
        self.bridge.tick()
        self.assertEqual(1, len(self.generations))

    def test_changed_seed_revokes_running_epoch_without_automatic_recovery(self):
        monitor = self.install_localization()
        self.backend.activate_map(self.identity.mapId, None)
        self.boot()
        self.command('start')
        self.bridge.tick()
        self.localization_value['state']['generation'] += 1
        monitor.refresh()
        self.bridge.tick()
        self.assertFalse(self.output.armed)
        self.assertTrue(self.bridge.requires_stop)
        self.assertEqual('stopping', self.bridge.phase)
        self.finish_retirement()
        self.assertEqual('failed', self.bridge.phase)
        self.assertEqual(1, len(self.generations))

    def test_unsupported_map_or_planar_seed_does_not_fault_idle_owner(self):
        self.install_localization()
        self.command('stop')
        self.refresh()
        self.bridge.tick()
        for map_id, initial in (('slam-' + 'f' * 32, None), (self.identity.mapId, dict(x=0., y=0., yaw=0.))):
            self.number += 1
            request = dict(id=request_id(self.number), action='activate_map', session=self.bridge.session, mapId=map_id)
            if initial is not None:
                request['initialPose'] = initial
            with self.assertRaises(BridgeError):
                self.bridge.request(request)
            self.assertEqual('idle', self.bridge.phase)
            self.assertTrue(self.bridge.output_quiescent)
            self.assertFalse(self.bridge.requires_stop)
        self.assertFalse(self.generations)

    def test_reservation_requires_prior_stop_and_never_arms(self):
        ticket = Ticket('tour', 'e' * 32)
        with self.assertRaisesRegex(RuntimeError, 'reservation'):
            self.output.reserve(ticket, self.identity)
        self.boot()
        self.output.reserve(ticket, self.identity)
        self.assertFalse(self.output.armed)
        with self.assertRaisesRegex(RuntimeError, 'reservation'):
            self.output.reserve(Ticket('other', 'f' * 32), self.identity)

    def candidate(self, identity=None):
        from test_localization_guard import BINDING, snapshot
        from lingxi_navigation.localization_guard import LocalizationMonitor, LocalizationGuard
        identity = identity or evidence('b')
        monitor = LocalizationMonitor('/unused-test.sock', identity, BINDING,
            clock=self.clock, exchange=lambda: snapshot(identity))
        self.addCleanup(monitor.close)
        monitor.refresh()
        guard = LocalizationGuard(monitor)
        guard.activate_map(identity.mapId, None)
        def factory(ticket):
            generation = FakeGeneration(ticket, identity, self.clock, self.output)
            self.generations.append(generation)
            return generation
        return Nav2MapSelection(identity, guard, factory)

    def activation(self, identity):
        self.number += 1
        return self.bridge.request(dict(id=request_id(self.number), action='activate_map',
            session=self.bridge.session, mapId=identity.mapId))

    def test_switch_tears_down_warmup_and_requires_fresh_new_map_stop(self):
        self.install_localization()
        self.backend.activate_map(self.identity.mapId, None)
        self.boot()
        old_generation = self.generations[0]
        old_generation.close = Mock()
        old_proof = self.output.completed_retirement
        selection = self.candidate()
        self.backend.map_loader = Mock(return_value=selection)
        self.assertIsNone(self.activation(selection.identity))
        old_generation.close.assert_called_once()
        self.assertEqual(selection.identity, self.bridge.identity)
        self.assertEqual(selection.identity, self.output.identity)
        self.assertIsNone(self.output.ticket)
        self.assertIsNone(self.output.completed_retirement)
        self.assertIsNone(self.output.retirement(old_proof.nonce))
        self.assertTrue(self.bridge.requires_stop)
        self.assertEqual(dict(x=0., y=0., yaw=0.), self.bridge.last_pose)
        self.bridge.tick()
        self.assertEqual('stopping', self.bridge.phase)
        self.assertEqual(1, len(self.generations))
        self.refresh()
        self.bridge.tick()
        self.assertEqual('idle', self.bridge.phase)
        self.assertEqual('navigation_map_activated', self.bridge.detail)
        proof = self.output.completed_retirement
        self.assertEqual(selection.identity, proof.identity)
        self.assertNotEqual(old_proof.nonce, proof.nonce)
        self.assertIsNone(proof.ticket)
        self.assertEqual(2, len(self.generations))
        self.assertNotEqual(old_generation.ticket.epoch, self.generations[-1].ticket.epoch)
        self.assertEqual(selection.identity, self.generations[-1].identity)
        self.assertFalse(self.output.armed)
        self.assertFalse(self.bridge.state()['navigationReady'])
        old_generation.queue.append((Ticket('old', old_generation.ticket.epoch), 'accepted'))
        self.generations[-1].ready = True
        self.bridge.tick()
        self.assertTrue(self.bridge.state()['navigationReady'])
        self.assertIsNone(self.bridge.ticket)

    def test_candidate_preflight_rejection_leaves_idle_map_and_warmup_unchanged(self):
        self.install_localization()
        self.backend.activate_map(self.identity.mapId, None)
        self.boot()
        old = self.backend.generation
        old.close = Mock()
        self.backend.map_loader = Mock(side_effect=BridgeError('saved_map_localization_tracking_required', 503))
        with self.assertRaisesRegex(BridgeError, 'tracking_required'):
            self.activation(evidence('b'))
        self.assertIs(old, self.backend.generation)
        old.close.assert_not_called()
        self.assertEqual(self.identity, self.output.identity)
        self.assertEqual('idle', self.bridge.phase)
        self.assertFalse(self.bridge.requires_stop)

    def test_cleanup_failure_does_not_rebind_or_reuse_candidate(self):
        self.install_localization()
        self.backend.activate_map(self.identity.mapId, None)
        self.boot()
        self.backend.generation.close = Mock(side_effect=RuntimeError('synthetic_exit_failure'))
        selection = self.candidate()
        self.backend.map_loader = Mock(return_value=selection)
        with self.assertRaisesRegex(BridgeError, 'activation_unconfirmed'):
            self.activation(selection.identity)
        self.assertEqual(self.identity, self.output.identity)
        self.assertTrue(selection.localization.monitor.stopping.is_set())
        self.assertTrue(self.bridge.requires_stop)
        self.assertFalse(self.output.armed)

    def test_active_or_reserved_task_cannot_rebind_output(self):
        self.boot()
        selection = self.candidate()
        self.backend.localization = selection.localization
        self.backend.map_loader = Mock(return_value=selection)
        self.output.reserve(Ticket('reserved', 'd' * 32), self.identity)
        self.backend.reserved = self.output.ticket
        with self.assertRaisesRegex(BridgeError, 'quiescent_navigation'):
            self.backend.activate_map(selection.identity.mapId, None)
        self.backend.map_loader.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'output_map_handoff'):
            self.output.rebind_map(selection.identity)
        self.assertEqual(self.identity, self.output.identity)

    def test_new_map_stop_timeout_never_starts_its_producer(self):
        self.install_localization()
        self.command('stop')
        self.refresh()
        self.bridge.tick()
        selection = self.candidate()
        self.backend.map_loader = Mock(return_value=selection)
        self.activation(selection.identity)
        self.clock.advance(3.01)
        self.bridge.tick()
        self.assertEqual('failed', self.bridge.phase)
        self.assertTrue(self.bridge.requires_stop)
        self.assertFalse(self.bridge.output_quiescent)
        self.assertFalse(self.generations)

    def test_reconfirming_same_map_retires_old_task_ticket_before_next_stop(self):
        self.install_localization()
        self.command('stop')
        self.refresh()
        self.bridge.tick()
        old_ticket = Ticket('finished-task', 'e' * 32)
        self.output.reserve(old_ticket, self.identity)
        self.output.retire(old_ticket, 'd' * 32)
        self.refresh()
        self.output.complete_retirement('d' * 32)
        self.bridge.ticket = old_ticket
        selection = self.candidate(self.identity)
        self.backend.map_loader = Mock(return_value=selection)
        self.activation(self.identity)
        self.assertIsNone(self.output.ticket)
        self.assertIsNone(self.bridge.ticket)
        self.assertIsNone(self.output.completed_retirement)
        self.refresh()
        self.bridge.tick()
        self.assertEqual('idle', self.bridge.phase)
        self.assertIsNone(self.output.completed_retirement.ticket)
        self.command('stop')
        self.assertFalse(self.output.armed)
        self.assertEqual('stopping', self.bridge.phase)

    def test_old_map_and_changed_same_id_evidence_cannot_own_new_output(self):
        self.boot()
        selection = self.candidate()
        old_proof = self.output.completed_retirement
        self.output.rebind_map(selection.identity)
        self.assertFalse(self.output.ready(self.identity))
        self.assertFalse(self.output.arm(Ticket('late', 'f' * 32), self.identity))
        self.assertIsNone(self.output.retirement(old_proof.nonce))
        with self.assertRaisesRegex(RuntimeError, 'output_map_handoff'):
            self.output.rebind_map(replace(selection.identity, receiptSha256='e' * 64))

    def test_candidate_tracking_loss_after_warmup_exit_never_admits_it(self):
        self.install_localization()
        self.backend.activate_map(self.identity.mapId, None)
        self.boot()
        selection = self.candidate()
        self.backend.map_loader = Mock(return_value=selection)
        self.backend.generation.close = Mock(side_effect=lambda: self.clock.advance(.51))
        with self.assertRaisesRegex(BridgeError, 'activation_unconfirmed'):
            self.activation(selection.identity)
        self.assertEqual(self.identity, self.output.identity)
        self.assertTrue(self.bridge.requires_stop)
        self.assertFalse(self.output.armed)


if __name__ == '__main__':
    unittest.main()
