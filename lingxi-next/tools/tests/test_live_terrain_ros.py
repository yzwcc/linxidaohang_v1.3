"""Owned live integration lifecycle with ROS doubles, not field acceptance."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-runtime/python'), str(ROOT / 'robot-navigation')]
from live_terrain_ros import LiveTerrainRosOutput, setup_localization_ros
from lingxi_navigation.occupancy import ClearanceProjection
from lingxi_navigation.terrain_lane import LaneSnapshot


class LiveTerrainOutputTests(unittest.TestCase):
    def setUp(self):
        self.projection = ClearanceProjection(8, 8, .1, 0., 0.)
        self.state = LaneSnapshot(0, 'starting')
        self.retire = Mock(side_effect=self.revoke)
        self.output = LiveTerrainRosOutput(self.projection, lambda: self.state, self.retire)
        self.node = SimpleNamespace(destroy_node=Mock(return_value=True))
        self.executor = SimpleNamespace(add_node=Mock(return_value=True), remove_node=Mock())
        self.sensor = SimpleNamespace(context=object(), executor=self.executor)
        self.ros = SimpleNamespace(create_node=Mock(return_value=self.node))
        self.enterContext(patch.dict(sys.modules, {'rclpy': self.ros}))
        case = self
        class Publisher:
            def __init__(self, node, lane, projection):
                self.node, self.lane, self.projection = node, lane, projection
                self.publications = 1
            def close(self):
                self.lane.retire()
                case.assertIsNone(self.lane.snapshot().grid)
                self.publications += 1
        self.publisher = self.enterContext(patch('live_terrain_ros.TerrainRosPublisher', side_effect=Publisher))

    def revoke(self):
        self.state = replace(self.state, state='retired', grid=None)

    def test_no_node_or_native_work_until_setup_and_private_namespace_is_fresh(self):
        self.ros.create_node.assert_not_called()
        self.assertRegex(self.output.namespace, r'^lingxi_offline_[0-9a-f]{32}$')
        other = LiveTerrainRosOutput(self.projection, lambda: self.state, self.retire)
        self.assertNotEqual(other.namespace, self.output.namespace)
        self.assertEqual('starting', self.output.snapshot().state)

    def test_attaches_to_existing_context_executor_and_confirms_idempotent_owned_teardown(self):
        close = self.output.setup(self.sensor)
        self.ros.create_node.assert_called_once_with('declared_terrain', namespace=self.output.namespace,
            context=self.sensor.context, use_global_arguments=False, enable_rosout=False, start_parameter_services=False)
        self.executor.add_node.assert_called_once_with(self.node)
        close(); close()
        self.retire.assert_called_once()
        self.executor.remove_node.assert_called_once_with(self.node)
        self.node.destroy_node.assert_called_once()
        self.assertEqual(dict(mode='private_declared_terrain', started=True, closed=True, failed=False,
            publications=2, physicalScaleValidated=False, groundSupportValidated=False,
            physicalMotionSent=False, navigationReady=False), self.output.summary())
        with self.assertRaisesRegex(RuntimeError, 'cannot_restart'):
            self.output.setup(self.sensor)

    def test_close_before_native_source_exists_retires_admission_and_cannot_restart(self):
        self.output.close()
        self.assertTrue(self.output.closed)
        self.assertFalse(self.output.started)
        self.ros.create_node.assert_not_called()
        self.retire.assert_called_once()
        self.assertIsNone(self.output.snapshot().grid)

    def test_retired_output_cannot_reveal_a_late_source_result(self):
        self.output.retire()
        self.state = LaneSnapshot(2, 'ready', session='a' * 32, grid=object())
        self.assertIsNone(self.output.snapshot().grid)
        self.assertEqual('retired', self.output.snapshot().state)

    def test_invalid_replacement_reason_latches_failure_and_withdraws_source(self):
        self.output.retire('invalid_ros_terrain_replacement')
        self.retire.assert_called_once()
        self.assertTrue(self.output.failed)
        self.assertIsNone(self.output.snapshot().grid)

    def test_retirement_failure_hides_late_results_before_cleanup(self):
        self.output.setup(self.sensor)
        self.state = LaneSnapshot(2, 'ready', session='a' * 32, grid=object())
        self.retire.side_effect = RuntimeError('synthetic_retirement_failure')
        with self.assertRaisesRegex(RuntimeError, 'synthetic_retirement_failure'):
            self.output.retire()
        self.assertIsNone(self.output.snapshot().grid)
        self.output.close()
        self.executor.remove_node.assert_called_once_with(self.node)
        self.node.destroy_node.assert_called_once()
        self.assertTrue(self.output.summary()['failed'])

    def test_wrong_thread_cleanup_does_not_touch_ros_or_source(self):
        self.output.setup(self.sensor)
        failures = []
        def wrong_owner():
            try:
                self.output.close()
            except RuntimeError as error:
                failures.append(str(error))
        thread = threading.Thread(target=wrong_owner)
        thread.start(); thread.join(1.)
        self.assertEqual(['same_sensor_executor_terrain_cleanup_required'], failures)
        self.retire.assert_not_called()
        self.output.close()

    def test_every_cleanup_is_attempted_after_publisher_failure(self):
        self.output.setup(self.sensor)
        self.output.publisher.close = Mock(side_effect=RuntimeError('synthetic_close_failure'))
        with self.assertRaisesRegex(RuntimeError, 'cleanup_failed'):
            self.output.close()
        self.retire.assert_called_once()
        self.executor.remove_node.assert_called_once()
        self.node.destroy_node.assert_called_once()
        self.assertTrue(self.output.summary()['failed'])

    def test_missing_executor_node_creation_and_attach_failures_revoke_without_leaks(self):
        for stage in ('executor', 'create', 'publisher', 'attach'):
            with self.subTest(stage=stage):
                output = LiveTerrainRosOutput(self.projection, lambda: self.state, Mock(side_effect=self.revoke))
                sensor = SimpleNamespace(context=object(), executor=None if stage == 'executor' else self.executor)
                with patch.object(self.ros, 'create_node', side_effect=RuntimeError('create_failed') if stage == 'create' else None,
                                  return_value=self.node), patch('live_terrain_ros.TerrainRosPublisher',
                        side_effect=RuntimeError('publisher_failed') if stage == 'publisher' else self.publisher.side_effect), \
                        patch.object(self.executor, 'add_node', return_value=stage != 'attach'):
                    with self.assertRaises((ValueError, RuntimeError)):
                        output.setup(sensor)
                self.assertTrue(output.failed)
                self.assertTrue(output.closed)
                self.assertEqual('retired', output.snapshot().state)


class CombinedSetupTests(unittest.TestCase):
    def test_base_reader_and_terrain_owner_setup_once_and_close_in_reverse_order(self):
        calls = []
        def owner(name):
            return SimpleNamespace(setup=lambda node: (calls.append(name), lambda: calls.append(name + '_closed'))[1])
        close = setup_localization_ros(object(), owner('base'), owner('terrain'), owner('observations'))
        close(); close()
        self.assertEqual(['base', 'terrain', 'observations', 'observations_closed', 'terrain_closed', 'base_closed'], calls)

    def test_failed_observation_setup_closes_terrain_and_base(self):
        base, terrain = (Mock(setup=Mock(return_value=Mock())) for _ in range(2))
        observation = Mock(setup=Mock(side_effect=RuntimeError('observation_setup_failed')))
        with self.assertRaisesRegex(RuntimeError, 'observation_setup_failed'):
            setup_localization_ros(object(), base, terrain, observation)
        terrain.setup.return_value.assert_called_once()
        base.setup.return_value.assert_called_once()

    def test_failed_terrain_setup_still_closes_base_reader(self):
        base = Mock(setup=Mock(return_value=Mock()))
        terrain = Mock(setup=Mock(side_effect=RuntimeError('terrain_setup_failed')))
        with self.assertRaisesRegex(RuntimeError, 'terrain_setup_failed'):
            setup_localization_ros(object(), base, terrain)
        base.setup.return_value.assert_called_once()

    def test_all_cleanup_runs_even_when_first_cleanup_fails(self):
        base = Mock(setup=Mock(return_value=Mock()))
        terrain = Mock(setup=Mock(return_value=Mock(side_effect=RuntimeError('close_failed'))))
        close = setup_localization_ros(object(), base, terrain)
        with self.assertRaisesRegex(RuntimeError, 'localization_ros_cleanup_failed'):
            close()
        base.setup.return_value.assert_called_once()


if __name__ == '__main__':
    unittest.main()
