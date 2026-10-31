"""Static composition validation, not ROS controller execution evidence."""
from dataclasses import asdict, replace
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
sys.path.insert(0, str(ROOT / 'tools'))
from lingxi_navigation.nav2_profile import (
    BEHAVIOR_TREE, THROUGH_POSES_BEHAVIOR_TREE, parameters, source_reference)
from test_navigation_runtime import platform
from prepare_nav2_offline import load_platform, prepare

spec = importlib.util.spec_from_file_location('offline_launch', ROOT / 'robot-navigation/nav2_offline.launch.py')
offline_launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline_launch)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.namespace = 'lingxi_offline_' + 'e' * 32
        self.config = parameters(platform(), ROOT / '.local/navigate.xml',
                                 ROOT / '.local/navigate-through.xml', self.namespace)

    def costmaps(self):
        return [self.config[name][name]['ros__parameters'] for name in ('global_costmap', 'local_costmap')]

    def test_pinned_source_never_claims_execution_or_navigation_acceptance(self):
        source = source_reference()
        lock = json.loads((ROOT / 'robot-navigation/navfn.lock.json').read_bytes())
        self.assertEqual(lock['commit'], source['commit'])
        self.assertFalse(source['controllerExecuted'])
        self.assertFalse(source['navigationReady'])

    def test_no_unknown_free_space_or_automatic_clearing(self):
        for config in self.costmaps():
            self.assertTrue(config['track_unknown_space'])
            obstacle = config['obstacle_layer']
            self.assertFalse(obstacle['footprint_clearing_enabled'])
            self.assertFalse(obstacle['lidar']['clearing'])
            self.assertTrue(obstacle['lidar']['marking'])
            self.assertEqual(1, obstacle['combination_method'])
            self.assertEqual((.1, 1.4), (obstacle['lidar']['min_obstacle_height'],
                                       obstacle['lidar']['max_obstacle_height']))
            self.assertEqual(0., obstacle['lidar']['observation_persistence'])
        self.assertFalse(self.config['planner_server']['ros__parameters']['GridBased']['allow_unknown'])

    def test_footprint_contains_entire_measured_circle_in_every_orientation(self):
        radius = platform().clearance_radius_metres + platform().stopping_margin_metres
        for config in self.costmaps():
            polygon = json.loads(config['footprint'])
            for a, b in zip(polygon, polygon[1:] + polygon[:1]):
                distance_to_edge = abs(a[0] * b[1] - a[1] * b[0]) / math.dist(a, b)
                self.assertGreaterEqual(distance_to_edge + 1e-12, radius)
            self.assertGreater(config['inflation_layer']['inflation_radius'], radius)

    def test_all_sensor_topics_and_tf_frames_are_epoch_owned(self):
        for config in self.costmaps():
            self.assertTrue(config['global_frame'].startswith(self.namespace + '_'))
            self.assertEqual(self.namespace + '_base', config['robot_base_frame'])
            self.assertTrue(config['static_layer']['map_topic'].startswith('/' + self.namespace + '/'))
            self.assertTrue(config['obstacle_layer']['lidar']['topic'].startswith('/' + self.namespace + '/'))
        self.assertEqual('/' + self.namespace + '/odom', self.config['controller_server']['ros__parameters']['odom_topic'])

    def test_rpp_has_collision_checking_and_bounded_closed_loop_smoothing(self):
        controller = self.config['controller_server']['ros__parameters']['FollowPath']
        self.assertEqual('nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController', controller['plugin'])
        self.assertTrue(controller['use_collision_detection'])
        self.assertTrue(controller['use_rotate_to_heading'])
        self.assertFalse(controller['allow_reversing'])
        p = platform()
        self.assertGreaterEqual(controller['max_allowed_time_to_collision_up_to_carrot'],
            p.maximum_forward_metres_per_second /
            p.acceleration_metres_per_second_squared + .3)
        smoother = self.config['velocity_smoother']['ros__parameters']
        self.assertEqual('CLOSED_LOOP', smoother['feedback'])
        self.assertEqual([.2, 0., .3], smoother['max_velocity'])
        self.assertEqual([0., 0., -.3], smoother['min_velocity'])
        self.assertEqual([.2, .2, .4], smoother['max_accel'])
        self.assertEqual([-.2, -.2, -.4], smoother['max_decel'])
        self.assertEqual(.15, smoother['velocity_timeout'])

    def test_no_spin_backup_clear_or_recovery_in_either_loaded_tree(self):
        allowed = {'root', 'BehaviorTree', 'PipelineSequence', 'RateController',
                   'ComputePathToPose', 'ComputePathThroughPoses', 'FollowPath'}
        for text in (BEHAVIOR_TREE, THROUGH_POSES_BEHAVIOR_TREE):
            tags = {node.tag for node in ET.fromstring(text).iter()}
            self.assertTrue(tags <= allowed)
            self.assertIn('FollowPath', tags)
        self.assertIn('ComputePathThroughPoses', THROUGH_POSES_BEHAVIOR_TREE)
        bt = self.config['bt_navigator']['ros__parameters']
        self.assertEqual(20, bt['default_server_timeout'])
        self.assertIn('default_nav_through_poses_bt_xml', bt)
        self.assertTrue(all(not any(word in name for word in ('spin', 'back_up', 'clear', 'recovery'))
                            for name in bt['plugin_lib_names']))

    def test_unmeasured_or_unbounded_profiles_are_not_silently_defaulted(self):
        for changes in ({'clearance_radius_metres': 0.}, {'maximum_forward_metres_per_second': True},
                        {'profile_evidence_sha256': ''}, {'acceleration_metres_per_second_squared': .02}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(platform(), **changes)

    def test_generation_namespace_and_both_trees_must_be_explicit(self):
        for ns in ('robot', '/lingxi_offline', 'lingxi_offline_old', '../cmd_vel'):
            with self.assertRaises(ValueError):
                parameters(platform(), ROOT / 'a', ROOT / 'b', ns)
        with self.assertRaises(ValueError):
            parameters(platform(), Path('relative'), ROOT / 'b', self.namespace)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.local').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='nav2-test-', dir=ROOT / '.local')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_preparation_is_private_hash_bound_and_does_not_execute_any_controller(self):
        output = self.root / 'prepared'
        manifest = prepare(platform(), output)
        self.assertFalse(manifest['controllerExecuted'])
        self.assertFalse(manifest['navigationReady'])
        self.assertFalse(manifest['physicalMotionSent'])
        self.assertEqual('1', manifest['environment']['ROS_LOCALHOST_ONLY'])
        self.assertEqual('173', manifest['environment']['ROS_DOMAIN_ID'])
        for name, digest in manifest['files'].items():
            self.assertEqual(digest, hashlib.sha256((output / name).read_bytes()).hexdigest())
        data = json.loads((output / 'nav2-parameters.yaml').read_bytes())
        config = data[manifest['namespace']]
        bt = config['bt_navigator']['ros__parameters']
        self.assertEqual(str(output / 'navigate.xml'), bt['default_nav_to_pose_bt_xml'])
        self.assertEqual(str(output / 'navigate-through.xml'), bt['default_nav_through_poses_bt_xml'])
        self.assertEqual('mark_only_latched_no_automatic_clearing', manifest['dynamicObstaclePolicy'])

    def test_prepared_generations_never_share_topics_or_frames(self):
        first = prepare(platform(), self.root / 'first')
        second = prepare(platform(), self.root / 'second')
        self.assertNotEqual(first['namespace'], second['namespace'])

    def test_launch_rechecks_hashes_and_does_not_trust_preparation_alone(self):
        output = self.root / 'prepared'
        manifest = prepare(platform(), output)
        self.assertEqual(manifest, offline_launch.verify_preparation(output, manifest['environment']))
        for name in manifest['files']:
            with self.subTest(name=name):
                path = output / name
                original = path.read_bytes()
                path.write_bytes(original + b'\n')
                with self.assertRaisesRegex(RuntimeError, 'identity_mismatch'):
                    offline_launch.verify_preparation(output, manifest['environment'])
                path.write_bytes(original)

    def test_launch_rejects_nonlocal_or_different_domain_and_middleware(self):
        output = self.root / 'prepared'
        manifest = prepare(platform(), output)
        for changes in ({'ROS_LOCALHOST_ONLY': '0'}, {'ROS_DOMAIN_ID': '174'},
                        {'RMW_IMPLEMENTATION': 'rmw_cyclonedds_cpp'}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                offline_launch.verify_preparation(output, {**manifest['environment'], **changes})

    def test_output_cannot_overwrite_sources_existing_preparation_or_parent(self):
        output = self.root / 'prepared'
        prepare(platform(), output)
        for path in (output, ROOT, ROOT / 'robot-navigation', ROOT.parent):
            with self.subTest(path=str(path)), self.assertRaises(ValueError):
                prepare(platform(), path)

    def test_input_profile_has_no_defaults_unknown_fields_or_duplicate_keys(self):
        path = self.root / 'profile.json'
        value = asdict(platform())
        path.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(platform(), load_platform(path))
        for invalid in ({}, {**value, 'robotRadius': .2}, {'profile': value}):
            path.write_text(json.dumps(invalid), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_platform(path)
        path.write_text('{"maximum_forward_metres_per_second":0.2,"maximum_forward_metres_per_second":0.5}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate_json_key'):
            load_platform(path)


if __name__ == '__main__':
    unittest.main()
