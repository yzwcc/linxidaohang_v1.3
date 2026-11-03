"""Bounded stdio product backend using real isolated Humble Nav2, never robot IO."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import select
import signal
import subprocess
import sys
import time

from run_nav2_offline import world, synthetic_platform, signal_generation, Observer
from prepare_nav2_offline import prepare, strict_object
from lingxi_navigation.runtime import Goal, MapIdentity, OfflineNavigationRuntime, RuntimeInputs
from lingxi_navigation.lifecycle_startup import LifecycleStartup
from humble_velocity_source import HumbleTwistSource, SourceTimeError


class RequestError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class Generation:
    def __init__(self, owner, ticket, goal):
        import rclpy
        from rclpy.action import ActionClient
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from nav_msgs.msg import OccupancyGrid, Odometry
        from nav2_msgs.action import NavigateToPose
        from sensor_msgs.msg import PointCloud2
        from tf2_msgs.msg import TFMessage
        from rosgraph_msgs.msg import Clock
        from lifecycle_msgs.srv import GetState
        from nav2_msgs.srv import ManageLifecycleNodes
        self.owner, self.ticket, self.goal = owner, ticket, goal
        self.retiring, self.sent, self.handle = False, False, None
        self.sequence = 0
        output = owner.output / ticket.epoch
        manifest = prepare(synthetic_platform(), output, domain_id=int(os.environ['ROS_DOMAIN_ID']))
        self.namespace = manifest['namespace']
        self.node = rclpy.create_node('product_fixture', namespace=self.namespace)
        reliable = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.publishers = {
            'map': self.node.create_publisher(OccupancyGrid, 'traversable_map', reliable),
            'odom': self.node.create_publisher(Odometry, 'odom', 10),
            'tf': self.node.create_publisher(TFMessage, 'tf', 20),
            'cloud': self.node.create_publisher(PointCloud2, 'registered_obstacles', 10),
            'clock': self.node.create_publisher(Clock, '/clock', 10),
        }
        self.velocity_source = HumbleTwistSource(self.namespace,
            maximum_age_ns=int(owner.runtime.command_timeout * 1e9))
        self.action = ActionClient(self.node, NavigateToPose, 'navigate_to_pose')
        clients = [self.node.create_client(GetState, name + '/get_state') for name in LifecycleStartup.names]
        manager = self.node.create_client(ManageLifecycleNodes, 'lifecycle_manager/manage_nodes')
        self.lifecycle = LifecycleStartup(clients, manager, GetState.Request,
            lambda: ManageLifecycleNodes.Request(command=ManageLifecycleNodes.Request.STARTUP))
        self.next_sensor = 0.
        self.started_at = time.monotonic()
        self.diagnostic = output / 'startup.json'
        self.log = (output / 'nav2.log').open('x', encoding='utf-8')
        try:
            self.process = subprocess.Popen(['ros2', 'launch', str(output / 'nav2_offline.launch.py'),
                'autostart:=false'],
                stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        except BaseException:
            self.velocity_source.close()
            self.node.destroy_node()
            self.log.close()
            raise

    def stamp(self):
        from builtin_interfaces.msg import Time
        seconds = time.monotonic() - self.owner.origin
        return Time(sec=int(seconds), nanosec=int((seconds % 1) * 1e9))

    def velocity(self, message, generated):
        self.sequence += 1
        accepted = self.owner.runtime.velocity(self.ticket, self.sequence, generated,
            message.linear.x, message.linear.y, message.angular.z)
        if not accepted and self.owner.runtime.detail == 'controller_command_rejected':
            diagnostic = self.owner.output / self.ticket.epoch / 'controller-rejection.json'
            if not diagnostic.exists():
                diagnostic.write_text(json.dumps(dict(sequence=self.sequence,
                    forward=message.linear.x, lateral=message.linear.y, yaw=message.angular.z,
                    generated=generated, checked=time.monotonic()), allow_nan=True), encoding='utf-8')

    def send(self):
        from geometry_msgs.msg import PoseStamped
        from nav2_msgs.action import NavigateToPose
        self.sent = True
        message = NavigateToPose.Goal()
        message.pose = PoseStamped()
        message.pose.header.frame_id, message.pose.header.stamp = self.namespace + '_map', self.stamp()
        message.pose.pose.position.x = float(self.goal.x_metres)
        message.pose.pose.position.y = float(self.goal.y_metres)
        message.pose.pose.orientation.z = math.sin(self.goal.yaw_radians / 2)
        message.pose.pose.orientation.w = math.cos(self.goal.yaw_radians / 2)

        def accepted(future):
            self.handle = future.result()
            if self.retiring:
                if self.handle.accepted:
                    self.handle.cancel_goal_async()
                return
            if not self.handle.accepted:
                self.owner.runtime.terminal(self.ticket, 'failed')
                return
            self.owner.runtime.accepted(self.ticket)

            def finished(result):
                succeeded = result.result().status == 4
                pose = self.owner.pose
                # Independently check arrival before product tasks may narrate.
                arrived = (math.hypot(pose[0] - self.goal.x_metres, pose[1] - self.goal.y_metres) <= .16
                           and abs(math.atan2(math.sin(pose[2] - self.goal.yaw_radians),
                               math.cos(pose[2] - self.goal.yaw_radians))) <= .16)
                self.owner.runtime.terminal(self.ticket, 'succeeded' if succeeded and arrived else 'failed')

            self.handle.get_result_async().add_done_callback(finished)

        self.action.send_goal_async(message).add_done_callback(accepted)

    def publish(self, sensors):
        import struct
        from geometry_msgs.msg import TransformStamped
        from nav_msgs.msg import OccupancyGrid, Odometry
        from sensor_msgs.msg import PointCloud2, PointField
        from tf2_msgs.msg import TFMessage
        from rosgraph_msgs.msg import Clock
        pose, source, now = self.owner.pose, self.owner.source, self.stamp()
        self.publishers['clock'].publish(Clock(clock=now))
        transforms = []
        for parent, child, values in ((self.namespace + '_map', self.namespace + '_odom', (0., 0., 0.)),
                                     (self.namespace + '_odom', self.namespace + '_base', pose)):
            transform = TransformStamped()
            transform.header.stamp, transform.header.frame_id, transform.child_frame_id = now, parent, child
            transform.transform.translation.x, transform.transform.translation.y = values[0], values[1]
            transform.transform.rotation.z = math.sin(values[2] / 2)
            transform.transform.rotation.w = math.cos(values[2] / 2)
            transforms.append(transform)
        self.publishers['tf'].publish(TFMessage(transforms=transforms))
        odom = Odometry()
        odom.header.stamp, odom.header.frame_id, odom.child_frame_id = now, self.namespace + '_odom', self.namespace + '_base'
        odom.pose.pose.position.x, odom.pose.pose.position.y = pose[0], pose[1]
        odom.pose.pose.orientation = transforms[-1].transform.rotation
        odom.twist.twist.linear.x, odom.twist.twist.linear.y, odom.twist.twist.angular.z = self.owner.observer.latest
        self.publishers['odom'].publish(odom)
        if not sensors:
            return
        grid = OccupancyGrid()
        grid.header.stamp, grid.header.frame_id = now, self.namespace + '_map'
        grid.info.width, grid.info.height, grid.info.resolution = source['width'], source['height'], source['resolution']
        grid.info.origin.position.x, grid.info.origin.position.y = source['origin']
        grid.info.origin.orientation.w, grid.data = 1., source['cells']
        self.publishers['map'].publish(grid)
        cloud = PointCloud2()
        cloud.header.stamp, cloud.header.frame_id = now, self.namespace + '_map'
        visible = self.owner.visible_points()
        cloud.height, cloud.width = 1, len(visible)
        cloud.fields = [PointField(name=name, offset=i * 4, datatype=PointField.FLOAT32, count=1)
                        for i, name in enumerate(('x', 'y', 'z'))]
        cloud.point_step, cloud.row_step, cloud.is_dense = 12, 12 * cloud.width, True
        cloud.data = b''.join(struct.pack('<fff', *point) for point in visible)
        self.publishers['cloud'].publish(cloud)

    def tick(self):
        import rclpy
        if self.retiring:
            return
        now = time.monotonic()
        self.publish(now >= self.next_sensor)
        if now >= self.next_sensor:
            self.next_sensor = now + .05
        try:
            sample = self.velocity_source.take()
        except SourceTimeError:
            self.owner.runtime.reject_velocity_source(self.ticket)
            return
        if sample is not None:
            self.velocity(*sample)
            if self.retiring:
                return
        for _ in range(40):
            rclpy.spin_once(self.node, timeout_sec=0.)
            if self.retiring:
                return
        if not self.sent:
            self.lifecycle.poll(now)
        if self.lifecycle.failure is not None:
            self.owner.runtime.terminal(self.ticket, 'failed')
            return
        if not self.sent and self.lifecycle.ready(now) and self.action.server_is_ready():
            self.send()
        if self.process.poll() is not None and not self.retiring:
            self.owner.runtime.terminal(self.ticket, 'failed')

    def retire(self):
        if self.retiring:
            return
        self.retiring = True
        self.lifecycle.close()
        self.velocity_source.close()
        self.node.destroy_node()
        signal_generation(self.process, signal.SIGINT)
        with self.diagnostic.open('x', encoding='utf-8') as stream:
            json.dump(dict(purpose='offline_lifecycle_diagnostic_not_field_acceptance',
                elapsedSeconds=time.monotonic() - self.started_at,
                startupSent=self.lifecycle.sent, startupConfirmed=self.lifecycle.confirmed,
                states=self.lifecycle.states, observationRounds=self.lifecycle.rounds,
                failure=self.lifecycle.failure, goalSent=self.sent,
                runtimeDetail=self.owner.runtime.detail, startupTimeoutSeconds=self.owner.runtime.startup_timeout,
                acceptanceSeconds=(None if self.owner.runtime.accepted_at is None else
                    self.owner.runtime.accepted_at - self.owner.runtime.requested_at),
                physicalMotionSent=False, navigationReady=False), stream, allow_nan=False)

    def quiescent(self):
        if self.process.poll() is None:
            return False
        try:
            os.killpg(self.process.pid, 0)
            return False
        except ProcessLookupError:
            self.log.close()
            return self.retiring

    def close(self):
        self.retire()
        signal_generation(self.process, signal.SIGKILL)
        self.process.wait(timeout=2.)
        self.log.close()


class Service:
    offline_only = True

    def __init__(self, case, output):
        self.case, self.output, self.session = case, output, secrets.token_hex(16)
        self.source, self.pose = world(case), [0., 0., 0.]
        sha = hashlib.sha256(json.dumps(self.source, sort_keys=True).encode()).hexdigest()
        self.identity = MapIdentity('slam-' + sha[:32], sha, sha, sha)
        self.origin, self.last = time.monotonic(), time.monotonic()
        self.observer, self.generation = Observer(), None
        self.observer.values = deque(maxlen=512)
        self.factory = ThreadPoolExecutor(max_workers=1, thread_name_prefix='nav2-generation')
        self.building, self.retiring_ticket = None, None
        self.sequence, self.inputs_sequence, self.point_id = 0, 0, None
        self.command_count, self.obstacle_activated = 0, case != 'appearing-wall'
        self.runtime = OfflineNavigationRuntime(self, self, synthetic_platform())

    def write(self, observation):
        self.observer.write(observation)
        if observation.reason == 'controller_output':
            self.command_count += 1

    def submit(self, ticket, goal):
        if self.building is not None or (self.generation is not None and not self.generation.quiescent()):
            raise RuntimeError('previous_generation_not_retired')
        self.generation, self.retiring_ticket = None, None
        # ROS entity construction must not block the single watchdog/callback owner.
        self.building = self.factory.submit(Generation, self, ticket, goal)

    def retire(self, ticket):
        if self.building is not None:
            self.retiring_ticket = ticket
            return
        if self.generation is None or self.generation.ticket != ticket:
            raise RuntimeError('generation_retirement_mismatch')
        self.generation.retire()

    def quiescent(self, ticket):
        return self.generation is not None and self.generation.ticket == ticket and self.generation.quiescent()

    def inputs(self):
        self.inputs_sequence += 1
        return RuntimeInputs(self.identity, self.inputs_sequence, time.monotonic(), True, True, True, True, True)

    def visible_points(self):
        return self.source['points'] if self.obstacle_activated else []

    def tick(self):
        # Expire controller/input watchdogs before integrating or delivering callbacks.
        self.runtime.poll()
        now, (vx, vy, wz) = time.monotonic(), self.observer.latest
        dt, self.last = min(.05, max(0., now - self.last)), now
        self.pose[0] += (vx * math.cos(self.pose[2]) - vy * math.sin(self.pose[2])) * dt
        self.pose[1] += (vx * math.sin(self.pose[2]) + vy * math.cos(self.pose[2])) * dt
        self.pose[2] = math.atan2(math.sin(self.pose[2] + wz * dt), math.cos(self.pose[2] + wz * dt))
        if self.pose[0] >= .25:
            self.obstacle_activated = True
        if self.runtime.goal is not None:
            self.runtime.update_inputs(self.inputs())
        if self.building is not None and self.building.done():
            future, self.building = self.building, None
            try:
                self.generation = future.result()
            except Exception:
                self.runtime.terminal(self.runtime.ticket, 'failed')
                raise
            if self.retiring_ticket is not None:
                self.generation.retire()
        if self.generation is not None:
            self.generation.tick()
        self.runtime.poll()

    def state(self):
        self.sequence += 1
        state = self.runtime.state()
        return dict(protocol=1, session=self.session, sequence=self.sequence, identity=asdict(self.identity),
            **{**state, 'ticket': asdict(state['ticket']) if state['ticket'] else None}, pointId=self.point_id,
            pose=dict(zip(('x', 'y', 'yaw'), self.pose)), commandCount=self.command_count, syntheticInputs=True)

    def request(self, request):
        if type(request) is not dict or type(request.get('id')) is not str or not re.fullmatch('[0-9a-f]{32}', request['id']):
            raise RequestError('bounded_request_required', 400)
        action = request.get('action')
        if action in ('state', 'scene'):
            if set(request) != {'id', 'action'}:
                raise RequestError('unexpected_fields', 400)
            if action == 'state':
                return self.state()
            source = self.source
            return dict(protocol=1, session=self.session, identity=asdict(self.identity), case=self.case,
                grid=dict(width=source['width'], height=source['height'], resolution=source['resolution'],
                          originX=source['origin'][0], originY=source['origin'][1], cells=source['cells']),
                points=[value for point in self.visible_points() for value in point], offlineOnly=True,
                syntheticInputs=True, physicalMotionSent=False, navigationReady=False)
        fields = {'id', 'action', 'session', 'identity', 'expectedEpoch', 'taskId'}
        if action == 'start':
            fields |= {'pointId', 'goal'}
        if action not in ('start', 'pause', 'resume', 'stop') or set(request) != fields:
            raise RequestError('explicit_command_required', 400)
        ticket = self.runtime.ticket
        if (request['session'] != self.session or request['identity'] != asdict(self.identity)
                or request['expectedEpoch'] != (ticket.epoch if ticket else None)):
            raise RequestError('stale_navigation_identity')
        task = request['taskId']
        if type(task) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,100}', task):
            raise RequestError('bounded_task_required', 400)
        if action != 'start' and (ticket is None or task != ticket.task_id):
            raise RequestError('task_mismatch')
        try:
            if action == 'start':
                point, goal = request['pointId'], request['goal']
                if (type(point) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,100}', point)
                        or type(goal) is not dict or set(goal) != {'x', 'y', 'yaw'}):
                    raise RequestError('bounded_goal_required', 400)
                value = Goal(self.identity, goal['x'], goal['y'], goal['yaw'])
                self.runtime.start(task, value, self.inputs())
                self.point_id = point
            elif action == 'pause':
                self.runtime.pause()
            elif action == 'resume':
                self.runtime.resume(self.inputs())
            else:
                self.runtime.stop()
        except (ValueError, RuntimeError) as error:
            raise RequestError('navigation_request_refused') from error
        return None if action in ('pause', 'stop') else self.state()

    def close(self):
        self.factory.shutdown(wait=True)
        if self.building is not None:
            try:
                self.generation = self.building.result()
            except Exception:
                pass
        if self.generation:
            self.generation.close()


def serve(service, reader=sys.stdin.buffer, writer=sys.stdout):
    buffer, pending = b'', None
    while True:
        service.tick()
        if pending is not None:
            identifier, deadline = pending
            state = service.state()
            if state['outputQuiescent'] or time.monotonic() >= deadline:
                response = (dict(id=identifier, ok=True, value=state) if state['outputQuiescent']
                            else dict(id=identifier, ok=False, code='retirement_unconfirmed', status=503))
                writer.write(json.dumps(response, allow_nan=False) + '\n')
                writer.flush()
                pending = None
        ready, _, _ = select.select([reader], [], [], .005)
        if not ready:
            continue
        chunk = os.read(reader.fileno(), 4096)
        if not chunk:
            break
        buffer += chunk
        if len(buffer) > 4096 or buffer.count(b'\n') > 1:
            raise ValueError('bounded_single_request_required')
        if not buffer.endswith(b'\n'):
            continue
        if pending:
            raise ValueError('request_before_acknowledgement')
        request = json.loads(buffer, object_pairs_hook=strict_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))
        buffer = b''
        try:
            result = service.request(request)
            if result is None:
                pending = request['id'], time.monotonic() + 3.
                continue
            response = dict(id=request['id'], ok=True, value=result)
        except RequestError as error:
            response = dict(id=request['id'], ok=False, code=error.code, status=error.status)
        writer.write(json.dumps(response, allow_nan=False) + '\n')
        writer.flush()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('open', 'blocked', 'detour', 'appearing-wall'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (os.environ.get('ROS_LOCALHOST_ONLY') != '1' or os.environ.get('ROS_DISTRO') != 'humble'
            or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'
            or set(os.listdir('/sys/class/net')) != {'lo'}):
        raise ValueError('isolated_loopback_humble_required')
    if (not args.output.is_absolute() or args.output.exists() or args.output.is_symlink()
            or args.output.parent.resolve(strict=True) != args.output.parent):
        raise ValueError('new_offline_output_required')
    os.umask(0o077)
    args.output.mkdir(mode=0o700)
    import rclpy
    rclpy.init()
    owner = Service(args.case, args.output)
    def terminate(_signal, _frame):
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        serve(owner)
    finally:
        owner.close()
        rclpy.shutdown()
