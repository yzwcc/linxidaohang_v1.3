"""Owned physical Nav2 producers. No sensor, localization or motion simulation."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from .nav2_profile import BEHAVIOR_TREE, THROUGH_POSES_BEHAVIOR_TREE, _parameters
from .navfn import finite
from .physical_bridge import CHECKS, MapEvidence, Observation, Ticket
from .physical_velocity import ControllerVelocityChannel, VelocityLimits, controller_namespace


def spin_callbacks(executor, *, clock=time.monotonic):
    """Drain a bounded DDS burst without starving socket/output watchdogs."""
    deadline = clock() + .005
    for _ in range(64):
        executor.spin_once(timeout_sec=0.)
        if clock() >= deadline:
            break


def _direct(path, *, directory=False):
    path = Path(path)
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or not (path.is_dir() if directory else path.is_file())):
        raise ValueError('direct_absolute_nav2_path_required')
    return path


def load_profile(path, identity, *, map_frame, base_frame, sensor_frame, cloud_topic,
                 odometry_topic, terrain_topic, tf_topic='/tf', tf_static_topic='/tf_static'):
    path = _direct(path)
    if path.stat().st_size > 16384:
        raise ValueError('bounded_physical_nav2_profile_required')
    def strict(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_nav2_profile_field')
            result[key] = value
        return result
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=strict)
    keys = {'schema', 'footprintSha256', 'clearanceRadiusMetres', 'stoppingMarginMetres', 'forward',
            'lateral', 'angular', 'acceleration', 'deceleration', 'angularAcceleration',
            'commandAge', 'firstCommandTimeout'}
    if (type(value) is not dict or set(value) != keys or type(value['schema']) is not int
            or value['schema'] != 1 or value['footprintSha256'] != identity.footprintSha256):
        raise ValueError('complete_footprint_bound_nav2_profile_required')
    limits = VelocityLimits(identity, value['forward'], value['lateral'], value['angular'],
        value['acceleration'], value['deceleration'], value['angularAcceleration'],
        value['commandAge'], value['firstCommandTimeout'])
    return PhysicalNav2Profile(limits, value['clearanceRadiusMetres'], value['stoppingMarginMetres'],
        map_frame, base_frame, sensor_frame, cloud_topic, odometry_topic, terrain_topic, tf_topic, tf_static_topic)


@dataclass(frozen=True)
class PhysicalNav2Profile:
    limits: VelocityLimits
    clearance_radius_metres: float
    stopping_margin_metres: float
    map_frame: str
    base_frame: str
    sensor_frame: str
    cloud_topic: str
    odometry_topic: str
    terrain_topic: str
    tf_topic: str = '/tf'
    tf_static_topic: str = '/tf_static'

    def __post_init__(self):
        frames = (self.map_frame, self.base_frame, self.sensor_frame)
        topics = (self.cloud_topic, self.odometry_topic, self.terrain_topic, self.tf_topic, self.tf_static_topic)
        if (type(self.limits) is not VelocityLimits
                or any(type(value) is not str or not re.fullmatch('[A-Za-z][A-Za-z0-9_/]{0,119}', value)
                       or '//' in value or value.endswith('/') for value in frames)
                or len(set(frames)) != 3
                or any(type(value) is not str or not re.fullmatch('/[A-Za-z_][A-Za-z0-9_/]{0,199}', value)
                       or '//' in value or value.endswith('/') for value in topics)
                or len(set(topics)) != len(topics)
                or not finite(self.clearance_radius_metres) or not .1 <= self.clearance_radius_metres <= 1.5
                or not finite(self.stopping_margin_metres) or not .05 <= self.stopping_margin_metres <= 1.
                or self.limits.lateral != 0.
                or max(self.limits.forward / self.limits.deceleration,
                       self.limits.angular / self.limits.angular_acceleration) > 2.7):
            raise ValueError('explicit_bounded_physical_nav2_profile_required')

    @property
    def maximum_forward_metres_per_second(self):
        return self.limits.forward

    @property
    def maximum_yaw_radians_per_second(self):
        return self.limits.angular

    @property
    def acceleration_metres_per_second_squared(self):
        return self.limits.deceleration

    @property
    def yaw_acceleration_radians_per_second_squared(self):
        return self.limits.angular_acceleration

    def parameters(self, ticket, root):
        # The live acquisition contract supplies saved-map-frame odometry and
        # terrain. Use that same frame for the rolling local costmap, not a
        # nonexistent odom-frame TF. No identity transform is manufactured.
        result = _parameters(self, root / 'navigate.xml', root / 'navigate-through.xml',
            controller_namespace(ticket)[1:], map_mode='support_fused_revisions', use_sim_time=False,
            frames=dict(map=self.map_frame, local=self.map_frame, base=self.base_frame),
            topics=dict(map=self.terrain_topic, odom=self.odometry_topic, cloud=self.cloud_topic))
        smoother = result['velocity_smoother']['ros__parameters']
        smoother['max_accel'] = [self.limits.acceleration, self.limits.acceleration,
                                 self.limits.angular_acceleration]
        smoother['max_decel'] = [-self.limits.deceleration, -self.limits.deceleration,
                                 -self.limits.angular_acceleration]
        smoother['velocity_timeout'] = self.limits.command_age
        for name in ('global_costmap', 'local_costmap'):
            # At 10 Hz updates, Humble's 5 Hz publish condition can retain a
            # grid for 300 ms. Produce fresh grids; never renew their receipts.
            result[name][name]['ros__parameters']['publish_frequency'] = 10.
        return result


def prepare_producer(profile, ticket, root):
    if type(profile) is not PhysicalNav2Profile or type(ticket) is not Ticket:
        raise ValueError('typed_physical_nav2_preparation_required')
    root = Path(root)
    parent = _direct(root.parent, directory=True)
    if root.exists() or root.is_symlink() or root.name != ticket.epoch:
        raise ValueError('new_generation_nav2_directory_required')
    if sys.platform == 'linux' and (parent.stat().st_uid != os.geteuid() or parent.stat().st_mode & 0o077):
        raise ValueError('private_owned_nav2_run_directory_required')
    root.mkdir(mode=0o700)
    files = {
        'navigate.xml': BEHAVIOR_TREE.encode(),
        'navigate-through.xml': THROUGH_POSES_BEHAVIOR_TREE.encode(),
        'nav2-parameters.yaml': json.dumps({controller_namespace(ticket)[1:]:
            profile.parameters(ticket, root)}, allow_nan=False).encode(),
        'nav2_physical.launch.py': (Path(__file__).resolve().parents[1] / 'nav2_physical.launch.py').read_bytes(),
    }
    for name, data in files.items():
        with (root / name).open('xb') as stream:
            stream.write(data)
        (root / name).chmod(0o600)
    manifest = dict(protocol=1, namespace=controller_namespace(ticket), identity=asdict(profile.limits.identity),
        profile=asdict(profile), purpose='owned_physical_nav2_producer_not_robot_acceptance',
        files={name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
    with (root / 'producer.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, allow_nan=False)
    (root / 'producer.json').chmod(0o600)
    return manifest


class Nav2Process:
    """Nonblocking TERM/KILL and whole-process-group retirement, Linux only."""
    def __init__(self, profile, ticket, root, *, clock=time.monotonic):
        if sys.platform != 'linux' or os.environ.get('ROS_DISTRO') != 'humble':
            raise RuntimeError('linux_humble_nav2_producer_required')
        executable = shutil.which('ros2')
        if executable is None:
            raise RuntimeError('ros2_executable_required')
        prepare_producer(profile, ticket, root)
        self.clock, self.stopping, self.kill_at = clock, False, None
        self.log = (root / 'nav2.log').open('xb')
        environment = dict(os.environ, ROS_LOG_DIR=str(root / 'ros-log'))
        try:
            self.child = subprocess.Popen([executable, 'launch', str(root / 'nav2_physical.launch.py')],
                stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
                start_new_session=True, env=environment)
        except BaseException:
            self.log.close()
            raise

    def alive(self):
        return not self.stopping and self.child.poll() is None

    def _live_group(self):
        with os.scandir('/proc') as entries:
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                try:
                    fields = (Path(entry.path) / 'stat').read_text().rsplit(')', 1)[1].split()
                    if int(fields[2]) == self.child.pid and fields[0] not in ('Z', 'X'):
                        return True
                except (FileNotFoundError, ProcessLookupError):
                    continue
        return False

    def _signal(self, value):
        try:
            os.killpg(self.child.pid, value)
        except ProcessLookupError:
            pass

    def stop(self):
        if not self.stopping:
            self.stopping, self.kill_at = True, self.clock() + .6
            self._signal(signal.SIGTERM)

    def quiescent(self):
        if not self.stopping:
            return False
        if self.clock() >= self.kill_at:
            self._signal(signal.SIGKILL)
        exited = self.child.poll() is not None
        stopped = exited and not self._live_group()
        if stopped:
            self.log.close()
        return stopped


class Nav2Generation:
    def __init__(self, profile, ticket, run_root, footprint, executor, *, clock=time.monotonic):
        import rclpy
        from .nav2_action import Nav2ActionDriver
        from .physical_ros import RosSensorEvidence, RosTopics
        self.ticket, self.identity, self.clock = ticket, profile.limits.identity, clock
        self.claimed = self.closing = False
        self.node = self.driver = self.sensors = self.process = self.controller = None
        self.executor, self.backend = executor, None
        self.node = rclpy.create_node('physical_navigation_client', namespace=controller_namespace(ticket),
            context=executor.context, use_global_arguments=False)
        try:
            executor.add_node(self.node)
            topics = RosTopics(cloud=profile.cloud_topic, odometry=profile.odometry_topic,
                tf=profile.tf_topic, tf_static=profile.tf_static_topic, terrain=profile.terrain_topic,
                global_costmap=controller_namespace(ticket) + '/global_costmap/costmap',
                local_costmap=controller_namespace(ticket) + '/local_costmap/costmap')
            self.sensors = RosSensorEvidence(self.node, self.identity, topics, map_frame=profile.map_frame,
                base_frame=profile.base_frame, sensor_frame=profile.sensor_frame, footprint=footprint, clock=clock)
            self.driver = Nav2ActionDriver(self.node, profile.map_frame, clock=clock)
            self.process = Nav2Process(profile, ticket, run_root / ticket.epoch, clock=clock)
        except BaseException:
            self.close()
            raise

    def connect(self, output, limits):
        from .nav2_action import RosNavigationBackend
        self.controller = ControllerVelocityChannel(output, limits, clock=self.clock)
        self.backend = RosNavigationBackend(self.driver, self.sensors, output,
            lambda *_: (_ for _ in ()).throw(RuntimeError('live_map_activation_not_integrated')),
            lambda: None, controller=self.controller)

    def tick(self):
        if self.closing:
            return
        if not self.process.alive():
            raise RuntimeError('owned_nav2_producer_exited')
        self.driver.poll()
        if self.driver.generation is not None:
            self.controller.poll(self.driver.generation, self.backend.observe())

    def close_ros(self):
        errors = []
        for name in ('controller', 'driver', 'sensors'):
            value = getattr(self, name)
            if value is not None:
                try:
                    value.retire() if name == 'controller' else value.close()
                    setattr(self, name, None)
                except Exception as error:
                    errors.append(error)
        if self.node is not None and not errors:
            try:
                self.executor.remove_node(self.node)
                self.node.destroy_node()
                self.node = None
            except Exception as error:
                errors.append(error)
        if errors:
            raise RuntimeError('nav2_ros_cleanup_unconfirmed') from errors[0]

    def begin_close(self):
        self.closing = True
        try:
            self.close_ros()
        finally:
            if self.process is not None:
                self.process.stop()

    def quiescent(self):
        process_stopped = self.process is None or self.process.quiescent()
        return (self.closing and self.node is None and self.controller is None
                and self.driver is None and self.sensors is None
                and process_stopped)

    def close(self):
        deadline = time.monotonic() + 2.
        cleanup_error = None
        while True:
            try:
                self.begin_close()
            except Exception as error:
                cleanup_error = error
            if self.quiescent():
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('owned_nav2_cleanup_unconfirmed') from cleanup_error
            time.sleep(.01)


class OwnedNav2Backend:
    """Prewarm an unused epoch; never queue a goal while Nav2 is starting."""
    def __init__(self, identity, output, factory, spin, *, clock=time.monotonic, startup_timeout=30.):
        if (type(identity) is not MapEvidence or not callable(factory) or not callable(spin)
                or not finite(startup_timeout) or not 1. <= startup_timeout <= 60.):
            raise ValueError('owned_nav2_backend_configuration_required')
        self.identity, self.output, self.factory, self.spin, self.clock = identity, output, factory, spin, clock
        self.startup_timeout, self.owner = startup_timeout, threading.get_ident()
        self.generation = self.started_at = self.retiring = self.retire_at = None
        self.reserved, self.startup_complete = None, False
        self.used_epochs, self.closed = set(), False

    def _owned(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_nav2_producer_owner_required')

    def prepare(self):
        import secrets
        self._owned()
        if self.closed or self.retiring is not None or self.generation is not None:
            return
        if not self.output.ready(self.identity) or len(self.used_epochs) >= 4096:
            return
        ticket = Ticket('preparing', secrets.token_hex(16))
        if ticket.epoch in self.used_epochs:
            raise RuntimeError('nav2_producer_epoch_reuse')
        self.used_epochs.add(ticket.epoch)
        self.started_at = self.clock()
        self.startup_complete = False
        self.generation = self.factory(ticket)
        if self.generation.ticket != ticket or self.generation.identity != self.identity:
            raise RuntimeError('nav2_producer_identity_mismatch')

    def tick(self):
        self._owned()
        self.spin()
        self.output.poll()
        if self.generation is not None and self.retiring is None:
            self.generation.tick()
            if not self.startup_complete:
                try:
                    self.startup_complete = all(self.observe().checks.values())
                except RuntimeError:
                    pass
            if not self.startup_complete and self.clock() - self.started_at > self.startup_timeout:
                raise RuntimeError('nav2_producer_startup_timeout')

    def observe(self):
        self._owned()
        if self.generation is None or self.generation.closing or self.retiring is not None:
            return Observation(self.identity, self.clock(), dict(x=0., y=0., yaw=0.),
                               dict.fromkeys(CHECKS, False), self.output.physical_motion_sent)
        return self.generation.backend.observe()

    def events(self):
        self._owned()
        if self.generation is None or self.generation.closing:
            return ()
        return self.generation.driver.events()

    def new_ticket(self, task_id):
        self._owned()
        if (self.closed or self.retiring is not None or self.generation is None or self.generation.claimed
                or self.reserved is not None
                or not all(self.observe().checks.values())):
            raise RuntimeError('prepared_nav2_producer_required')
        ticket = Ticket(task_id, self.generation.ticket.epoch)
        self.output.reserve(ticket, self.identity)
        self.reserved = ticket
        return ticket

    def start(self, ticket, goal):
        self._owned()
        if (ticket != self.reserved or self.generation is None or self.generation.claimed
                or self.retiring is not None or self.closed):
            raise RuntimeError('prepared_nav2_ticket_required')
        self.generation.claimed = True
        self.generation.backend.start(ticket, goal)

    def retire(self, ticket, nonce):
        self._owned()
        self.retiring, self.retire_at = nonce, self.clock()
        try:
            if self.generation is not None:
                if self.generation.controller is not None:
                    self.generation.controller.retire()
        finally:
            try:
                self.output.retire(ticket, nonce)
            finally:
                if self.generation is not None:
                    driver = self.generation.driver
                    if driver is not None:
                        driver.cancel(driver.generation.ticket if driver.generation is not None else None)

    def retirement(self, nonce):
        self._owned()
        if nonce != self.retiring:
            return None
        generation = self.generation
        if generation is not None:
            driver = generation.driver
            ticket = driver.generation.ticket if driver is not None and driver.generation is not None else None
            # A cancel ACK alone grants nothing. Either observe the terminal
            # Action, or revoke its client and kill the entire owned producer.
            if (generation.closing or driver is None or driver.action_retired(ticket)
                    or self.clock() - self.retire_at >= .3):
                generation.begin_close()
            if not generation.quiescent():
                return None
        proof = self.output.complete_retirement(nonce)
        if proof is not None:
            self.generation, self.retiring = None, None
            self.reserved = None
        return proof

    def activate_map(self, map_id, initial_pose):
        raise RuntimeError('live_map_loader_and_localization_seed_not_integrated')

    def close(self):
        self._owned()
        self.closed = True
        try:
            self.output.fence_controller()
        finally:
            if self.generation is not None:
                self.generation.close()
                self.generation = None
