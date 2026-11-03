"""Session topology contracts only; synthetic declarations are not admission."""
import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.owned_inputs import OwnedRosInputs, descriptor, parse_descriptor
from test_localization_guard import snapshot

NAMESPACE = 'lingxi_offline_' + 'a' * 32


class OwnedInputTests(unittest.TestCase):
    def setUp(self):
        self.state = snapshot()['state']

    def test_fixed_topology_is_immutable_bound_and_never_promotes_readiness(self):
        value = descriptor(self.state, NAMESPACE)
        parsed = parse_descriptor(value, self.state)
        self.assertEqual(OwnedRosInputs(NAMESPACE), parsed)
        self.assertIsNot(value['binding'], self.state['binding'])
        self.assertEqual(dict(map_frame='lingxi_saved_map', base_frame='base_link', sensor_frame='lidar_chest_front',
            cloud_topic='/' + NAMESPACE + '/points', odometry_topic='/' + NAMESPACE + '/odometry',
            terrain_topic='/' + NAMESPACE + '/traversable_map', tf_topic='/' + NAMESPACE + '/tf',
            tf_static_topic='/' + NAMESPACE + '/tf_static'), parsed.profile_options())
        self.assertTrue(all(value[key] is False for key in
            ('physicalScaleValidated', 'groundSupportValidated', 'navigationReady')))
        with self.assertRaises(AttributeError):
            parsed.namespace = 'other'

    def test_only_exact_private_live_current_generation_schema_is_accepted(self):
        original = descriptor(self.state, NAMESPACE)
        bad = [dict(original, schema=True), dict(original, schema=2), dict(original, generation=True),
               dict(original, generation=2), dict(original, extra=1), dict(original, mode='explicit'),
               dict(original, binding=dict(original['binding'], session='f' * 32))]
        bad.extend(dict(original, **{key: True}) for key in
            ('physicalScaleValidated', 'groundSupportValidated', 'navigationReady'))
        bad.extend(dict(original, namespace=value) for value in
            ('/tf', '/' + NAMESPACE, 'lingxi_offline_' + 'A' * 32, NAMESPACE + '/cmd_vel', NAMESPACE + '\n', None))
        bad.append({key: value for key, value in original.items() if key != 'binding'})
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_descriptor(value, self.state)
        for change in (dict(generation=True), dict(generation=-1),
                       dict(binding=dict(self.state['binding'], input_mode='recorded'))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_descriptor(original, dict(self.state, **change))

    def test_entrypoint_selects_owned_mode_explicitly_without_manual_fallback(self):
        select = runpy.run_path(str(ROOT / 'robot-navigation/bridge/physical_nav_bridge.py'))['_profile_inputs']
        with patch.dict(os.environ, {'LINGXI_NAVIGATION_INPUTS': 'owned-localization'}, clear=True):
            self.assertEqual(('owned-localization', {}), select())
            for name in ('LINGXI_MAP_FRAME', 'LINGXI_BASE_FRAME', 'LINGXI_SENSOR_FRAME', 'LINGXI_POINTCLOUD_TOPIC',
                         'LINGXI_ODOMETRY_TOPIC', 'LINGXI_TERRAIN_TOPIC', 'LINGXI_TF_TOPIC', 'LINGXI_TF_STATIC_TOPIC'):
                with patch.dict(os.environ, {name: '/manual'}), self.assertRaisesRegex(RuntimeError, 'rejects_manual'):
                    select()
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, 'required'):
            select()
        with patch.dict(os.environ, {'LINGXI_NAVIGATION_INPUTS': 'automatic'}, clear=True), \
                self.assertRaisesRegex(RuntimeError, 'input_mode'):
            select()


if __name__ == '__main__':
    unittest.main()
