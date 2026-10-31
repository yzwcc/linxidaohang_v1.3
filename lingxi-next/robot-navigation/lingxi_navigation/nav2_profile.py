"""Measured-platform Nav2 composition for isolated offline integration only."""
from dataclasses import dataclass
import json
import math
import re

from .navfn import UPSTREAM_COMMIT, finite


BEHAVIOR_TREE = '''<root main_tree_to_execute="MainTree">
  <BehaviorTree ID="MainTree">
    <PipelineSequence name="ReplanAndFollow">
      <RateController hz="1.0">
        <ComputePathToPose goal="{goal}" path="{path}" planner_id="GridBased"/>
      </RateController>
      <FollowPath path="{path}" controller_id="FollowPath"/>
    </PipelineSequence>
  </BehaviorTree>
</root>
'''

THROUGH_POSES_BEHAVIOR_TREE = BEHAVIOR_TREE.replace(
    'ComputePathToPose goal="{goal}"', 'ComputePathThroughPoses goals="{goals}"')


@dataclass(frozen=True)
class OfflinePlatform:
    # A conservative circular envelope covers every orientation of the body.
    clearance_radius_metres: float
    maximum_forward_metres_per_second: float
    maximum_lateral_metres_per_second: float
    maximum_yaw_radians_per_second: float
    acceleration_metres_per_second_squared: float
    yaw_acceleration_radians_per_second_squared: float
    stopping_margin_metres: float
    profile_evidence_sha256: str

    def __post_init__(self):
        limits = ((self.clearance_radius_metres, .1, 1.5),
                  (self.maximum_forward_metres_per_second, .02, .5),
                  (self.maximum_lateral_metres_per_second, .0, .3),
                  (self.maximum_yaw_radians_per_second, .02, .8),
                  (self.acceleration_metres_per_second_squared, .02, .5),
                  (self.yaw_acceleration_radians_per_second_squared, .02, 1.),
                  (self.stopping_margin_metres, .05, 1.))
        if (any(not finite(value) or not low <= value <= high for value, low, high in limits)
                or type(self.profile_evidence_sha256) is not str
                or re.fullmatch('[0-9a-f]{64}', self.profile_evidence_sha256) is None):
            raise ValueError('bounded_explicit_platform_envelope_required')
        if max(math.hypot(self.maximum_forward_metres_per_second,
                          self.maximum_lateral_metres_per_second) /
               self.acceleration_metres_per_second_squared,
               self.maximum_yaw_radians_per_second /
               self.yaw_acceleration_radians_per_second_squared) > 2.7:
            raise ValueError('offline_prediction_budget_must_cover_configured_stopping_horizon')


def parameters(platform, behavior_tree_path, through_poses_tree_path, namespace):
    """JSON is emitted as YAML-compatible ROS parameters; no fake map is generated.

    Inputs are dedicated offline topics, never vendor topics. The map publisher
    must provide ray-based, metric, ground-support-filtered occupancy. An empty
    point cloud is not evidence that unseen space is free.
    """
    from pathlib import Path
    if (type(platform) is not OfflinePlatform
            or any(not Path(path).is_absolute() for path in (behavior_tree_path, through_poses_tree_path))
            or type(namespace) is not str or re.fullmatch('lingxi_offline_[0-9a-f]{32}', namespace) is None):
        raise ValueError('explicit_offline_platform_and_behavior_tree_required')
    topic = '/' + namespace + '/'
    frame = namespace + '_'
    radius = float(platform.clearance_radius_metres)
    margin = float(platform.stopping_margin_metres)
    vx = float(platform.maximum_forward_metres_per_second)
    wz = float(platform.maximum_yaw_radians_per_second)
    accel = float(platform.acceleration_metres_per_second_squared)
    yaw_accel = float(platform.yaw_acceleration_radians_per_second_squared)
    # Humble updateWithMax replaces master NO_INFORMATION with ray-cleared FREE.
    # Until a verified ground-support mask is integrated, obstacles latch; no
    # automatic ray/footprint clearing or recovery service may open unknown ground.
    source = dict(topic=topic + 'registered_obstacles', data_type='PointCloud2',
                  marking=True, clearing=False, min_obstacle_height=.1,
                  max_obstacle_height=1.4, observation_persistence=0.,
                  expected_update_rate=.2, obstacle_max_range=5., obstacle_min_range=0.,
                  raytrace_max_range=6., raytrace_min_range=0.)

    def costmap(frame, rolling):
        envelope = (radius + margin) / math.cos(math.pi / 16)
        footprint = [[envelope * math.cos(2 * math.pi * i / 16),
                      envelope * math.sin(2 * math.pi * i / 16)] for i in range(16)]
        value = dict(use_sim_time=True, global_frame=frame,
                     robot_base_frame=namespace + '_base', update_frequency=10.,
                     publish_frequency=5., resolution=.05, robot_radius=radius,
                     footprint=json.dumps(footprint), footprint_padding=0., transform_tolerance=.1,
                     track_unknown_space=True, rolling_window=rolling,
                     always_send_full_costmap=True,
                     plugins=['static_layer', 'obstacle_layer', 'inflation_layer'],
                     static_layer=dict(plugin='nav2_costmap_2d::StaticLayer',
                         map_topic=topic + 'traversable_map',
                         map_subscribe_transient_local=True, subscribe_to_updates=False),
                     obstacle_layer=dict(plugin='nav2_costmap_2d::ObstacleLayer',
                         observation_sources='lidar', combination_method=1,
                         min_obstacle_height=.1, max_obstacle_height=1.4,
                         footprint_clearing_enabled=False, lidar=source.copy()),
                     inflation_layer=dict(plugin='nav2_costmap_2d::InflationLayer',
                         inflation_radius=radius + margin + .3, cost_scaling_factor=3.))
        if rolling:
            value.update(width=8, height=8)
        return dict(ros__parameters=value)

    controller = dict(
        use_sim_time=True, controller_frequency=20., odom_topic=topic + 'odom',
        failure_tolerance=0., min_x_velocity_threshold=.001, min_y_velocity_threshold=.001,
        min_theta_velocity_threshold=.001, progress_checker_plugin='progress_checker',
        goal_checker_plugins=['goal_checker'], controller_plugins=['FollowPath'],
        progress_checker=dict(plugin='nav2_controller::SimpleProgressChecker',
                              required_movement_radius=.15, movement_time_allowance=15.),
        goal_checker=dict(plugin='nav2_controller::SimpleGoalChecker',
                          xy_goal_tolerance=.15, yaw_goal_tolerance=.15, stateful=True),
        FollowPath=dict(plugin='nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController',
            desired_linear_vel=vx, lookahead_dist=.4, min_lookahead_dist=.3, max_lookahead_dist=.6,
            lookahead_time=1.5, use_velocity_scaled_lookahead_dist=True,
            rotate_to_heading_angular_vel=wz, rotate_to_heading_min_angle=.4,
            use_rotate_to_heading=True, allow_reversing=False,
            max_angular_accel=yaw_accel, transform_tolerance=.1,
            min_approach_linear_velocity=.02, approach_velocity_scaling_dist=.6,
            use_collision_detection=True,
            max_allowed_time_to_collision_up_to_carrot=max(1.5, vx / accel + .3),
            use_regulated_linear_velocity_scaling=True,
            use_cost_regulated_linear_velocity_scaling=True,
            cost_scaling_dist=radius + margin + .3, cost_scaling_gain=1.,
            inflation_cost_scaling_factor=3.,
            regulated_linear_scaling_min_radius=max(.9, vx / wz),
            regulated_linear_scaling_min_speed=.02))
    return dict(
        planner_server=dict(ros__parameters=dict(use_sim_time=True,
            expected_planner_frequency=1., planner_plugins=['GridBased'],
            GridBased=dict(plugin='nav2_navfn_planner/NavfnPlanner', tolerance=.0,
                           use_astar=False, allow_unknown=False))),
        controller_server=dict(ros__parameters=controller),
        velocity_smoother=dict(ros__parameters=dict(use_sim_time=True,
            smoothing_frequency=20., scale_velocities=True, feedback='CLOSED_LOOP',
            max_velocity=[vx, 0., wz], min_velocity=[0., 0., -wz],
            max_accel=[accel, accel, yaw_accel], max_decel=[-accel, -accel, -yaw_accel],
            odom_topic=topic + 'odom', odom_duration=.05,
            deadband_velocity=[0., 0., 0.], velocity_timeout=.15)),
        local_costmap=dict(local_costmap=costmap(frame + 'odom', True)),
        global_costmap=dict(global_costmap=costmap(frame + 'map', False)),
        bt_navigator_navigate_to_pose_rclcpp_node=dict(ros__parameters=dict(use_sim_time=True)),
        bt_navigator_navigate_through_poses_rclcpp_node=dict(ros__parameters=dict(use_sim_time=True)),
        bt_navigator=dict(ros__parameters=dict(use_sim_time=True,
            global_frame=frame + 'map', robot_base_frame=frame + 'base',
            odom_topic=topic + 'odom', bt_loop_duration=10, default_server_timeout=20,
            wait_for_service_timeout=1000,
            default_nav_to_pose_bt_xml=str(behavior_tree_path),
            default_nav_through_poses_bt_xml=str(through_poses_tree_path),
            plugin_lib_names=['nav2_compute_path_to_pose_action_bt_node',
                              'nav2_compute_path_through_poses_action_bt_node',
                              'nav2_follow_path_action_bt_node',
                              'nav2_rate_controller_bt_node', 'nav2_pipeline_sequence_bt_node'])))


def source_reference():
    return {'repository': 'https://github.com/ros-navigation/navigation2',
            'commit': UPSTREAM_COMMIT,
            'controller': 'nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController',
            'smoother': 'nav2_velocity_smoother::VelocitySmoother', 'offlineOnly': True,
            'controllerExecuted': False, 'navigationReady': False}
