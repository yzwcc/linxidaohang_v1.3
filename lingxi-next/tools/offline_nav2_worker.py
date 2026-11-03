"""Process-owned synthetic ROS IO; no robot transport or physical readiness."""
import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

from prepare_nav2_offline import strict_object
from lingxi_navigation.runtime import Goal, MapIdentity, Ticket
from lingxi_navigation.navfn import finite

LIMIT = 8192


class Channel:
    def __init__(self, stream, ticket):
        self.stream, self.ticket = stream, ticket
        self.sent, self.received = 0, 0
        stream.setblocking(False)

    def send(self, kind, value, *, optional=False):
        sequence = self.sent + 1
        raw = json.dumps(dict(schema=1, ticket=asdict(self.ticket), sequence=sequence,
            issued=time.monotonic(), kind=kind, value=value), allow_nan=False,
            separators=(',', ':')).encode()
        if len(raw) > LIMIT:
            raise ValueError('bounded_ros_ipc_packet_required')
        try:
            if self.stream.send(raw) != len(raw):
                raise RuntimeError('atomic_ros_ipc_packet_required')
        except BlockingIOError:
            if optional:
                return False
            raise RuntimeError('ros_event_consumer_not_draining') from None
        self.sent = sequence
        return True

    def receive(self):
        try:
            raw = self.stream.recv(LIMIT + 1)
        except BlockingIOError:
            return None
        if not raw:
            raise EOFError('ros_worker_channel_closed')
        if len(raw) > LIMIT:
            raise ValueError('bounded_ros_ipc_packet_required')
        value = json.loads(raw, object_pairs_hook=strict_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('finite_ros_ipc_required')))
        if (type(value) is not dict or set(value) != {'schema', 'ticket', 'sequence', 'issued', 'kind', 'value'}
                or type(value['schema']) is not int or value['schema'] != 1
                or value['ticket'] != asdict(self.ticket) or type(value['sequence']) is not int
                or not self.received < value['sequence'] < 2**53
                or not finite(value['issued']) or value['issued'] < 0
                or type(value['kind']) is not str or type(value['value']) is not dict):
            raise ValueError('current_typed_ros_ipc_packet_required')
        self.received = value['sequence']
        return value


def worker_command(fd, case, output, ticket):
    return [sys.executable, '-B', str(Path(__file__).resolve()), '--fd', str(fd), '--case', case,
            '--output', str(output), '--task', ticket.task_id, '--epoch', ticket.epoch]


class WorkerGeneration:
    """The runtime owner never imports, spins or destroys native ROS entities."""
    def __init__(self, owner, ticket, goal):
        self.owner, self.ticket, self.goal = owner, ticket, goal
        self.retiring, self.retired_at, self.escalation = False, None, 0
        self.next_snapshot, self.last_diagnostic = 0., {}
        self.started_at = time.monotonic()
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.channel = Channel(parent, ticket)
        self.log = None
        try:
            self.log = (owner.output / ('worker-' + ticket.epoch + '.log')).open('x', encoding='utf-8')
            self.process = subprocess.Popen(worker_command(child.fileno(), owner.case, owner.output, ticket),
                pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL, stdout=self.log,
                stderr=subprocess.STDOUT, start_new_session=True)
            self.channel.send('initialize', dict(goal=asdict(goal), origin=owner.origin,
                commandTimeout=owner.runtime.command_timeout, startupTimeout=owner.runtime.startup_timeout,
                pose=owner.pose.copy(), latest=list(owner.observer.latest),
                obstacleActivated=owner.obstacle_activated))
        except BaseException:
            if hasattr(self, 'process'):
                self.process.kill()
                self.process.wait(timeout=2.)
            parent.close()
            if self.log is not None:
                self.log.close()
            raise
        finally:
            child.close()

    def snapshot(self, now):
        if now < self.next_snapshot:
            return
        self.next_snapshot = now + .02
        self.channel.send('snapshot', dict(pose=self.owner.pose.copy(), latest=list(self.owner.observer.latest),
            obstacleActivated=self.owner.obstacle_activated, detail=self.owner.runtime.detail,
            acceptedAt=self.owner.runtime.accepted_at), optional=True)

    def event(self, packet, now):
        if self.retiring:
            return
        kind, value = packet['kind'], packet['value']
        age = now - packet['issued']
        if not 0 <= age <= self.owner.runtime.command_timeout:
            self.owner.runtime.reject_velocity_source(self.ticket)
            return
        if kind == 'accepted' and not value:
            self.owner.runtime.accepted(self.ticket)
        elif kind == 'velocity' and set(value) == {'sequence', 'generated', 'forward', 'lateral', 'yaw'}:
            self.owner.runtime.velocity(self.ticket, value['sequence'], value['generated'],
                value['forward'], value['lateral'], value['yaw'])
        elif kind == 'source_invalid' and not value:
            self.owner.runtime.reject_velocity_source(self.ticket)
        elif kind == 'terminal' and set(value) == {'result'} and value['result'] in ('succeeded', 'failed', 'canceled'):
            result = value['result']
            pose, goal = self.owner.pose, self.goal
            if result == 'succeeded' and not (math.hypot(pose[0] - goal.x_metres, pose[1] - goal.y_metres) <= .16
                    and abs(math.atan2(math.sin(pose[2] - goal.yaw_radians),
                        math.cos(pose[2] - goal.yaw_radians))) <= .16):
                result = 'failed'
            self.owner.runtime.terminal(self.ticket, result)
        elif kind == 'diagnostic':
            if (set(value) != {'startupSent', 'startupConfirmed', 'states', 'goalSent',
                    'constructionPhases', 'tickPhaseMaximumSeconds'}
                    or any(type(value[key]) is not bool for key in ('startupSent', 'startupConfirmed', 'goalSent'))
                    or type(value['states']) is not list or len(value['states']) != 4
                    or any(item is not None and (type(item) is not int or item not in (1, 2, 3))
                           for item in value['states'])
                    or type(value['constructionPhases']) is not list or len(value['constructionPhases']) > 8
                    or type(value['tickPhaseMaximumSeconds']) is not dict
                    or len(value['tickPhaseMaximumSeconds']) > 16
                    or any(not finite(item) or item < 0 for item in value['tickPhaseMaximumSeconds'].values())):
                raise ValueError('bounded_ros_diagnostic_required')
            self.last_diagnostic = value
        else:
            raise ValueError('known_ros_worker_event_required')

    def tick(self):
        if self.retiring:
            self.escalate(time.monotonic())
            return
        try:
            self.snapshot(time.monotonic())
            for _ in range(40):
                packet = self.channel.receive()
                if packet is None:
                    break
                self.event(packet, time.monotonic())
                if self.retiring:
                    return
        except (EOFError, OSError, ValueError, RuntimeError):
            self.owner.runtime.terminal(self.ticket, 'failed')
            return
        if self.process.poll() is not None:
            self.owner.runtime.terminal(self.ticket, 'failed')

    def escalate(self, now):
        for stage, delay, value in ((1, .2, signal.SIGTERM), (2, .4, signal.SIGKILL)):
            if self.escalation < stage and now - self.retired_at >= delay:
                self.escalation = stage
                try:
                    os.killpg(self.process.pid, value)
                except ProcessLookupError:
                    pass

    def retire(self):
        if self.retiring:
            return
        self.retiring, self.retired_at = True, time.monotonic()
        self.channel.stream.close()
        try:
            os.killpg(self.process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        output = self.owner.output / self.ticket.epoch
        output.mkdir(mode=0o700, exist_ok=True)
        with (output / 'startup.json').open('x', encoding='utf-8') as stream:
            json.dump(dict(**self.last_diagnostic, runtimeDetail=self.owner.runtime.detail,
                elapsedSeconds=self.retired_at - self.started_at,
                acceptanceSeconds=(None if self.owner.runtime.accepted_at is None else
                    self.owner.runtime.accepted_at - self.owner.runtime.requested_at),
                maximumLoopGapSeconds=self.owner.maximum_loop_gap,
                workerProcess=self.process.pid, rosIoProcessSeparated=True,
                startupTimeoutSeconds=self.owner.runtime.startup_timeout,
                physicalMotionSent=False, navigationReady=False), stream, allow_nan=False)

    def quiescent(self):
        if not self.retiring:
            return False
        self.escalate(time.monotonic())
        if self.process.poll() is None:
            return False
        # The isolated service is PID 1 and must reap adopted dead descendants.
        for _ in range(64):
            try:
                if os.waitpid(-self.process.pid, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
        try:
            os.killpg(self.process.pid, 0)
            return False
        except ProcessLookupError:
            self.log.close()
            return True

    def close(self):
        self.retire()
        deadline = time.monotonic() + 2.
        while not self.quiescent():
            if time.monotonic() >= deadline:
                raise RuntimeError('ros_worker_group_retirement_unconfirmed')
            time.sleep(.005)


def vectors(value):
    return type(value) is list and len(value) == 3 and all(finite(item) and abs(item) <= 10000 for item in value)


def inputs_ready(owner):
    if owner.inputs_current():
        return True
    if owner.runtime.accepted_at is not None:
        raise RuntimeError('parent_snapshot_expired')
    # Cold ROS construction can leave old queued snapshots. No dispatch is allowed
    # until current data arrives; the parent still owns the five-second deadline.
    return False


class RuntimeEvents:
    def __init__(self, channel, command_timeout, startup_timeout):
        self.channel, self.command_timeout, self.startup_timeout = channel, command_timeout, startup_timeout
        self.detail, self.accepted_at = 'goal_requested', None

    def accepted(self, ticket):
        self.channel.send('accepted', {})
        return True

    def velocity(self, ticket, sequence, generated, forward, lateral, yaw):
        self.channel.send('velocity', dict(sequence=sequence, generated=generated,
            forward=forward, lateral=lateral, yaw=yaw))
        return True

    def terminal(self, ticket, result):
        self.channel.send('terminal', dict(result=result))

    def reject_velocity_source(self, ticket):
        self.channel.send('source_invalid', {})


def run_worker(args):
    from offline_nav2_service import RosGeneration, isolated_environment
    isolated_environment()
    ticket = Ticket(args.task, args.epoch)
    stream = socket.socket(fileno=args.fd)
    if stream.family != socket.AF_UNIX or stream.type != socket.SOCK_SEQPACKET:
        raise ValueError('private_seqpacket_parent_required')
    channel = Channel(stream, ticket)
    if not select.select([stream], [], [], 2.)[0]:
        raise TimeoutError('worker_initialization_required')
    packet = channel.receive()
    if packet['kind'] != 'initialize' or not 0 <= time.monotonic() - packet['issued'] <= 2.:
        raise ValueError('fresh_worker_initialization_required')
    value = packet['value']
    expected = {'goal', 'origin', 'commandTimeout', 'startupTimeout', 'pose', 'latest', 'obstacleActivated'}
    if (set(value) != expected or not vectors(value['pose']) or not vectors(value['latest'])
            or type(value['obstacleActivated']) is not bool or not finite(value['origin'])
            or not 0 < value['commandTimeout'] <= .15 or value['startupTimeout'] != 5.):
        raise ValueError('bounded_worker_initialization_required')
    goal = value['goal']
    goal = Goal(MapIdentity(**goal['identity']), goal['x_metres'], goal['y_metres'], goal['yaw_radians'])
    from run_nav2_offline import world
    owner = SimpleNamespace(output=args.output, source=world(args.case), pose=value['pose'],
        origin=value['origin'], observer=SimpleNamespace(latest=value['latest']),
        obstacle_activated=value['obstacleActivated'], maximum_loop_gap=0.)
    owner.runtime = RuntimeEvents(channel, value['commandTimeout'], value['startupTimeout'])
    owner.visible_points = lambda: owner.source['points'] if owner.obstacle_activated else []
    observed = packet['issued']
    owner.inputs_current = lambda: 0 <= time.monotonic() - observed <= .3
    import rclpy
    def terminate(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGTERM, terminate)
    rclpy.init(args=[])
    generation = None
    try:
        generation = RosGeneration(owner, ticket, goal)
        next_diagnostic = 0.
        while True:
            for _ in range(32):
                packet = channel.receive()
                if packet is None:
                    break
                value = packet['value']
                if (packet['kind'] != 'snapshot' or set(value) != {'pose', 'latest', 'obstacleActivated', 'detail', 'acceptedAt'}
                        or not vectors(value['pose']) or not vectors(value['latest'])
                        or type(value['obstacleActivated']) is not bool or type(value['detail']) is not str
                        or len(value['detail']) > 100
                        or value['acceptedAt'] is not None and not finite(value['acceptedAt'])):
                    raise ValueError('typed_worker_snapshot_required')
                observed = packet['issued']
                owner.pose, owner.observer.latest = value['pose'], value['latest']
                owner.obstacle_activated = value['obstacleActivated']
                owner.runtime.detail, owner.runtime.accepted_at = value['detail'], value['acceptedAt']
            if not inputs_ready(owner):
                select.select([stream], [], [], .005)
                continue
            generation.tick()
            now = time.monotonic()
            if now >= next_diagnostic:
                channel.send('diagnostic', dict(startupSent=generation.lifecycle.sent,
                    startupConfirmed=generation.lifecycle.confirmed, states=generation.lifecycle.states,
                    goalSent=generation.sent, constructionPhases=generation.construction_phases,
                    tickPhaseMaximumSeconds=generation.tick_phase_maximum_seconds))
                next_diagnostic = now + .2
            select.select([stream], [], [], .005)
    finally:
        if generation is not None:
            generation.close()
        rclpy.shutdown()
        stream.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fd', type=int, required=True)
    parser.add_argument('--case', choices=('open', 'blocked', 'detour', 'appearing-wall'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--epoch', required=True)
    run_worker(parser.parse_args())
