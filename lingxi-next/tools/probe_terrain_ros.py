"""Live native terrain -> steady ROS timer -> actual isolated Humble consumers.

Synthetic ray geometry only. No pose integration, sensor or physical bridge.
"""
import argparse
import base64
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'robot-navigation'), str(ROOT / 'tools')]
from lingxi_navigation.ground import GroundDeclaration
from lingxi_navigation.terrain import TerrainBudget
from lingxi_navigation.terrain_lane import TerrainLane
from lingxi_navigation.terrain_process import TerrainProcess
from lingxi_navigation.terrain_ros import TerrainRosPublisher
from prepare_nav2_offline import prepare, digest
from run_nav2_offline import synthetic_platform, signal_generation
from terrain_nav2_fixture import acquisition, projection, native_provenance


def execute(output, mode):
    if (set(os.listdir('/sys/class/net')) != {'lo'} or os.environ.get('ROS_LOCALHOST_ONLY') != '1'
            or os.environ.get('ROS_DISTRO') != 'humble'
            or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'
            or mode not in ('expiry', 'death', 'revoke')):
        raise ValueError('isolated_explicit_humble_trial_required')
    native = output / '.cache/lingxi-next'
    ground, octomap = native / 'ground/liblingxi_ground.so', native / 'octomap/liblingxi_octomap.so'
    provenance = native_provenance(ground, digest(ground), ground.parent / 'build-report.json',
        octomap, digest(octomap), octomap.parent / 'build-report.json')
    sources = ('tools/probe_terrain_ros.py', 'tools/terrain-ros-isolated.sh',
        'robot-navigation/lingxi_navigation/terrain_lane.py',
        'robot-navigation/lingxi_navigation/terrain_ros.py',
        'robot-navigation/lingxi_navigation/terrain_feed.py',
        'robot-navigation/lingxi_navigation/terrain_process.py',
        'robot-navigation/lingxi_navigation/terrain_worker.py',
        'robot-navigation/lingxi_navigation/terrain_protocol.py',
        'robot-navigation/lingxi_navigation/terrain.py',
        'tools/terrain_nav2_fixture.py', 'tools/run_nav2_offline.py')
    source_hashes = {name: digest(ROOT / name) for name in sources}
    manifest = prepare(synthetic_platform(), output / 'nav2', domain_id=173, map_mode='support_fused_revisions')
    namespace, spec, data = manifest['namespace'], projection(), acquisition('open')
    budget = TerrainBudget(maximum_rays=131072, maximum_processing_seconds=10.,
        maximum_age_seconds=15., maximum_work_units=16000000)
    lane = TerrainLane(lambda: TerrainProcess(ground_library=ground, ground_sha256=digest(ground),
        octomap_library=octomap, octomap_sha256=digest(octomap),
        dependencies=provenance['dependenciesSha256'], projection=spec,
        declaration=GroundDeclaration(.6, 0.), budget=budget, stderr_path=output / 'native.log'))
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.parameter import Parameter
    from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
    from nav_msgs.msg import OccupancyGrid, Odometry
    from nav2_msgs.action import ComputePathToPose, FollowPath
    from geometry_msgs.msg import TransformStamped, Twist
    from tf2_msgs.msg import TFMessage
    from rosgraph_msgs.msg import Clock
    from builtin_interfaces.msg import Time
    from lifecycle_msgs.srv import GetState

    rclpy.init()
    node = rclpy.create_node('terrain_probe', namespace=namespace,
        parameter_overrides=[Parameter('use_sim_time', value=True)])
    owner = TerrainRosPublisher(node, lane, spec)
    qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                     reliability=ReliabilityPolicy.RELIABLE)
    observed = {'global': [], 'local': [], 'source': [], 'late': [], 'commands': []}
    def record(name, message):
        observed[name].append((time.monotonic(), message))
    subscriptions = [node.create_subscription(OccupancyGrid, topic,
        lambda msg, name=name: record(name, msg), qos) for name, topic in
        (('source', 'traversable_map'), ('global', 'global_costmap/costmap'), ('local', 'local_costmap/costmap'))]
    subscriptions.append(node.create_subscription(Twist, 'observed_cmd_vel',
        lambda msg: record('commands', (msg.linear.x, msg.linear.y, msg.angular.z)), 10))
    clock_pub = node.create_publisher(Clock, '/clock', 10)
    tf_pub = node.create_publisher(TFMessage, 'tf', 20)
    odom_pub = node.create_publisher(Odometry, 'odom', 10)
    planner = ActionClient(node, ComputePathToPose, 'compute_path_to_pose')
    controller = ActionClient(node, FollowPath, 'follow_path')
    lifecycle = [node.create_client(GetState, name + '/get_state') for name in
                 ('planner_server', 'controller_server', 'bt_navigator', 'velocity_smoother')]
    states, state_pending = [None] * 4, [None] * 4
    sim_time, last, paused = 1., time.monotonic(), False
    process, follow_result = None, None
    log = (output / 'nav2.log').open('x')

    def tick():
        nonlocal last, sim_time
        now = time.monotonic()
        if not paused:
            sim_time += min(.1, now - last)
        last = now
        stamp = Time(sec=int(sim_time), nanosec=int(sim_time % 1 * 1e9))
        clock_pub.publish(Clock(clock=stamp))
        transforms = []
        for parent, child in ((namespace + '_map', namespace + '_odom'),
                               (namespace + '_odom', namespace + '_base')):
            value = TransformStamped()
            value.header.stamp, value.header.frame_id, value.child_frame_id = stamp, parent, child
            value.transform.rotation.w = 1.
            transforms.append(value)
        tf_pub.publish(TFMessage(transforms=transforms))
        odom = Odometry()
        odom.header.stamp, odom.header.frame_id = stamp, namespace + '_odom'
        odom.child_frame_id, odom.pose.pose.orientation.w = namespace + '_base', 1.
        odom_pub.publish(odom)
        for _ in range(30):
            rclpy.spin_once(node, timeout_sec=0.)
        time.sleep(.005)

    def until(predicate, seconds=8.):
        end = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= end:
                raise TimeoutError('terrain_ros_probe_condition_timeout')
            tick()

    def future(value):
        until(value.done)
        return value.result()

    def cell(message, x, y):
        ix = math.floor((x - message.info.origin.position.x) / message.info.resolution)
        iy = math.floor((y - message.info.origin.position.y) / message.info.resolution)
        if not 0 <= ix < message.info.width or not 0 <= iy < message.info.height:
            return None
        return int(message.data[iy * message.info.width + ix])

    def free(name):
        return bool(observed[name]) and cell(observed[name][-1][1], 1., 0.) == 0

    def blocked_after(name, since):
        # Inspect all old supported cells, not just the goal. Inflation may
        # conservatively convert unknown into lethal (100).
        for stamp, message in observed[name]:
            if stamp < since:
                continue
            values = [cell(message, spec.origin_x_metres + (i % spec.width + .5) * spec.resolution_metres,
                spec.origin_y_metres + (i // spec.width + .5) * spec.resolution_metres)
                for i, cost in enumerate(accepted.costs) if cost == 0]
            if values and all(value in (-1, 100) for value in values):
                return True
        return False

    def encode_grid(message):
        return dict(width=message.info.width, height=message.info.height,
            resolution=message.info.resolution,
            origin=[message.info.origin.position.x, message.info.origin.position.y],
            cellsBase64=base64.b64encode(bytes(value & 255 for value in message.data)).decode())

    def compute():
        goal = ComputePathToPose.Goal()
        goal.goal.header.frame_id = namespace + '_map'
        goal.goal.pose.position.x, goal.goal.pose.orientation.w = 2., 1.
        handle = future(planner.send_goal_async(goal))
        if not handle.accepted:
            raise RuntimeError('planner_action_not_accepted')
        return future(handle.get_result_async())

    try:
        process = subprocess.Popen(['ros2', 'launch', str(output / 'nav2/nav2_offline.launch.py')],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        def active():
            for i, client in enumerate(lifecycle):
                pending = state_pending[i]
                if pending is not None and pending.done():
                    states[i], state_pending[i] = pending.result().current_state.id, None
                if states[i] != 3 and state_pending[i] is None and client.service_is_ready():
                    state_pending[i] = client.call_async(GetState.Request())
            return states == [3] * 4 and planner.server_is_ready() and controller.server_is_ready()
        until(active, 25.)
        until(lambda: lane.snapshot().state == 'ready')
        submitted, before_native_publications = time.monotonic(), owner.publications
        if not lane.submit(data, sequence=0, received_at=submitted):
            raise RuntimeError('native_lane_not_ready')
        until(lambda: lane.snapshot().grid is not None, 11.)
        accepted = lane.snapshot().grid
        completed = time.monotonic()
        native_pending_publications = owner.publications - before_native_publications
        until(lambda: free('global') and free('local') and free('source'))
        initial_maps = {name: encode_grid(observed[name][-1][1]) for name in ('source', 'global', 'local')}
        initial = compute()
        if initial.status != 4 or not initial.result.path.poses:
            raise RuntimeError('initial_actual_native_path_missing')
        follow = FollowPath.Goal()
        follow.path, follow.controller_id, follow.goal_checker_id = initial.result.path, 'FollowPath', 'goal_checker'
        handle = future(controller.send_goal_async(follow))
        if not handle.accepted:
            raise RuntimeError('controller_action_not_accepted')
        follow_result = handle.get_result_async()
        until(lambda: any(any(abs(v) > 1e-6 for v in command) for _, command in observed['commands']))
        triggered = time.monotonic()
        if mode == 'expiry':
            paused = True
        elif mode == 'death':
            os.kill(lane.snapshot().child_pid, signal.SIGKILL)
        else:
            lane.retire('explicit_map_retirement')
        count = owner.publications
        paused_stamp = node.get_clock().now().nanoseconds
        until(lambda: observed['source'] and observed['source'][-1][0] >= triggered
              and set(observed['source'][-1][1].data) == {-1}, 17.)
        revoked = observed['source'][-1][0]
        steady_while_paused = mode != 'expiry' or (
            owner.publications > count + 3 and node.get_clock().now().nanoseconds == paused_stamp)
        # A new DDS reader must receive UNKNOWN, not a retained free snapshot.
        subscriptions.append(node.create_subscription(OccupancyGrid, 'traversable_map',
            lambda msg: record('late', msg), qos))
        until(lambda: bool(observed['late']))
        late_unknown = set(observed['late'][0][1].data) == {-1}
        paused = False
        until(lambda: blocked_after('global', revoked) and blocked_after('local', revoked))
        consumed = time.monotonic()
        withdrawn_maps = {name: encode_grid(observed[name][-1][1]) for name in ('source', 'global', 'local', 'late')}
        after = compute()
        until(follow_result.done, 4.)
        # Leave a bounded drain for commands already queued before consumption.
        until(lambda: time.monotonic() >= consumed + .5, 2.)
        stale_commands = [cmd for stamp, cmd in observed['commands'] if stamp >= consumed + .3
                          and any(abs(v) > 1e-6 for v in cmd)]
        checks = dict(nativeProcessProducedFree=accepted.costs.count(0) > 0,
            nativeWorkDidNotBlockPublication=native_pending_publications > 3,
            globalReceivedFree=True, localReceivedFree=True, initialPlannerSucceeded=True,
            controllerProducedNonzero=True, fullUnknownPublished=True,
            steadyTimerIndependentOfPausedClock=steady_while_paused, lateSubscriberUnknown=late_unknown,
            globalRevokedOldFree=blocked_after('global', revoked), localRevokedOldFree=blocked_after('local', revoked),
            plannerRejectedExpiredTerrain=after.status != 4 and not after.result.path.poses,
            controllerAborted=follow_result.result().status == 6,
            noNonzeroAfterConsumption=not stale_commands)
        if source_hashes != {name: digest(ROOT / name) for name in sources}:
            raise RuntimeError('terrain_ros_sources_changed_during_trial')
        report = dict(schemaVersion=1, purpose='live_native_synthetic_terrain_ros_expiry_not_field_acceptance',
            mode=mode, passed=all(checks.values()), checks=checks, sourcesSha256=source_hashes,
            preparationSources=manifest['sources'], nativeBuild=provenance,
            groundLibrarySha256=digest(ground), octomapLibrarySha256=digest(octomap),
            actualNativeGrid=dict(cells={str(c): accepted.costs.count(c) for c in (0, 254, 255)},
                observationsSha256=accepted.observations_sha256, costsSha256=hashlib.sha256(accepted.costs).hexdigest(),
                projection=asdict(spec), costsBase64=base64.b64encode(accepted.costs).decode(),
                ingressToResultSeconds=completed - submitted,
                ttlSeconds=accepted.expires_at - accepted.received_at),
            initialMaps=initial_maps, withdrawnMaps=withdrawn_maps,
            nativePendingPublications=native_pending_publications,
            withdrawalSeconds=revoked - (accepted.expires_at if mode == 'expiry' else triggered),
            controllerStatus=follow_result.result().status, expiredPlannerStatus=after.status,
            publications=owner.publications, rosDistro='humble', lifecycleStates=states,
            postConsumptionCommands=[list(cmd) for stamp, cmd in observed['commands'] if stamp >= consumed + .3],
            actualCostmapReplacementVerified=True, syntheticInputs=True,
            physicalScaleValidated=False, groundSupportValidated=False, navigationReady=False,
            physicalMotionSent=False, robotContacted=False, physicalStopAcknowledged=False)
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({key: report[key] for key in ('mode', 'passed', 'checks', 'withdrawalSeconds',
            'nativePendingPublications', 'physicalMotionSent', 'navigationReady')}, indent=2))
        if not report['passed']:
            raise RuntimeError('terrain_ros_checks_failed')
        return report
    except BaseException as error:
        (output / 'failure.json').write_text(json.dumps(dict(error=type(error).__name__, detail=str(error),
            mode=mode, lane=asdict(lane.snapshot()) if lane.snapshot().grid is None else dict(state=lane.snapshot().state),
            observedCounts={name: len(values) for name, values in observed.items()}), indent=2) + '\n')
        raise
    finally:
        owner.close()
        lane.close()
        if process is not None:
            signal_generation(process, signal.SIGINT)
            try:
                process.wait(timeout=4.)
            except subprocess.TimeoutExpired:
                signal_generation(process, signal.SIGKILL)
                process.wait(timeout=2.)
            signal_generation(process, signal.SIGKILL)
        node.destroy_node()
        rclpy.shutdown()
        log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('expiry', 'death', 'revoke'), required=True)
    args = parser.parse_args()
    execute(args.output, args.mode)
