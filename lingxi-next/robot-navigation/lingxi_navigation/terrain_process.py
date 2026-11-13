"""Single-owner persistent native terrain child with bounded private pipe IO.

Call from the terrain orchestration thread, not an API/ROS executor callback.
No request queue, auto-restart, network endpoint or physical motion permission.
"""
import base64
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import selectors
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid

from .ground import GroundDeclaration
from .navfn import finite
from .occupancy import ClearanceProjection, DEPENDENCIES, canonical, digest_value
from .runtime_fault import record_fault
from .terrain import TerrainBudget
from .terrain_protocol import MAXIMUM_MESSAGE_BYTES, decode, encode_observations, frame


@dataclass(frozen=True)
class TerrainGrid:
    session: str
    sequence: int
    observations_sha256: str
    map_revision: int
    epoch_sha256: str
    projection: ClearanceProjection
    costs: bytes
    received_at: float
    expires_at: float
    coordinate_frame: object = None
    oldest_scan_ns: object = None
    latest_scan_ns: object = None

    def __post_init__(self):
        if (type(self.session) is not str or len(self.session) != 32
                or any(char not in '0123456789abcdef' for char in self.session)
                or type(self.sequence) is not int or not 0 <= self.sequence < 2**53
                or not digest_value(self.observations_sha256) or not digest_value(self.epoch_sha256)
                or type(self.map_revision) is not int or not 1 <= self.map_revision < 2**53
                or type(self.projection) is not ClearanceProjection or type(self.costs) is not bytes
                or len(self.costs) != self.projection.width * self.projection.height
                or not set(self.costs) <= {0, 254, 255}
                or not finite(self.received_at) or not finite(self.expires_at)
                or not 0 <= self.received_at < self.expires_at <= self.received_at + 15.):
            raise ValueError('complete_session_bound_expiring_terrain_grid_required')
        if ((self.coordinate_frame, self.oldest_scan_ns, self.latest_scan_ns) != (None, None, None)
                and (self.coordinate_frame != 'lingxi_saved_map'
                    or type(self.oldest_scan_ns) is not int or type(self.latest_scan_ns) is not int
                    or not 0 < self.oldest_scan_ns <= self.latest_scan_ns < 2**63)):
            raise ValueError('complete_original_scan_saved_map_terrain_time_required')


class TerrainProcess:
    def __init__(self, *, ground_library, ground_sha256, octomap_library, octomap_sha256,
                 dependencies, projection, declaration, stderr_path, budget=TerrainBudget(), startup_seconds=10.):
        if (sys.platform != 'linux' or type(projection) is not ClearanceProjection
                or type(declaration) is not GroundDeclaration or type(budget) is not TerrainBudget
                or not finite(startup_seconds) or not .1 <= startup_seconds <= 30.
                or not digest_value(ground_sha256) or not digest_value(octomap_sha256)
                or type(dependencies) is not dict or set(dependencies) != DEPENDENCIES
                or not all(map(digest_value, dependencies.values()))):
            raise ValueError('explicit_linux_native_terrain_configuration_required')
        for path in (ground_library, octomap_library):
            if not isinstance(path, Path) or not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file():
                raise ValueError('direct_native_library_required')
        self.log = Path(stderr_path)
        if not self.log.is_absolute() or self.log.exists() or self.log.is_symlink():
            raise ValueError('new_private_absolute_terrain_log_required')
        self.configuration = dict(groundLibrary=str(ground_library), groundSha256=ground_sha256,
            octomapLibrary=str(octomap_library), octomapSha256=octomap_sha256, dependencies=dependencies.copy(),
            projection=asdict(projection), declaration=asdict(declaration), budget=asdict(budget))
        self.projection, self.budget, self.startup_seconds = projection, budget, startup_seconds
        self.owner, self.session = threading.get_ident(), uuid.uuid4().hex
        self.process, self.grid, self.summary = None, None, None
        self.state, self.failure, self.cleanup_complete = 'new', None, False
        self.capture, self.capture_received, self.sequence, self.last_clock = None, None, -1, None

    def _now(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_terrain_process_owner_required')
        now = time.monotonic()
        if self.last_clock is not None and now < self.last_clock:
            self.grid = None
            raise RuntimeError('monotonic_terrain_process_clock_required')
        self.last_clock = now
        return now

    def _exchange(self, message, deadline):
        payload, sent, buffer, size = frame(message), 0, bytearray(), None
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdin, selectors.EVENT_WRITE)
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - self._now()
                if remaining <= 0:
                    raise TimeoutError('terrain_process_deadline_exceeded')
                for key, _ in selector.select(min(.05, remaining)):
                    if key.fileobj is self.process.stdin:
                        sent += os.write(key.fd, payload[sent:sent + 65536])
                        if sent == len(payload):
                            selector.unregister(self.process.stdin)
                    else:
                        chunk = os.read(key.fd, min(65536, (size + 4 if size else 4) - len(buffer)))
                        if not chunk:
                            raise RuntimeError('terrain_worker_pipe_closed')
                        buffer.extend(chunk)
                        if size is None and len(buffer) == 4:
                            size, = struct.unpack('!I', buffer)
                            if not 1 <= size <= MAXIMUM_MESSAGE_BYTES:
                                raise ValueError('bounded_terrain_worker_reply_required')
                        if size is not None and len(buffer) == size + 4:
                            reply = decode(bytes(buffer[4:]))
                            if reply.get('session') != self.session or reply.get('version') != 1:
                                raise ValueError('current_terrain_worker_session_required')
                            if reply.get('kind') == 'failed':
                                raise RuntimeError('native_terrain_worker_failed_see_private_log')
                            if sent != len(payload) or self._now() >= deadline:
                                raise TimeoutError('late_or_unsolicited_terrain_reply')
                            return reply

    def start(self):
        started = self._now()
        if self.state != 'new':
            raise RuntimeError('terrain_process_cannot_restart')
        try:
            root = Path(__file__).resolve().parents[1]
            environment = {key: value for key, value in os.environ.items()
                           if key not in ('LD_PRELOAD', 'PYTHONINSPECT', 'PYTHONSTARTUP', 'PYTHONPATH')}
            environment.update(PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
            # ROS exports a different OctoMap with the same SONAME. This child
            # uses no ROS: select only its source-verified bundled dependencies.
            environment['LD_LIBRARY_PATH'] = str(Path(self.configuration['octomapLibrary']).parent / 'lib')
            descriptor = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb', buffering=0) as log:
                self.process = subprocess.Popen([sys.executable, '-B', '-m', 'lingxi_navigation.terrain_worker', str(os.getpid())],
                    cwd=root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=log, bufsize=0, start_new_session=True)
            for stream in (self.process.stdin, self.process.stdout):
                os.set_blocking(stream.fileno(), False)
            reply = self._exchange(dict(version=1, kind='configure', session=self.session, configuration=self.configuration),
                                   self._now() + self.startup_seconds)
            if reply != dict(version=1, kind='ready', session=self.session):
                raise ValueError('native_terrain_ready_required')
            self.state = 'ready'
            return self
        except BaseException as error:
            record_fault('terrain_process_start', error, started_at=started, code='terrain_process_start_failed')
            self._fault('terrain_process_start_failed')
            raise

    def update(self, observations, *, sequence, received_at):
        now = self._now()
        phase = 'terrain_process_encode'
        self.grid = self.summary = None
        try:
            if (self.state != 'ready' or type(sequence) is not int or not self.sequence < sequence < 2**53
                    or not finite(received_at) or not 0 <= received_at <= now):
                raise ValueError('ready_ordered_terrain_request_required')
            data = encode_observations(observations, self.budget, packed=True)
            capture = (observations.scans[-1].stamp, observations.scans[-1].source_sha256)
            effective = min(received_at, self.capture_received) if capture == self.capture else received_at
            self.capture, self.capture_received, self.sequence = capture, effective, sequence
            expires = effective + self.budget.maximum_age_seconds
            deadline = min(now + self.budget.maximum_processing_seconds, expires)
            phase = 'terrain_process_exchange'
            reply = self._exchange(dict(version=1, kind='update', session=self.session,
                observations=data, sequence=sequence, receivedAt=received_at), deadline)
            phase = 'terrain_process_validate'
            summary = reply.get('summary', {})
            costs = base64.b64decode(reply['costs'], validate=True)
            if (set(reply) != {'version', 'kind', 'session', 'summary', 'costs', 'receivedAt', 'expiresAt'}
                    or reply['kind'] != 'terrain' or summary.get('sequence') != sequence
                    or summary.get('mapRevision') != observations.revision
                    or summary.get('observationsSha256') != observations.sha256
                    or summary.get('traversabilityBytesSha256') != hashlib.sha256(costs).hexdigest()
                    or summary.get('cells') != dict(zip(('free', 'occupied', 'unknown'), (costs.count(i) for i in (0, 254, 255))))
                    or summary.get('scans') != len(observations.scans)
                    or summary.get('rays') != sum(len(scan.returns) for scan in observations.scans)
                    or summary.get('offlineOnly') is not True
                    or any(summary.get(key) is not False for key in ('physicalScaleValidated', 'groundSupportValidated', 'navigationReady'))
                    or reply['receivedAt'] != effective or reply['expiresAt'] != expires):
                raise ValueError('complete_current_source_bound_terrain_reply_required')
            epoch = hashlib.sha256(canonical([observations.map_id, observations.receipt_sha256,
                observations.binding_sha256, observations.geometry_declaration_sha256])).hexdigest()
            result = TerrainGrid(self.session, sequence, observations.sha256, observations.revision, epoch,
                                 self.projection, costs, effective, expires)
            if self._now() >= deadline:
                raise TimeoutError('terrain_reply_expired_before_publication')
            self.grid, self.summary = result, summary
            return result
        except BaseException as error:
            record_fault(phase, error, started_at=now, code='terrain_process_update_failed')
            self._fault('terrain_process_update_failed')
            raise

    def current(self):
        now = self._now()
        if self.process is not None and self.state == 'ready' and self.process.poll() is not None:
            record_fault('terrain_process_current', code='terrain_process_exited')
            self._fault('terrain_process_exited')
        if self.grid is not None and now >= self.grid.expires_at:
            self.grid = None
        return self.grid

    def _fault(self, failure):
        self.grid = self.summary = None
        self.state, self.failure = 'failed', failure
        self._cleanup()

    def _cleanup(self):
        if self.cleanup_complete:
            return
        child = self.process
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=.3)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=1.)
            child.stdin.close()
            child.stdout.close()
        self.cleanup_complete = True

    def close(self):
        self._now()
        self.grid = self.summary = None
        self._cleanup()
        if self.state != 'failed':
            self.state = 'closed'

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
