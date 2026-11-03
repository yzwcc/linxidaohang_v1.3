"""Production saved-map loader with actual profile and synthetic localization."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.localization_guard import LocalizationMonitor
from lingxi_navigation.physical_bridge import BridgeError, Ticket
from lingxi_navigation.physical_maps import load_map_selection
from lingxi_navigation.owned_inputs import descriptor
from lingxi_navigation.physical_ros import digest_footprint
from test_localization_guard import BINDING, snapshot
from test_physical_bridge import FakeClock, evidence


class MapLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.footprint = self.root / 'footprint.json'
        self.footprint.write_text(json.dumps([[-.2, -.2], [.2, -.2], [.2, .2], [-.2, .2]]), encoding='utf-8')
        self.identity = replace(evidence(), footprintSha256=digest_footprint(self.footprint))
        self.profile_file = self.root / 'profile.json'
        self.profile_file.write_text(json.dumps(dict(schema=1, footprintSha256=self.identity.footprintSha256,
            clearanceRadiusMetres=.3, stoppingMarginMetres=.1, forward=.2, lateral=0., angular=.3,
            acceleration=.3, deceleration=.4, angularAcceleration=.6, commandAge=.15, firstCommandTimeout=1.)),
            encoding='utf-8')
        self.activator = NS(evidence=Mock(return_value=self.identity), localization_binding=Mock(return_value=BINDING))
        self.options = dict(map_frame='lingxi_saved_map', base_frame='base_link', sensor_frame='lidar_chest_front',
            cloud_topic='/test/points', odometry_topic='/test/odom', terrain_topic='/test/terrain')
        self.output, self.executor, self.gate = Mock(), Mock(), Mock()
        self.clock, self.value = FakeClock(), snapshot(self.identity)
        self.monitors = []
        def monitor(endpoint, identity, binding, **kwargs):
            value = LocalizationMonitor(endpoint, identity, binding, clock=self.clock, exchange=lambda: self.value, **kwargs)
            value.start = Mock()
            self.monitors.append(value)
            self.addCleanup(value.close)
            return value
        self.patch = patch('lingxi_navigation.physical_maps.LocalizationMonitor', side_effect=monitor)
        self.monitor_factory = self.patch.start()
        self.addCleanup(self.patch.stop)

    def load(self, activate=True, input_mode='explicit'):
        return load_map_selection(self.activator, self.identity.mapId, footprint=self.footprint,
            profile_file=self.profile_file, profile_options=self.options, localization_endpoint='/unused.sock',
            run_root=self.root, executor=self.executor, output=self.output, operation_gate=self.gate,
            activate=activate, input_mode=input_mode)

    def owned(self, namespace='lingxi_offline_' + 'a' * 32):
        self.options = {}
        self.value['inputs'] = descriptor(self.value['state'], namespace)

    def test_owned_startup_validates_limits_without_topics_or_tracking_and_never_spawns(self):
        self.options = {}
        self.value = None
        with patch('lingxi_navigation.physical_maps.Nav2Generation') as factory:
            selection = self.load(activate=False, input_mode='owned-localization')
            self.assertTrue(self.monitors[-1].require_inputs)
            with self.assertRaisesRegex(RuntimeError, 'activated_owned'):
                selection.factory(Ticket('preparing', 'a' * 32))
            factory.assert_not_called()
        value = json.loads(self.profile_file.read_text(encoding='utf-8'))
        value['clearanceRadiusMetres'] = 0.
        self.profile_file.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.load(activate=False, input_mode='owned-localization')

    def test_owned_mode_never_uses_manual_topics_or_missing_descriptors(self):
        with self.assertRaisesRegex(ValueError, 'exclusive_navigation'):
            self.load(input_mode='owned-localization')
        self.options = {}
        with self.assertRaisesRegex(BridgeError, 'tracking_required'):
            self.load(input_mode='owned-localization')

    def test_owned_generation_binds_only_the_current_activated_namespace(self):
        self.owned()
        selection = self.load(input_mode='owned-localization')
        generation = NS(connect=Mock(), close=Mock())
        with patch('lingxi_navigation.physical_maps.Nav2Generation', return_value=generation) as factory:
            selection.factory(Ticket('preparing', 'a' * 32))
            profile = factory.call_args.args[0]
            self.assertEqual('/lingxi_offline_' + 'a' * 32 + '/points', profile.cloud_topic)
            self.assertEqual(self.identity, profile.limits.identity)
            self.owned('lingxi_offline_' + 'b' * 32)
            self.monitors[-1].refresh()
            with self.assertRaisesRegex(RuntimeError, 'activated_owned'):
                selection.factory(Ticket('preparing', 'b' * 32))
            self.assertEqual(1, factory.call_count)
            selection.localization.activate_map(self.identity.mapId, None)
            selection.factory(Ticket('preparing', 'c' * 32))
            self.assertEqual('/lingxi_offline_' + 'b' * 32 + '/points', factory.call_args.args[0].cloud_topic)

    def test_owned_replacement_during_construction_closes_without_connecting(self):
        self.owned()
        selection = self.load(input_mode='owned-localization')
        generation = NS(connect=Mock(), close=Mock())
        def changed(*args, **kwargs):
            self.value['inputs'] = None
            self.monitors[-1].refresh()
            return generation
        with patch('lingxi_navigation.physical_maps.Nav2Generation', side_effect=changed), \
                self.assertRaisesRegex(RuntimeError, 'inputs_changed'):
            selection.factory(Ticket('preparing', 'a' * 32))
        generation.connect.assert_not_called()
        generation.close.assert_called_once()

    def test_startup_starts_reader_without_pretending_tracking_or_activation(self):
        selection = self.load(activate=False)
        self.assertEqual(self.identity, selection.identity)
        self.assertIsNone(selection.localization.current())
        self.monitors[0].start.assert_called_once()

    def test_loader_requires_current_selected_tracking_before_starting_candidate_reader(self):
        self.value['runtime']['selectedMap']['mapId'] = evidence('b').mapId
        with self.assertRaisesRegex(BridgeError, 'tracking_required'):
            self.load()
        self.monitors[0].start.assert_not_called()
        self.assertTrue(self.monitors[0].stopping.is_set())

    def test_footprint_and_frames_fail_before_reader_creation(self):
        self.activator.evidence.return_value = replace(self.identity, footprintSha256='f' * 64)
        with self.assertRaisesRegex(RuntimeError, 'footprint_mismatch'):
            self.load()
        self.activator.evidence.return_value = self.identity
        self.options['map_frame'] = 'unbound_map'
        with self.assertRaisesRegex(RuntimeError, 'projection_frames'):
            self.load()
        self.monitor_factory.assert_not_called()

    def test_each_generation_uses_selected_map_profile_and_rechecks_files(self):
        selection = self.load()
        ticket = Ticket('preparing', 'a' * 32)
        generation = NS(connect=Mock(), close=Mock())
        with patch('lingxi_navigation.physical_maps.Nav2Generation', return_value=generation) as factory:
            self.assertIs(generation, selection.factory(ticket))
            args, kwargs = factory.call_args
            self.assertEqual(self.identity, args[0].limits.identity)
            self.assertEqual((ticket, self.root, self.footprint, self.executor), args[1:])
            self.assertIs(self.gate, kwargs['operation_gate'])
            self.assertEqual(selection.localization.validate_observation,
                generation.connect.call_args.kwargs['validate_observation'])
            self.activator.evidence.return_value = replace(self.identity, receiptSha256='f' * 64)
            with self.assertRaisesRegex(RuntimeError, 'evidence_changed'):
                selection.factory(ticket)
            self.assertEqual(1, factory.call_count)

    def test_failed_connect_retires_the_partial_generation(self):
        selection = self.load()
        generation = NS(connect=Mock(side_effect=RuntimeError('synthetic_connect_failure')), close=Mock())
        with patch('lingxi_navigation.physical_maps.Nav2Generation', return_value=generation), \
                self.assertRaisesRegex(RuntimeError, 'connect_failure'):
            selection.factory(Ticket('preparing', 'a' * 32))
        generation.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
