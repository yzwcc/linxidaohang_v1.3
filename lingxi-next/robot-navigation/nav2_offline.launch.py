"""Local-only Nav2 composition; use tools/prepare_nav2_offline.py, not robot bringup."""
import hashlib
import json
import os
from pathlib import Path
import re

def verify_preparation(root, environment):
    root = Path(root)
    manifest_path = root / 'offline-nav2-manifest.json'
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 32768:
        raise RuntimeError('bounded_direct_offline_manifest_required')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    namespace = manifest['namespace']
    if (environment.get('ROS_LOCALHOST_ONLY') != '1'
            or environment.get('ROS_DOMAIN_ID') != str(manifest['domainId'])
            or environment.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'
            or type(manifest['domainId']) is not int or not 1 <= manifest['domainId'] <= 200
            or re.fullmatch('lingxi_offline_[0-9a-f]{32}', namespace) is None
            or manifest['physicalMotionSent'] is not False
            or manifest['controllerExecuted'] is not False or manifest['navigationReady'] is not False
            or manifest['purpose'] != 'offline_nav2_preparation_not_acceptance'
            or manifest['dynamicObstaclePolicy'] != 'mark_only_latched_no_automatic_clearing'):
        raise RuntimeError('prepared_local_only_navigation_environment_required')
    names = {'navigate.xml', 'navigate-through.xml', 'nav2-parameters.yaml',
             'platform.json', 'nav2_offline.launch.py'}
    if set(manifest['files']) != names:
        raise RuntimeError('complete_offline_preparation_files_required')
    for name in names:
        path = root / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 65536
                or hashlib.sha256(path.read_bytes()).hexdigest() != manifest['files'][name]):
            raise RuntimeError('offline_preparation_file_identity_mismatch')
    return manifest


def generate_launch_description():
    from launch import LaunchDescription
    from launch.actions import GroupAction
    from launch_ros.actions import Node, PushRosNamespace
    root = Path(__file__).resolve().parent
    manifest = verify_preparation(root, os.environ)
    namespace = manifest['namespace']
    nodes = []
    for name, package in (('planner_server', 'nav2_planner'),
                          ('controller_server', 'nav2_controller'),
                          ('bt_navigator', 'nav2_bt_navigator')):
        nodes.append(Node(package=package, executable=name, name=name, output='log',
                          parameters=[str(root / 'nav2-parameters.yaml')],
                          remappings=[('/tf', 'tf'), ('/tf_static', 'tf_static'),
                                      ('cmd_vel', 'raw_cmd_vel')]))
    nodes.append(Node(package='nav2_velocity_smoother', executable='velocity_smoother',
                      name='velocity_smoother', output='log',
                      parameters=[str(root / 'nav2-parameters.yaml')],
                      remappings=[('cmd_vel', 'raw_cmd_vel'), ('cmd_vel_smoothed', 'observed_cmd_vel')]))
    nodes.append(Node(package='nav2_lifecycle_manager', executable='lifecycle_manager',
                      name='lifecycle_manager', output='log', parameters=[dict(
                          use_sim_time=True, autostart=True,
                          node_names=['planner_server', 'controller_server', 'bt_navigator', 'velocity_smoother'],
                          bond_timeout=2.)]))
    return LaunchDescription([GroupAction([PushRosNamespace(namespace), *nodes])])
