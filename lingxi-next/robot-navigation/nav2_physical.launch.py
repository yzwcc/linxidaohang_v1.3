"""Launch only the prepared ticket's Nav2 nodes. Never launch vendor services."""
import hashlib
import json
from pathlib import Path
import re


def verify_preparation(root):
    root = Path(root)
    path = root / 'producer.json'
    if path.is_symlink() or path.stat().st_size > 32768:
        raise RuntimeError('bounded_nav2_producer_manifest_required')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    names = {'navigate.xml', 'navigate-through.xml', 'nav2-parameters.yaml', 'nav2_physical.launch.py'}
    if (manifest.get('protocol') != 1 or manifest.get('purpose') != 'owned_physical_nav2_producer_not_robot_acceptance'
            or type(manifest.get('namespace')) is not str
            or re.fullmatch('/lingxi_nav_[a-f0-9]{32}', manifest['namespace']) is None
            or manifest['namespace'][-32:] != root.name or set(manifest.get('files', {})) != names
            or manifest.get('identity') != manifest.get('profile', {}).get('limits', {}).get('identity')):
        raise RuntimeError('ticket_bound_nav2_producer_manifest_required')
    for name in names:
        artifact = root / name
        if (artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size > 65536
                or hashlib.sha256(artifact.read_bytes()).hexdigest() != manifest['files'][name]):
            raise RuntimeError('nav2_producer_file_identity_mismatch')
    return manifest


def generate_launch_description():
    from launch import LaunchDescription
    from launch.actions import GroupAction
    from launch_ros.actions import Node, PushRosNamespace
    root = Path(__file__).resolve().parent
    manifest = verify_preparation(root)
    profile = manifest['profile']
    remaps = [('/tf', profile['tf_topic']), ('/tf_static', profile['tf_static_topic'])]
    nodes = []
    for name, package in (('planner_server', 'nav2_planner'), ('controller_server', 'nav2_controller'),
                          ('bt_navigator', 'nav2_bt_navigator')):
        nodes.append(Node(package=package, executable=name, name=name, output='log',
            parameters=[str(root / 'nav2-parameters.yaml')], remappings=remaps + [('cmd_vel', 'raw_cmd_vel')]))
    nodes.append(Node(package='nav2_velocity_smoother', executable='velocity_smoother',
        name='velocity_smoother', output='log', parameters=[str(root / 'nav2-parameters.yaml')],
        remappings=[('cmd_vel', 'raw_cmd_vel'), ('cmd_vel_smoothed', 'observed_cmd_vel')]))
    nodes.append(Node(package='nav2_lifecycle_manager', executable='lifecycle_manager',
        name='lifecycle_manager', output='log', parameters=[dict(use_sim_time=False, autostart=False,
        node_names=['planner_server', 'controller_server', 'bt_navigator', 'velocity_smoother'], bond_timeout=2.)]))
    return LaunchDescription([GroupAction([PushRosNamespace(manifest['namespace']), *nodes])])
