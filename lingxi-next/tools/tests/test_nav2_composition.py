"""Launch contract tests; actual component execution is verified separately."""
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PLUGINS = {
    'planner_server': ('nav2_planner', 'nav2_planner::PlannerServer'),
    'controller_server': ('nav2_controller', 'nav2_controller::ControllerServer'),
    'bt_navigator': ('nav2_bt_navigator', 'nav2_bt_navigator::BtNavigator'),
    'velocity_smoother': ('nav2_velocity_smoother', 'nav2_velocity_smoother::VelocitySmoother'),
    'lifecycle_manager': ('nav2_lifecycle_manager', 'nav2_lifecycle_manager::LifecycleManager'),
}


class Description:
    def __init__(self, *args, **kwargs):
        self.args, self.options = args, kwargs


class Container(Description):
    pass


class CompositionTests(unittest.TestCase):
    def launch(self, *, physical=False):
        namespace = '/' + ('lingxi_nav_' if physical else 'lingxi_offline_') + 'a' * 32
        profile = dict(tf_topic='/owned/tf', tf_static_topic='/owned/tf_static')
        manifest = dict(namespace=namespace if physical else namespace[1:], profile=profile)
        path = ROOT / 'robot-navigation' / ('nav2_physical.launch.py' if physical else 'nav2_offline.launch.py')
        values = runpy.run_path(str(path))
        generate = values['generate_launch_description']
        generate.__globals__['verify_preparation'] = lambda *args: manifest
        modules = {
            'launch': NS(LaunchDescription=Description),
            'launch.actions': NS(DeclareLaunchArgument=Description),
            'launch.substitutions': NS(LaunchConfiguration=Description),
            'launch_ros.actions': NS(ComposableNodeContainer=Container),
            'launch_ros.descriptions': NS(ComposableNode=Description),
            'launch_ros.parameter_descriptions': NS(ParameterValue=Description),
        }
        with patch.dict(sys.modules, modules):
            actions, = generate().args
        container, = [value for value in actions if isinstance(value, Container)]
        return namespace, container.options, actions

    def verify_components(self, physical):
        namespace, container, _ = self.launch(physical=physical)
        self.assertEqual('rclcpp_components', container['package'])
        self.assertEqual('component_container_isolated', container['executable'])
        self.assertEqual(namespace, container['namespace'])
        self.assertEqual('nav2_container', container['name'])
        self.assertNotIn('respawn', container)
        nodes = {node.options['name']: node.options for node in container['composable_node_descriptions']}
        self.assertEqual(set(PLUGINS), set(nodes))
        for name, (package, plugin) in PLUGINS.items():
            self.assertEqual((package, plugin, namespace),
                (nodes[name]['package'], nodes[name]['plugin'], nodes[name]['namespace']))
        parameters = str(ROOT / 'robot-navigation/nav2-parameters.yaml')
        # Child costmap/BT nodes also need the global file, not only component parameters.
        self.assertEqual([parameters], container['parameters'])
        for name in set(nodes) - {'lifecycle_manager'}:
            self.assertEqual([parameters], nodes[name]['parameters'])
        manager, = nodes['lifecycle_manager']['parameters']
        self.assertEqual(list(PLUGINS)[:-1], manager['node_names'])
        self.assertEqual(not physical, manager['use_sim_time'])
        self.assertEqual(2., manager['bond_timeout'])
        if physical:
            self.assertIs(False, manager['autostart'])
        else:
            self.assertEqual({'value_type': bool}, manager['autostart'].options)
        return container, nodes

    def test_offline_official_components_preserve_namespace_parameters_and_clock(self):
        self.verify_components(False)

    def test_physical_official_components_preserve_ticket_namespace_and_manual_startup(self):
        self.verify_components(True)

    def test_physical_tf_topics_and_observed_output_do_not_escape_existing_handoff(self):
        container, nodes = self.verify_components(True)
        remaps = [('/tf', '/owned/tf'), ('/tf_static', '/owned/tf_static')]
        self.assertEqual(remaps, container['remappings'])
        for name in ('planner_server', 'controller_server', 'bt_navigator'):
            self.assertEqual(remaps + [('cmd_vel', 'raw_cmd_vel')], nodes[name]['remappings'])
        self.assertEqual([('cmd_vel', 'raw_cmd_vel'), ('cmd_vel_smoothed', 'observed_cmd_vel')],
            nodes['velocity_smoother']['remappings'])

    def test_offline_remappings_never_publish_robot_velocity(self):
        container, nodes = self.verify_components(False)
        self.assertEqual([('/tf', 'tf'), ('/tf_static', 'tf_static')], container['remappings'])
        for name in ('planner_server', 'controller_server', 'bt_navigator'):
            self.assertEqual(container['remappings'] + [('cmd_vel', 'raw_cmd_vel')], nodes[name]['remappings'])
        self.assertEqual([('cmd_vel', 'raw_cmd_vel'), ('cmd_vel_smoothed', 'observed_cmd_vel')],
            nodes['velocity_smoother']['remappings'])

    def test_offline_shutdown_escalation_fits_existing_retirement_deadline(self):
        _, container, _ = self.launch()
        self.assertEqual(('0.2', '0.2'), (container['sigterm_timeout'], container['sigkill_timeout']))


if __name__ == '__main__':
    unittest.main()
