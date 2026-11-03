"""Read-only localization veto. This is not physical calibration evidence."""
from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import struct
import threading
import time

from .navfn import finite
from .physical_bridge import BridgeError, MapEvidence


def _hex(value, length):
    return type(value) is str and re.fullmatch('[a-f0-9]{%d}' % length, value) is not None


def _counter(value, minimum=0):
    return type(value) is int and minimum <= value < 2**53


def _matrix(value):
    if (type(value) is not list or len(value) != 4
            or any(type(row) is not list or len(row) != 4 for row in value)
            or any(not finite(cell) for row in value for cell in row)
            or any(abs(value[3][i] - (1. if i == 3 else 0.)) > 1e-6 for i in range(4))):
        raise ValueError('rigid_localization_matrix_required')
    for a in range(3):
        for b in range(3):
            if abs(sum(value[a][k] * value[b][k] for k in range(3)) - (1. if a == b else 0.)) > 1e-6:
                raise ValueError('rigid_localization_matrix_required')
    a, b, c = value[:3]
    determinant = a[0]*(b[1]*c[2]-b[2]*c[1]) - a[1]*(b[0]*c[2]-b[2]*c[0]) + a[2]*(b[0]*c[1]-b[1]*c[0])
    if abs(determinant - 1.) > 1e-6 or max(abs(value[i][3]) for i in range(3)) > 1000:
        raise ValueError('bounded_rigid_localization_matrix_required')
    return value


@dataclass(frozen=True)
class LocalizationSnapshot:
    token: tuple
    frame: int
    scan_ns: int
    base_pose: tuple
    polled_at: float
    progress_at: float
    revision: int = 0


def parse_snapshot(value, identity, binding, started_at):
    if type(value) is not dict or set(value) != {'runtime', 'state'}:
        raise ValueError('bound_localization_snapshot_required')
    runtime, state = value['runtime'], value['state']
    if type(runtime) is not dict or type(state) is not dict:
        raise ValueError('bound_localization_snapshot_required')
    expected = dict(mapId=identity.mapId, receiptSha256=identity.receiptSha256)
    if (runtime.get('backend') != 'glim' or runtime.get('inputMode') != 'live'
            or runtime.get('phase') != 'running' or runtime.get('closed') is not False
            or runtime.get('cleanupComplete') is not False or 'failureCode' in runtime
            or runtime.get('selectedMap') != expected
            or not _hex(runtime.get('serviceId'), 32) or not _hex(runtime.get('runId'), 32)
            or not _counter(runtime.get('generation'), 1)):
        raise ValueError('running_selected_live_localization_required')
    actual = state.get('binding')
    if (type(actual) is not dict or set(actual) != {'session', 'input_mode', 'map_id',
            'receipt_sha256', 'points_sha256', 'profile_sha256'}
            or not _hex(actual.get('session'), 32) or actual.get('input_mode') != 'live'
            or actual.get('map_id') != identity.mapId or actual.get('receipt_sha256') != identity.receiptSha256
            or any(actual.get(key) != binding[key] for key in ('points_sha256', 'profile_sha256'))
            or not _counter(state.get('generation')) or state.get('state') != 'tracking'
            or state.get('sourceFrame') != 'lidar_chest_front' or state.get('coordinateUnits') != 'native-raw'
            or state.get('workerReady') is not True or state.get('workerClosed') is not False
            or state.get('cleanupComplete') is not False or state.get('requestPending') is not False
            or not _counter(state.get('consecutiveMatches'), 3) or not _counter(state.get('lastFrame'), 1)
            or not _counter(state.get('referenceFrame'), 1)
            or any(item.get(key) is not False for item in (runtime, state)
                   for key in ('localizationReady', 'navigationReady'))):
        raise ValueError('same_tracking_localization_generation_required')
    _matrix(state.get('mapFromSensor'))
    base = state.get('basePose')
    if (type(base) is not dict or base.get('status') != 'available'
            or base.get('reason') != 'diagnostic_quality_only' or base.get('lastFrame') != state['lastFrame']
            or base.get('coordinateUnits') != 'metres-at-declared-scale'
            or base.get('coordinateFrame') != 'lingxi_saved_map' or base.get('bodyFrame') != 'base_link'
            or base.get('physicalScaleValidated') is not False or base.get('navigationReady') is not False
            or not finite(base.get('metresPerMapUnit')) or base['metresPerMapUnit'] <= 0
            or any(type(base.get(key)) is not str or not re.fullmatch('[1-9][0-9]{0,18}', base[key])
                   for key in ('scanHeaderNs', 'effectiveScanNs'))):
        raise ValueError('timestamped_diagnostic_base_pose_required')
    matrix = _matrix(base.get('mapFromBase'))
    token = (runtime['serviceId'], runtime['generation'], runtime['runId'],
             tuple(sorted(actual.items())), state['generation'], base['metresPerMapUnit'])
    return LocalizationSnapshot(token, state['lastFrame'], int(base['effectiveScanNs']),
        (matrix[0][3], matrix[1][3], math.atan2(matrix[1][0], matrix[0][0])), started_at, started_at)


def _strict_json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_localization_snapshot_field')
            result[key] = value
        return result
    return json.loads(payload, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('finite_json_required')))


class LocalizationMonitor:
    """One bounded background reader; the ROS/output owner only reads memory."""
    def __init__(self, endpoint, identity, binding, *, clock=time.monotonic, exchange=None,
                 maximum_age=.3, progress_timeout=.5):
        if (type(identity) is not MapEvidence or set(binding) != {'points_sha256', 'profile_sha256'}
                or any(not _hex(value, 64) for value in binding.values())
                or any(not finite(value) or not .05 <= value <= 1. for value in (maximum_age, progress_timeout))):
            raise ValueError('explicit_localization_monitor_identity_required')
        self.endpoint, self.identity, self.binding = Path(endpoint), identity, dict(binding)
        self.clock, self.maximum_age, self.progress_timeout = clock, maximum_age, progress_timeout
        self.exchange = exchange or self._exchange
        self.lock, self.stopping = threading.Lock(), threading.Event()
        self.sample, self.watermark, self.revision, self.thread = None, None, 0, None

    def _exchange(self):
        path = self.endpoint
        parent, info = path.parent.stat(), path.lstat()
        if (not path.is_absolute() or path.resolve(strict=True) != path
                or os.geteuid() == 0 or parent.st_uid != os.geteuid() or parent.st_mode & 0o077
                or not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError('owned_private_localization_socket_required')
        deadline = time.monotonic() + .25
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(.25)
            client.connect(str(path))
            _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                raise ValueError('same_uid_localization_service_required')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('bounded_localization_snapshot_required')
            client.settimeout(remaining)
            client.sendall(b'{"action":"navigation_snapshot"}\n')
            payload = bytearray()
            while not payload.endswith(b'\n'):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or len(payload) >= 16384:
                    raise ValueError('bounded_localization_snapshot_required')
                client.settimeout(remaining)
                chunk = client.recv(min(4096, 16384 - len(payload)))
                if not chunk:
                    raise ValueError('complete_localization_snapshot_required')
                payload.extend(chunk)
        after = path.lstat()
        if (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('localization_socket_replaced')
        result = _strict_json(payload)
        if type(result) is not dict or set(result) != {'ok', 'value'} or result['ok'] is not True:
            raise ValueError('localization_snapshot_unavailable')
        return result['value']

    def _withdraw(self):
        if self.sample is not None:
            self.revision += 1
        self.sample = None

    def refresh(self):
        started = self.clock()
        try:
            sample = parse_snapshot(self.exchange(), self.identity, self.binding, started)
        except Exception:
            with self.lock:
                self._withdraw()
            return
        with self.lock:
            now, previous = self.clock(), self.watermark
            if self.stopping.is_set() or not 0 <= now - started <= self.maximum_age:
                self._withdraw()
                return
            if previous is not None and sample.token == previous.token:
                if (sample.frame < previous.frame or sample.scan_ns < previous.scan_ns
                        or (sample.frame == previous.frame) != (sample.scan_ns == previous.scan_ns)
                        or sample.frame == previous.frame and sample.base_pose != previous.base_pose):
                    self._withdraw()
                    return
                if sample.frame == previous.frame:
                    sample = replace(sample, progress_at=previous.progress_at)
            if not 0 <= now - sample.progress_at <= self.progress_timeout:
                self._withdraw()
                return
            if self.sample is None or sample.token != self.sample.token:
                self.revision += 1
            self.sample = self.watermark = replace(sample, revision=self.revision)

    def current(self):
        with self.lock:
            now = self.clock()
            if (self.sample is not None and (self.stopping.is_set()
                    or not 0 <= now - self.sample.polled_at <= self.maximum_age
                    or not 0 <= now - self.sample.progress_at <= self.progress_timeout)):
                self._withdraw()
            return self.sample

    def start(self):
        if self.thread is not None or self.stopping.is_set():
            raise RuntimeError('single_localization_monitor_owner_required')
        def run():
            while not self.stopping.is_set():
                self.refresh()
                self.stopping.wait(.05)
        self.thread = threading.Thread(target=run, name='navigation-localization-reader', daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(1.)
            if self.thread.is_alive():
                raise RuntimeError('localization_reader_cleanup_unconfirmed')
        with self.lock:
            self._withdraw()


class LocalizationGuard:
    def __init__(self, monitor):
        self.monitor, self.activation = monitor, None

    def current(self):
        sample = self.monitor.current()
        if sample is None or self.activation != (sample.token, sample.revision):
            self.activation = None
            return None
        return sample

    def activate_map(self, map_id, initial_pose):
        if map_id != self.monitor.identity.mapId:
            raise BridgeError('fixed_navigation_map_restart_required')
        if initial_pose is not None:
            raise BridgeError('chest_lidar_localization_seed_required', 400)
        sample = self.monitor.current()
        if sample is None:
            raise BridgeError('saved_map_localization_tracking_required', 503)
        self.activation = sample.token, sample.revision
        return self.monitor.identity

    def validate_observation(self, sensors, observation):
        sample = self.current()
        if sample is None or observation.identity != self.monitor.identity:
            return False
        actual = sensors.localization_sample_at(sample.scan_ns)
        if actual is None:
            return False
        x, y, yaw = sample.base_pose
        matched = (math.hypot(actual['x'] - x, actual['y'] - y) <= 1e-4
                   and abs(math.atan2(math.sin(actual['yaw'] - yaw), math.cos(actual['yaw'] - yaw))) <= 1e-4)
        if not matched:
            self.activation = None
        return matched
