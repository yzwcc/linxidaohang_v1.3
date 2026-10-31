"""Execute installed Humble NavFn/RPP in a loopback-only synthetic planar world.

This is an actual ROS controller integration test, not an X2 physics simulator,
not a real-map acceptance, and never a physical transport. Requires ROS Humble.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.nav2_profile import OfflinePlatform
from lingxi_navigation.runtime import Goal, MapIdentity, OfflineNavigationRuntime, RuntimeInputs
from prepare_nav2_offline import prepare


def synthetic_platform():
    evidence = hashlib.sha256(b'lingxi-planar-synthetic-fixture-v1-not-measured-X2').hexdigest()
    return OfflinePlatform(.3, .2, .1, .3, .2, .4, .1, evidence)


def world(case):
    if case not in ('open', 'blocked', 'detour', 'appearing-wall'):
        raise ValueError('known_synthetic_scene_required')
    width, height, resolution = 160, 120, .05
    cells = [0] * (width * height)
    for y in range(height):
        for x in range(width):
            if x < 2 or y < 2 or x >= width - 2 or y >= height - 2:
                cells[y * width + x] = -1
    points = []
    if case in ('blocked', 'appearing-wall'):
        points = [(1., y * .05, z) for y in range(-60, 61) for z in (.15, .7, 1.3)]
    elif case == 'detour':
        points = [(x * .05, y * .05, z) for x in range(18, 25)
                  for y in range(-6, 7) for z in (.15, .7, 1.3)]
    return dict(width=width, height=height, resolution=resolution, origin=(-2., -3.),
                cells=cells, points=points)


def assess_trial(case, source, poses, commands, plans, state):
    """Independent swept-circle assertions, not Nav2's own arrival/collision verdict."""
    platform = synthetic_platform()
    radius = platform.clearance_radius_metres + platform.stopping_margin_metres
    obstacles = set((point[0], point[1]) for point in source['points'])
    minimum = math.inf
    for a, b in zip(poses, poses[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length_squared = dx * dx + dy * dy
        for x, y in obstacles:
            t = (max(0., min(1., ((x - a[0]) * dx + (y - a[1]) * dy) / length_squared))
                 if length_squared else 0.)
            minimum = min(minimum, math.hypot(x - a[0] - t * dx, y - a[1] - t * dy))
    resolution = source['resolution']
    ox, oy = source['origin']

    def supported(pose):
        # Cell AABB/circle intersection checks the full envelope, not just its
        # center. These scenes have no hidden inferred ground or physical data.
        for iy in range(math.floor((pose[1] - radius - oy) / resolution),
                        math.floor((pose[1] + radius - oy) / resolution) + 1):
            for ix in range(math.floor((pose[0] - radius - ox) / resolution),
                            math.floor((pose[0] + radius - ox) / resolution) + 1):
                cx, cy = ox + (ix + .5) * resolution, oy + (iy + .5) * resolution
                dx = max(0., abs(cx - pose[0]) - resolution / 2)
                dy = max(0., abs(cy - pose[1]) - resolution / 2)
                if math.hypot(dx, dy) > radius:
                    continue
                if (not 0 <= ix < source['width'] or not 0 <= iy < source['height']
                        or source['cells'][iy * source['width'] + ix] != 0):
                    return False
        return True

    final = poses[-1] if poses else (math.inf, math.inf, math.inf)
    nonzero = any(any(abs(value) > 1e-8 for value in command) for command in commands)
    bounded = all(all(math.isfinite(value) for value in command)
                  and -1e-12 <= command[0] <= platform.maximum_forward_metres_per_second + 1e-12
                  and abs(command[1]) <= platform.maximum_lateral_metres_per_second + 1e-12
                  and abs(command[2]) <= platform.maximum_yaw_radians_per_second + 1e-12
                  for command in commands)
    checks = dict(actionExpected=state['phase'] == ('failed' if case in ('blocked', 'appearing-wall') else 'finished'),
                  sourceRetired=state['outputQuiescent'] is True, noLatchedFault=state['requiresStop'] is False,
                  commandsBounded=bounded, sweptObstacleClearance=minimum >= radius,
                  fullEnvelopeSupported=bool(poses) and all(supported(pose) for pose in poses))
    if case == 'blocked':
        checks.update(noNonzeroCommands=not nonzero, stayedAtStart=all(
            math.hypot(p[0], p[1]) < 1e-9 and abs(p[2]) < 1e-9 for p in poses))
    elif case == 'appearing-wall':
        checks.update(controllerProducedMotion=nonzero, planned=any(plans),
                      obstacleIntroduced=any(p[0] >= .25 for p in poses),
                      stoppedBeforeWall=final[0] <= 1. - radius)
    else:
        checks.update(controllerProducedMotion=nonzero, planned=any(plans),
                      metricGoalReached=math.hypot(final[0] - 2., final[1]) <= .15,
                      headingReached=abs(final[2]) <= .15)
        if case == 'detour':
            checks['obstacleActuallyBypassed'] = max(abs(p[1]) for p in poses) >= .3 + radius
    return dict(checks=checks, passed=all(checks.values()),
                minimumSweptObstacleDistanceMetres=None if not obstacles else minimum,
                requiredEnvelopeRadiusMetres=radius)


class Observer:
    offline_only = True

    def __init__(self):
        self.values = []
        self.latest = (0., 0., 0.)

    def write(self, value):
        self.values.append(asdict(value))
        self.latest = value.forward, value.lateral, value.yaw


def signal_generation(process, value):
    try:
        os.killpg(process.pid, value)
    except ProcessLookupError:
        pass


def execute(output, case, maximum_seconds=75.):
    if (os.environ.get('ROS_LOCALHOST_ONLY') != '1'
            or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'
            or os.environ.get('ROS_DISTRO') != 'humble'):
        raise ValueError('explicit_local_only_humble_environment_required')
    # Require a network namespace with no physical/virtual external NIC, not
    # merely DDS domain separation. This runner never starts a robot bridge.
    if set(os.listdir('/sys/class/net')) != {'lo'}:
        raise ValueError('isolated_loopback_network_namespace_required')
    domain = int(os.environ['ROS_DOMAIN_ID'])
    runner_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest = prepare(synthetic_platform(), output, domain_id=domain)
    namespace = manifest['namespace']
    source = world(case)
    sha = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    identity = MapIdentity('slam-' + namespace[-32:], sha, sha, sha)
    import rclpy
    from rclpy.action import ActionClient
    from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
    from geometry_msgs.msg import PoseStamped, TransformStamped, Twist
    from nav_msgs.msg import OccupancyGrid, Odometry, Path as RosPath
    from nav2_msgs.action import NavigateToPose
    from rosgraph_msgs.msg import Clock
    from sensor_msgs.msg import PointCloud2, PointField
    from tf2_msgs.msg import TFMessage
    from builtin_interfaces.msg import Time
    from lifecycle_msgs.srv import GetState

    rclpy.init()
    node = rclpy.create_node('fixture', namespace=namespace)
    reliable = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                          durability=DurabilityPolicy.TRANSIENT_LOCAL)
    publishers = {
        'map': node.create_publisher(OccupancyGrid, 'traversable_map', reliable),
        'tf': node.create_publisher(TFMessage, 'tf', 20),
        'odom': node.create_publisher(Odometry, 'odom', 10),
        'cloud': node.create_publisher(PointCloud2, 'registered_obstacles', 10),
        'clock': node.create_publisher(Clock, '/clock', 10),
    }
    observer = Observer()
    launched_at = time.monotonic()
    pose = [0., 0., 0.]
    poses, commands, plans = [], [], []
    obstacle_activated = False
    packet_sequence = 0
    runtime = None
    action_client = ActionClient(node, NavigateToPose, 'navigate_to_pose')
    action_status = None

    class Backend:
        offline_only = True

        def __init__(self):
            self.ticket, self.handle, self.retiring = None, None, False
            self.process = None
            self.subscription = None

        def submit(self, ticket, goal):
            self.ticket = ticket
            msg = NavigateToPose.Goal()
            msg.pose = PoseStamped()
            msg.pose.header.frame_id = namespace + '_map'
            msg.pose.header.stamp = stamp()
            msg.pose.pose.position.x, msg.pose.pose.position.y = goal.x_metres, goal.y_metres
            msg.pose.pose.orientation.z = math.sin(goal.yaw_radians / 2)
            msg.pose.pose.orientation.w = math.cos(goal.yaw_radians / 2)
            pending = action_client.send_goal_async(msg)

            def accepted(future):
                self.handle = future.result()
                if self.retiring:
                    if self.handle.accepted:
                        self.handle.cancel_goal_async()
                    return
                if not self.handle.accepted:
                    runtime.terminal(ticket, 'failed')
                    return
                runtime.accepted(ticket)

                def finished(result):
                    nonlocal action_status
                    action_status = result.result().status
                    runtime.terminal(ticket, 'succeeded' if action_status == 4 else 'failed')

                self.handle.get_result_async().add_done_callback(finished)

            pending.add_done_callback(accepted)

        def retire(self, ticket):
            if ticket != self.ticket:
                raise ValueError('generation_mismatch')
            self.retiring = True
            node.destroy_subscription(self.subscription)
            signal_generation(self.process, signal.SIGINT)

        def quiescent(self, ticket):
            # Action cancel alone does not fence Twist. The entire generation
            # process group and its subscription must end before confirmation.
            if self.process.poll() is None:
                return False
            try:
                os.killpg(self.process.pid, 0)
                return False
            except ProcessLookupError:
                return self.retiring

    backend = Backend()
    runtime = OfflineNavigationRuntime(backend, observer, synthetic_platform())

    def stamp():
        seconds = time.monotonic() - launched_at + 1.
        return Time(sec=int(seconds), nanosec=int(seconds % 1 * 1e9))

    def receive_velocity(msg):
        nonlocal packet_sequence
        # The closure belongs to this process generation; never assign current
        # job identity at delivery from a shared vendor cmd_vel subscription.
        ticket = backend.ticket
        if ticket is None:
            return
        packet_sequence += 1
        commands.append([msg.linear.x, msg.linear.y, msg.angular.z])
        runtime.velocity(ticket, packet_sequence, time.monotonic(),
                         msg.linear.x, msg.linear.y, msg.angular.z)

    backend.subscription = node.create_subscription(Twist, 'observed_cmd_vel', receive_velocity, 1)
    plan_subscription = node.create_subscription(RosPath, 'plan', lambda msg:
        plans.append([[p.pose.position.x, p.pose.position.y] for p in msg.poses]), 10)
    lifecycle = [node.create_client(GetState, name + '/get_state') for name in
                 ('planner_server', 'controller_server', 'bt_navigator', 'velocity_smoother')]
    lifecycle_futures = [None] * 4
    lifecycle_states = [None] * 4
    sequence = 0

    def inputs():
        nonlocal sequence
        sequence += 1
        return RuntimeInputs(identity, sequence, time.monotonic(), True, True, True, True, True)

    def publish(dt, sensors):
        nonlocal obstacle_activated
        vx, vy, wz = observer.latest
        # Deliberately synthetic planar kinematics, not X2 dynamics evidence.
        pose[0] += (vx * math.cos(pose[2]) - vy * math.sin(pose[2])) * dt
        pose[1] += (vx * math.sin(pose[2]) + vy * math.cos(pose[2])) * dt
        pose[2] = math.atan2(math.sin(pose[2] + wz * dt), math.cos(pose[2] + wz * dt))
        if case != 'appearing-wall' or pose[0] >= .25:
            obstacle_activated = True
        now = stamp()
        publishers['clock'].publish(Clock(clock=now))
        transforms = []
        for parent, child, values in ((namespace + '_map', namespace + '_odom', (0., 0., 0.)),
                                      (namespace + '_odom', namespace + '_base', pose)):
            transform = TransformStamped()
            transform.header.stamp, transform.header.frame_id, transform.child_frame_id = now, parent, child
            transform.transform.translation.x, transform.transform.translation.y = values[0], values[1]
            transform.transform.rotation.z = math.sin(values[2] / 2)
            transform.transform.rotation.w = math.cos(values[2] / 2)
            transforms.append(transform)
        publishers['tf'].publish(TFMessage(transforms=transforms))
        odom = Odometry()
        odom.header.stamp, odom.header.frame_id, odom.child_frame_id = now, namespace + '_odom', namespace + '_base'
        odom.pose.pose.position.x, odom.pose.pose.position.y = pose[0], pose[1]
        odom.pose.pose.orientation = transforms[-1].transform.rotation
        odom.twist.twist.linear.x, odom.twist.twist.linear.y, odom.twist.twist.angular.z = vx, vy, wz
        publishers['odom'].publish(odom)
        poses.append(pose.copy())
        if not sensors:
            return
        grid = OccupancyGrid()
        grid.header.stamp, grid.header.frame_id = now, namespace + '_map'
        grid.info.width, grid.info.height, grid.info.resolution = source['width'], source['height'], source['resolution']
        grid.info.origin.position.x, grid.info.origin.position.y = source['origin']
        grid.info.origin.orientation.w = 1.
        grid.data = source['cells']
        publishers['map'].publish(grid)
        cloud = PointCloud2()
        cloud.header.stamp, cloud.header.frame_id = now, namespace + '_map'
        visible = source['points'] if obstacle_activated else []
        cloud.height, cloud.width = 1, len(visible)
        cloud.fields = [PointField(name=name, offset=i * 4, datatype=PointField.FLOAT32, count=1)
                        for i, name in enumerate(('x', 'y', 'z'))]
        cloud.point_step, cloud.row_step, cloud.is_dense = 12, 12 * cloud.width, True
        cloud.data = b''.join(struct.pack('<fff', *point) for point in visible)
        publishers['cloud'].publish(cloud)

    log = (output / 'nav2.log').open('x', encoding='utf-8')
    result = None
    try:
        backend.process = subprocess.Popen(['ros2', 'launch', str(output / 'nav2_offline.launch.py')],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        started, last, next_lifecycle, next_sensor = False, time.monotonic(), 0., 0.
        while time.monotonic() - launched_at < maximum_seconds:
            now = time.monotonic()
            publish(min(.1, now - last), now >= next_sensor)
            if now >= next_sensor:
                next_sensor = now + .05
            last = now
            if runtime.goal is not None:
                runtime.update_inputs(inputs())
            # One spin handles only one callback. Drain a bounded batch so clock,
            # lifecycle and action traffic cannot starve the velocity observer.
            for _ in range(40):
                rclpy.spin_once(node, timeout_sec=0.)
            if not started and now >= next_lifecycle:
                for i, client in enumerate(lifecycle):
                    pending = lifecycle_futures[i]
                    if pending is not None and pending.done():
                        lifecycle_states[i] = pending.result().current_state.id
                        lifecycle_futures[i] = None
                    if lifecycle_futures[i] is None and client.service_is_ready():
                        lifecycle_futures[i] = client.call_async(GetState.Request())
                next_lifecycle = now + .2
            if not started and lifecycle_states == [3] * 4 and action_client.server_is_ready():
                runtime.start('offline_' + case, Goal(identity, 2., 0., 0.), inputs())
                started = True
            state = runtime.poll()
            if started and state['outputQuiescent']:
                result = state
                break
            if backend.process.poll() is not None and not backend.retiring:
                raise RuntimeError('nav2_process_ended_before_retirement')
            time.sleep(max(0., .01 - (time.monotonic() - now)))
        if result is None:
            raise RuntimeError('bounded_nav2_offline_trial_timeout')
        assessment = assess_trial(case, source, poses, commands, plans, result)
        if (runner_sha != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                or any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha
                       for name, sha in manifest['sources'].items())):
            raise RuntimeError('navigation_sources_changed_during_trial')
        packages = subprocess.check_output(['dpkg-query', '-W', '-f=${Package} ${Version}\n',
            'ros-humble-nav2-controller', 'ros-humble-dwb-core', 'ros-humble-dwb-plugins',
            'ros-humble-dwb-critics', 'ros-humble-nav2-navfn-planner',
            'ros-humble-nav2-regulated-pure-pursuit-controller',
            'ros-humble-nav2-velocity-smoother'], text=True).splitlines()
        report = dict(schemaVersion=1, purpose='synthetic_planar_nav2_integration_not_field_acceptance',
            case=case, **assessment, installedPackages=packages, preparedSource=manifest['upstream'],
            preparationSources=manifest['sources'],
            runnerSha256=runner_sha,
            installedBinaryMatchesPinnedSourceVerified=False,
            lifecycleStates=lifecycle_states, actionStatus=action_status,
            commandCount=len(commands), observedCommands=commands, trajectory=poses,
            finalPose=pose, globalPlans=plans, obstacleActivated=obstacle_activated,
            maximumAbsY=max(abs(value[1]) for value in poses), runtimeState={**result, 'ticket': asdict(result['ticket'])},
            syntheticInputs=True, groundSupportMeasured=False, controllerExecuted=bool(commands),
            dynamicObstaclePolicy=manifest['dynamicObstaclePolicy'],
            observedTwistHasSourceTimestamp=False, productionCommandAgeProven=False,
            physicalMotionSent=False, navigationReady=False, elapsedSeconds=time.monotonic() - launched_at)
        (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        return report
    except Exception as error:
        failure = dict(schemaVersion=1, purpose='failed_synthetic_nav2_integration',
            case=case, passed=False, errorType=type(error).__name__, detail=str(error)[:512],
            physicalMotionSent=False, navigationReady=False, runnerSha256=runner_sha)
        (output / 'failure.json').write_text(json.dumps(failure, indent=2) + '\n', encoding='utf-8')
        raise
    finally:
        if backend.process is not None:
            # Also signal remaining children if ros2 launch itself already ended.
            signal_generation(backend.process, signal.SIGINT)
            try:
                backend.process.wait(timeout=4.)
            except subprocess.TimeoutExpired:
                signal_generation(backend.process, signal.SIGKILL)
                backend.process.wait(timeout=2.)
            signal_generation(backend.process, signal.SIGKILL)
        node.destroy_node()
        rclpy.shutdown()
        log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=('open', 'blocked', 'detour', 'appearing-wall'), required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        value = execute(args.output, args.case)
        print(json.dumps({key: value[key] for key in ('case', 'passed', 'commandCount', 'finalPose',
              'maximumAbsY', 'controllerExecuted', 'physicalMotionSent', 'navigationReady')}))
        sys.exit(0 if value['passed'] else 1)
    finally:
        os.umask(previous)
