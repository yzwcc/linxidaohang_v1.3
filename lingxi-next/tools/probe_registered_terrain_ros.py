"""Registered synthetic acquisition -> actual native terrain -> isolated Nav2.

The registrar, ray geometry and stationary pose are synthetic. No robot IO.
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
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'robot-runtime/python'), str(ROOT / 'robot-navigation'), str(ROOT / 'robot-slam')]
import numpy as np
from registered_terrain import RegisteredTerrainSource
from lingxi_slam.acquisition_output import AcquisitionPacket
from lingxi_slam.localization_session import LocalizationBinding, LocalizationSession
from lingxi_slam.point_output import PointPacket
from lingxi_slam.seeded_localization import RegistrationEstimate
from lingxi_navigation.ground import GroundDeclaration
from lingxi_navigation.recorded_occupancy import OfflineGeometry
from lingxi_navigation.terrain import TerrainBudget
from lingxi_navigation.terrain_lane import TerrainLane
from lingxi_navigation.terrain_process import TerrainProcess
from lingxi_navigation.terrain_ros import TerrainRosPublisher
from prepare_nav2_offline import prepare, digest
from run_nav2_offline import synthetic_platform, signal_generation
from terrain_nav2_fixture import acquisition, projection, native_provenance
from verify_registered_terrain_ros import MODES, PURPOSE, SOURCES


def readonly(values):
    result = np.array(values, dtype=np.float64)
    result.flags.writeable = False
    return result


class SyntheticLocalization:
    """Real session and registration transform; explicitly synthetic registrar."""
    def __init__(self):
        class Registrar:
            def register(self, scan, seed):
                return RegistrationEstimate(True, (), seed, {})
        self.binding = LocalizationBinding('2' * 32, 'synthetic', 'slam-' + '1' * 32,
            '3' * 64, '4' * 64, '5' * 64)
        self.session = LocalizationSession(self.binding, Registrar, maximum_age_seconds=2.)
        self.session.open_worker()
        self.index, self.last_receipt, self.primed, self.disabled = 0, 0., False, False

    def point(self):
        self.index += 1
        self.last_receipt = time.monotonic()
        points = PointPacket(self.index, 1000. + .1 * self.index, 3, readonly(np.ones((3, 3))), readonly(np.eye(4)))
        self.session.observe(points, session=self.binding.session, input_mode='synthetic', received_at=self.last_receipt)
        return points

    def command(self, action):
        state = self.session.state()
        return self.session.request(action, asdict(self.binding), state['generation'], **(
            dict(seed=np.eye(4), reference_frame=state['referenceFrame']) if action == 'seed' else {}))

    def prime(self):
        self.point()
        self.command('seed')
        for _ in range(3):
            self.point()
        if self.session.state()['state'] != 'tracking':
            raise RuntimeError('synthetic_registration_did_not_track')
        self.primed = True

    def heartbeat(self):
        if self.primed and not self.disabled and time.monotonic() - self.last_receipt >= .05:
            self.point()

    def register(self, scan):
        points = self.point()
        raw = AcquisitionPacket(points.native_frame_id, points.algorithm_stamp, scan.source_sha256,
            readonly([ray.origin_metres for ray in scan.returns]),
            readonly([ray.endpoint_metres for ray in scan.returns]), readonly(np.eye(4)))
        result = self.session.register_acquisition(raw, points, received_at=self.last_receipt,
            expected_generation=self.session.state()['generation'])
        if result is None:
            raise RuntimeError('current_registered_synthetic_acquisition_required')
        return result


def execute(output, native, mode):
    if (set(os.listdir('/sys/class/net')) != {'lo'} or os.environ.get('ROS_LOCALHOST_ONLY') != '1'
            or os.environ.get('ROS_DISTRO') != 'humble' or mode not in MODES
            or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'):
        raise ValueError('isolated_explicit_registered_humble_trial_required')
    ground, octomap = native / 'ground/liblingxi_ground.so', native / 'octomap/liblingxi_octomap.so'
    provenance = native_provenance(ground, digest(ground), ground.parent / 'build-report.json',
        octomap, digest(octomap), octomap.parent / 'build-report.json')
    hashes = {name: digest(ROOT / name) for name in SOURCES}
    manifest = prepare(synthetic_platform(), output / 'nav2', domain_id=173, map_mode='support_fused_revisions')
    namespace, spec, data = manifest['namespace'], projection(), acquisition('open')
    budget = TerrainBudget(maximum_rays=131072, maximum_processing_seconds=10.,
        maximum_age_seconds=15., maximum_work_units=16000000)
    gate = threading.Event()
    def factory():
        if not gate.wait(35.):
            raise TimeoutError('registered_probe_startup_deadline')
        return TerrainProcess(ground_library=ground, ground_sha256=digest(ground),
            octomap_library=octomap, octomap_sha256=digest(octomap),
            dependencies=provenance['dependenciesSha256'], projection=spec,
            declaration=GroundDeclaration(.6, 0.), budget=budget, stderr_path=output / 'native.log')
    driver, lane = SyntheticLocalization(), TerrainLane(factory)
    source = RegisteredTerrainSource(driver.session, lane,
        OfflineGeometry(1., tuple(map(float, np.eye(4).ravel()))), budget=budget)
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
    node = rclpy.create_node('registered_terrain_probe', namespace=namespace,
        parameter_overrides=[Parameter('use_sim_time', value=True)])
    owner = TerrainRosPublisher(node, source, spec)
    qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
    observed = {name: [] for name in ('source', 'global', 'local', 'late', 'commands')}
    def record(name, value):
        observed[name].append((time.monotonic(), value))
    subscriptions = [node.create_subscription(OccupancyGrid, topic,
        lambda msg, name=name: record(name, msg), qos) for name, topic in
        (('source', 'traversable_map'), ('global', 'global_costmap/costmap'), ('local', 'local_costmap/costmap'))]
    subscriptions.append(node.create_subscription(Twist, 'observed_cmd_vel',
        lambda msg: record('commands', (msg.linear.x, msg.linear.y, msg.angular.z)), 10))
    clock_pub, tf_pub = node.create_publisher(Clock, '/clock', 10), node.create_publisher(TFMessage, 'tf', 20)
    odom_pub = node.create_publisher(Odometry, 'odom', 10)
    planner, controller = ActionClient(node, ComputePathToPose, 'compute_path_to_pose'), ActionClient(node, FollowPath, 'follow_path')
    lifecycle = [node.create_client(GetState, name + '/get_state') for name in
        ('planner_server', 'controller_server', 'bt_navigator', 'velocity_smoother')]
    states, pending = [None] * 4, [None] * 4
    paused, last, sim_time, process = False, time.monotonic(), 100., None
    log = (output / 'nav2.log').open('x')

    def tick():
        nonlocal last, sim_time
        now = time.monotonic()
        if not paused:
            sim_time += min(.1, now - last)
        last = now
        driver.heartbeat()
        stamp = Time(sec=int(sim_time), nanosec=int(sim_time % 1 * 1e9))
        clock_pub.publish(Clock(clock=stamp))
        transforms = []
        for parent, child in ((namespace + '_map', namespace + '_odom'), (namespace + '_odom', namespace + '_base')):
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
                raise TimeoutError('registered_ros_condition_timeout')
            tick()

    def future(value):
        until(value.done)
        return value.result()

    def cell(message, x, y):
        ix = math.floor((x - message.info.origin.position.x) / message.info.resolution)
        iy = math.floor((y - message.info.origin.position.y) / message.info.resolution)
        return int(message.data[iy * message.info.width + ix]) if 0 <= ix < message.info.width and 0 <= iy < message.info.height else None

    def blocked(name, since):
        return any(stamp >= since and all(cell(message,
            spec.origin_x_metres + (i % spec.width + .5) * spec.resolution_metres,
            spec.origin_y_metres + (i // spec.width + .5) * spec.resolution_metres) in (-1, 100)
            for i, cost in enumerate(accepted.costs) if cost == 0) for stamp, message in observed[name])

    def encode(message):
        return dict(width=message.info.width, height=message.info.height, resolution=message.info.resolution,
            origin=[message.info.origin.position.x, message.info.origin.position.y],
            cellsBase64=base64.b64encode(bytes(value & 255 for value in message.data)).decode())

    def compute():
        goal = ComputePathToPose.Goal()
        goal.goal.header.frame_id = namespace + '_map'
        goal.goal.pose.position.x, goal.goal.pose.orientation.w = 2., 1.
        handle = future(planner.send_goal_async(goal))
        if not handle.accepted:
            raise RuntimeError('registered_planner_not_accepted')
        return future(handle.get_result_async())

    try:
        process = subprocess.Popen(['ros2', 'launch', str(output / 'nav2/nav2_offline.launch.py')],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        def active():
            for i, client in enumerate(lifecycle):
                if pending[i] is not None and pending[i].done():
                    states[i], pending[i] = pending[i].result().current_state.id, None
                if states[i] != 3 and pending[i] is None and client.service_is_ready():
                    pending[i] = client.call_async(GetState.Request())
            return states == [3] * 4 and planner.server_is_ready() and controller.server_is_ready()
        until(active, 25.)
        driver.prime()
        receipts = []
        for scan in data.scans[:-1]:
            packet = driver.register(scan)
            receipts.append(packet.received_at)
            if source(packet):
                raise RuntimeError('registered_start_gate_bypassed')
        gate.set()
        until(lambda: lane.snapshot().state == 'ready', 11.)
        packet = driver.register(data.scans[-1])
        receipts.append(packet.received_at)
        publications, submitted = owner.publications, time.monotonic()
        if not source(packet):
            raise RuntimeError('registered_last_window_not_submitted')
        until(lambda: source.snapshot().grid is not None, 11.)
        accepted, completed = source.snapshot().grid, time.monotonic()
        pending_publications = owner.publications - publications
        input_value = source.submitted
        generation = driver.session.state()['generation']
        until(lambda: all(observed[name] and cell(observed[name][-1][1], 1., 0.) == 0 for name in ('global', 'local', 'source')))
        initial_maps = {name: encode(observed[name][-1][1]) for name in ('source', 'global', 'local')}
        initial = compute()
        if initial.status != 4 or not initial.result.path.poses:
            raise RuntimeError('registered_initial_path_missing')
        follow = FollowPath.Goal()
        follow.path, follow.controller_id, follow.goal_checker_id = initial.result.path, 'FollowPath', 'goal_checker'
        handle = future(controller.send_goal_async(follow))
        if not handle.accepted:
            raise RuntimeError('registered_controller_not_accepted')
        follow_result = handle.get_result_async()
        until(lambda: any(any(abs(v) > 1e-6 for v in cmd) for _, cmd in observed['commands']))
        triggered = time.monotonic()
        if mode == 'loss':
            driver.disabled = True
            effective_trigger = driver.last_receipt + driver.session.maximum_age
        else:
            driver.command('seed' if mode == 'reseed' else mode)
            effective_trigger = triggered
        paused, paused_stamp, count = True, node.get_clock().now().nanoseconds, owner.publications
        until(lambda: observed['source'] and observed['source'][-1][0] >= effective_trigger
            and set(observed['source'][-1][1].data) == {-1}, 5.)
        revoked = observed['source'][-1][0]
        until(lambda: owner.publications >= count + 4)
        steady = node.get_clock().now().nanoseconds == paused_stamp
        subscriptions.append(node.create_subscription(OccupancyGrid, 'traversable_map', lambda msg: record('late', msg), qos))
        until(lambda: bool(observed['late']))
        paused = False
        until(lambda: blocked('global', revoked) and blocked('local', revoked))
        consumed = time.monotonic()
        withdrawn = {name: encode(observed[name][-1][1]) for name in ('source', 'global', 'local', 'late')}
        after = compute()
        until(follow_result.done, 4.)
        until(lambda: time.monotonic() >= consumed + .5, 2.)
        commands = [list(cmd) for stamp, cmd in observed['commands'] if stamp >= consumed + .3]
        final_state = driver.session.state()
        checks = dict(nativeProcessProducedFree=accepted.costs.count(0) > 0,
            nativeWorkDidNotBlockPublication=pending_publications > 3, globalReceivedFree=True,
            localReceivedFree=True, initialPlannerSucceeded=True, controllerProducedNonzero=True,
            fullUnknownPublished=True, steadyTimerIndependentOfPausedClock=steady,
            lateSubscriberUnknown=set(observed['late'][0][1].data) == {-1},
            globalRevokedOldFree=blocked('global', revoked), localRevokedOldFree=blocked('local', revoked),
            plannerRejectedExpiredTerrain=after.status == 6 and not after.result.path.poses,
            controllerAborted=follow_result.result().status == 6,
            noNonzeroAfterConsumption=not any(any(abs(v) > 1e-6 for v in cmd) for cmd in commands))
        registered = dict(originalReceipts=receipts, acceptedReceipt=accepted.received_at,
            generation=generation, finalGeneration=final_state['generation'], finalState=final_state['state'],
            sourceRetired=source.retired, finalGridAbsent=source.snapshot().grid is None,
            sourceScans=len(data.scans), selectedScans=len(input_value.observations.scans),
            sourceReturns=sum(len(scan.returns) for scan in data.scans), selectedReturns=input_value.selected_returns,
            observationsSha256=input_value.observations.sha256, bindingSha256=input_value.observations.binding_sha256,
            epochSha256=accepted.epoch_sha256, sequence=accepted.sequence, units=input_value.observations.units,
            registrar='explicit_synthetic_identity', noAdditionalRaysAfterSubmission=True)
        if hashes != {name: digest(ROOT / name) for name in SOURCES}:
            raise RuntimeError('registered_ros_sources_changed')
        report = dict(schemaVersion=1, purpose=PURPOSE, mode=mode, passed=all(checks.values()), checks=checks,
            sourcesSha256=hashes, preparationSources=manifest['sources'], nativeBuild=provenance,
            groundLibrarySha256=digest(ground), octomapLibrarySha256=digest(octomap), registeredInput=registered,
            actualNativeGrid=dict(cells={str(c): accepted.costs.count(c) for c in (0, 254, 255)},
                observationsSha256=accepted.observations_sha256, costsSha256=hashlib.sha256(accepted.costs).hexdigest(),
                projection=asdict(spec), costsBase64=base64.b64encode(accepted.costs).decode(),
                ingressToResultSeconds=completed - accepted.received_at, ttlSeconds=accepted.expires_at - accepted.received_at),
            initialMaps=initial_maps, withdrawnMaps=withdrawn, nativePendingPublications=pending_publications,
            withdrawalSeconds=revoked - effective_trigger, controllerStatus=follow_result.result().status,
            expiredPlannerStatus=after.status, publications=owner.publications, rosDistro='humble', lifecycleStates=states,
            postConsumptionCommands=commands, actualCostmapReplacementVerified=True, syntheticInputs=True,
            physicalScaleValidated=False, groundSupportValidated=False, navigationReady=False,
            physicalMotionSent=False, robotContacted=False, physicalStopAcknowledged=False)
        from verify_registered_terrain_ros import verify
        verify(report)
        (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps(dict(mode=mode, passed=report['passed'], withdrawalSeconds=report['withdrawalSeconds'],
            finalState=final_state['state'], sourceRetired=source.retired), indent=2))
    except BaseException as error:
        (output / 'failure.json').write_text(json.dumps(dict(error=type(error).__name__, detail=str(error),
            observedCounts={name: len(value) for name, value in observed.items()}), indent=2) + '\n')
        raise
    finally:
        gate.set()
        owner.close()
        source.close()
        driver.session.close_worker()
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
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--mode', choices=MODES, required=True)
    args = parser.parse_args()
    execute(args.output, args.native, args.mode)
