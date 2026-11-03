"""Single-owner product bridge. ROS action retirement is not physical stopping."""
from dataclasses import asdict, dataclass
import math
import re
import secrets
import threading
import time

from .navfn import finite


CHECKS = ('nav2Lifecycle', 'localization', 'tf', 'costmap', 'terrain', 'footprint', 'velocityOutput')


class BridgeError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class MapEvidence:
    mapId: str
    receiptSha256: str
    calibrationSha256: str
    footprintSha256: str

    def __post_init__(self):
        if (type(self.mapId) is not str or not re.fullmatch('slam-[a-f0-9]{32}', self.mapId)
                or any(type(value) is not str or not re.fullmatch('[a-f0-9]{64}', value)
                       for value in (self.receiptSha256, self.calibrationSha256, self.footprintSha256))):
            raise ValueError('complete_navigation_map_evidence_required')


@dataclass(frozen=True)
class MapHandoff:
    """A rebound output needs a new map-bound stop before activation can ACK."""
    identity: MapEvidence

    def __post_init__(self):
        if type(self.identity) is not MapEvidence:
            raise ValueError('typed_navigation_map_handoff_required')


@dataclass(frozen=True)
class Ticket:
    task_id: str
    epoch: str

    def __post_init__(self):
        if (type(self.task_id) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,120}', self.task_id)
                or type(self.epoch) is not str or not re.fullmatch('[a-f0-9]{32}', self.epoch)):
            raise ValueError('bounded_navigation_ticket_required')


def pose(value):
    if (type(value) is not dict or set(value) != {'x', 'y', 'yaw'}
            or any(not finite(value[key]) for key in value)
            or max(abs(value['x']), abs(value['y'])) > 1000
            or abs(value['yaw']) > 2 * math.pi):
        raise BridgeError('bounded_navigation_pose_required', 400)
    return dict(x=float(value['x']), y=float(value['y']),
                yaw=math.atan2(math.sin(value['yaw']), math.cos(value['yaw'])))


@dataclass(frozen=True)
class Observation:
    identity: MapEvidence
    observed_at: float
    pose: dict
    checks: dict
    physical_motion_sent: bool

    def __post_init__(self):
        if (type(self.identity) is not MapEvidence or not finite(self.observed_at)
                or self.observed_at < 0 or type(self.checks) is not dict or set(self.checks) != set(CHECKS)
                or any(type(value) is not bool for value in self.checks.values())
                or type(self.physical_motion_sent) is not bool):
            raise ValueError('typed_current_navigation_observation_required')
        object.__setattr__(self, 'pose', pose(self.pose))
        object.__setattr__(self, 'checks', dict(self.checks))


@dataclass(frozen=True)
class Retirement:
    nonce: str
    identity: MapEvidence
    ticket: Ticket | None
    observed_at: float
    output_quiescent: bool

    def __post_init__(self):
        if (type(self.nonce) is not str or not re.fullmatch('[a-f0-9]{32}', self.nonce)
                or type(self.identity) is not MapEvidence or (self.ticket is not None and type(self.ticket) is not Ticket)
                or not finite(self.observed_at) or self.observed_at < 0 or type(self.output_quiescent) is not bool):
            raise ValueError('typed_output_retirement_evidence_required')


class PhysicalNavigationBridge:
    """backend owns sensor evidence, map activation, ActionClient and output fencing.

    retire(ticket, nonce) must revoke the output generation BEFORE requesting
    action cancellation. retirement(nonce) requires terminal action observation
    or verified teardown of its entire owned producer, plus a fresh matching
    physical-output receipt, never a cancel ACK alone.
    """
    def __init__(self, backend, identity, *, clock=time.monotonic, input_timeout=.3,
                 acceptance_timeout=3., retirement_timeout=3.):
        if (type(identity) is not MapEvidence or any(not finite(value) or not .01 <= value <= 4.
                for value in (input_timeout, acceptance_timeout, retirement_timeout))):
            raise ValueError('explicit_map_and_bounded_navigation_deadlines_required')
        self.backend, self.identity, self.clock = backend, identity, clock
        self.input_timeout, self.acceptance_timeout, self.retirement_timeout = input_timeout, acceptance_timeout, retirement_timeout
        self.owner, self.session, self.sequence = threading.get_ident(), secrets.token_hex(16), 0
        self.phase, self.detail, self.requires_stop = 'failed', 'initial_output_stop_required', True
        self.ticket = self.point_id = self.goal = None
        self.output_quiescent, self.physical_motion_sent = False, False
        self.retiring = self.retirement_nonce = None
        self.deadline = self.requested_at = None
        self.last_pose, self.last_checks = dict(x=0., y=0., yaw=0.), dict.fromkeys(CHECKS, False)
        self.last_pose_observed_at = None
        self.closed = False
        self.seen_requests = set()
        self.request_order = []

    def _owned(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_navigation_bridge_owner_required')

    def _observe(self):
        try:
            value = self.backend.observe()
            valid = (type(value) is Observation and value.identity == self.identity
                     and 0 <= self.clock() - value.observed_at <= self.input_timeout)
        except Exception:
            valid, value = False, None
        if valid:
            self.last_pose, self.last_checks = dict(value.pose), dict(value.checks)
            self.last_pose_observed_at = value.observed_at
            if set(self.last_checks) != set(CHECKS) or any(type(item) is not bool for item in self.last_checks.values()):
                self.last_checks = dict.fromkeys(CHECKS, False)
                return False
            self.physical_motion_sent = self.physical_motion_sent or value.physical_motion_sent
        else:
            self.last_checks = dict.fromkeys(CHECKS, False)
            self.last_pose_observed_at = None
        return valid and all(self.last_checks.values())

    def _retire(self, final_phase, reason):
        self.phase, self.detail, self.output_quiescent = 'stopping', reason, False
        self.retiring, self.retirement_nonce = final_phase, secrets.token_hex(16)
        self.deadline = self.clock() + self.retirement_timeout
        try:
            self.backend.retire(self.ticket, self.retirement_nonce)
        except Exception:
            self.requires_stop, self.retiring, self.detail = True, 'failed', 'output_retirement_unconfirmed'

    def _fault(self, reason):
        self.requires_stop = True
        if self.retiring is None:
            self._retire('failed', reason)
        else:
            self.retiring, self.detail = 'failed', reason

    def tick(self):
        self._owned()
        if self.closed and self.retiring is None:
            return
        try:
            self.backend.tick()
            events = self.backend.events()
            if (type(events) not in (list, tuple) or len(events) > 32
                    or any(type(row) not in (tuple, list) or len(row) != 2 or type(row[0]) is not Ticket
                        or row[1] not in ('accepted', 'succeeded', 'failed', 'canceled', 'unconfirmed') for row in events)):
                raise ValueError('bounded_navigation_events_required')
        except Exception:
            self._fault('navigation_backend_failed')
            events = ()
        ready = self._observe()
        if self.phase in ('requested', 'running'):
            if not ready:
                self._fault('navigation_evidence_expired')
            elif self.phase == 'requested' and not 0 <= self.clock() - self.requested_at <= self.acceptance_timeout:
                self._fault('navigation_acceptance_timeout')
        for ticket, event in events:
            if ticket != self.ticket or self.phase not in ('requested', 'running'):
                continue
            if event == 'accepted' and self.phase == 'requested':
                self.phase, self.detail = 'running', 'navigation_running'
            elif event in ('succeeded', 'failed', 'canceled', 'unconfirmed'):
                if event == 'succeeded':
                    arrived = (self.phase == 'running' and ready and self.goal is not None
                        and math.hypot(self.last_pose['x'] - self.goal['x'], self.last_pose['y'] - self.goal['y']) <= .15
                        and abs(math.atan2(math.sin(self.last_pose['yaw'] - self.goal['yaw']),
                                           math.cos(self.last_pose['yaw'] - self.goal['yaw']))) <= .15)
                    if not arrived:
                        self._fault('arrival_pose_unconfirmed')
                    else:
                        self._retire('finished', 'navigation_succeeded')
                elif event == 'unconfirmed':
                    self._fault('navigation_action_unconfirmed')
                else:
                    self._retire('failed', 'navigation_' + event)
        if self.retiring is not None:
            try:
                proof = self.backend.retirement(self.retirement_nonce)
                confirmed = (type(proof) is Retirement and proof.nonce == self.retirement_nonce
                    and proof.identity == self.identity and proof.ticket == self.ticket and proof.output_quiescent
                    and 0 <= self.clock() - proof.observed_at <= self.input_timeout and self.clock() <= self.deadline)
            except Exception:
                confirmed = False
            if confirmed:
                self.phase, self.retiring, self.output_quiescent = self.retiring, None, True
                if self.phase == 'idle':
                    self.requires_stop = False
                    if self.detail == 'navigation_map_activation_pending':
                        self.detail = 'navigation_map_activated'
            elif self.clock() >= self.deadline:
                self.phase, self.retiring, self.detail, self.requires_stop = 'failed', 'failed', 'output_retirement_timeout', True
        if self.closed:
            self.last_checks = dict.fromkeys(CHECKS, False)
        elif (not self.requires_stop and self.retiring is None and self.output_quiescent
                and self.phase in ('idle', 'paused', 'finished')):
            prepare = getattr(self.backend, 'prepare', None)
            if prepare is not None:
                try:
                    prepare()
                except Exception:
                    self._fault('navigation_preparation_failed')

    def state(self, *, poll=True):
        self._owned()
        if poll:
            self.tick()
        if self.sequence >= 2**53 - 1:
            raise BridgeError('navigation_sequence_exhausted', 503)
        self.sequence += 1
        pose_age = self.clock() - self.last_pose_observed_at if self.last_pose_observed_at is not None else None
        pose_current = (not self.closed and pose_age is not None and 0 <= pose_age <= self.input_timeout
                        and self.last_checks['localization'] and self.last_checks['tf'])
        return dict(protocol=1, source='ros2-nav2', session=self.session, sequence=self.sequence,
            identity=asdict(self.identity), phase=self.phase, detail=self.detail,
            ticket=asdict(self.ticket) if self.ticket else None, pointId=self.point_id,
            requiresStop=self.requires_stop, outputQuiescent=self.output_quiescent,
            pose=dict(self.last_pose),
            poseObservedAtMs=int((time.time() - pose_age) * 1000) if pose_current else None,
            checks=dict(self.last_checks),
            navigationReady=not self.closed and all(self.last_checks.values()) and not self.requires_stop,
            physicalMotionSent=self.physical_motion_sent)

    def request(self, request):
        self._owned()
        if self.closed:
            raise BridgeError('navigation_bridge_closed', 503)
        self.tick()
        if (type(request) is not dict or type(request.get('id')) is not str
                or not re.fullmatch('[a-f0-9]{32}', request['id'])):
            raise BridgeError('navigation_request_id_required', 400)
        if request['id'] in self.seen_requests:
            raise BridgeError('navigation_duplicate_request', 409)
        if len(self.request_order) == 128:
            self.seen_requests.remove(self.request_order.pop(0))
        self.request_order.append(request['id'])
        self.seen_requests.add(request['id'])
        action = request.get('action')
        if action == 'state' and set(request) == {'id', 'action'}:
            return self.state(poll=False)
        if request.get('session') != self.session:
            raise BridgeError('navigation_session_mismatch')
        if action == 'activate_map':
            fields = {'id', 'action', 'session', 'mapId'}
            if not fields <= set(request) or set(request) - fields - {'evidence', 'initialPose'}:
                raise BridgeError('navigation_activation_fields_invalid', 400)
            if (self.requires_stop or not self.output_quiescent or self.retiring is not None
                    or self.phase not in ('idle', 'finished', 'failed')):
                raise BridgeError('navigation_confirmed_stop_required')
            if ('evidence' in request and request['evidence'] != asdict(self.identity)):
                raise BridgeError('navigation_map_evidence_mismatch')
            if type(request['mapId']) is not str or not re.fullmatch('slam-[a-f0-9]{32}', request['mapId']):
                raise BridgeError('navigation_map_id_invalid', 400)
            initial = pose(request['initialPose']) if 'initialPose' in request else None
            try:
                selection = self.backend.activate_map(request['mapId'], initial)
                chosen = selection.identity if type(selection) is MapHandoff else selection
                if type(chosen) is not MapEvidence or chosen.mapId != request['mapId']:
                    raise ValueError('activation_evidence_mismatch')
                if chosen.mapId == self.identity.mapId and chosen != self.identity:
                    raise ValueError('changed_current_map_evidence')
            except BridgeError:
                # Explicitly unsupported selection/seed requests have no side effects.
                raise
            except Exception as error:
                self._fault('navigation_map_activation_unconfirmed')
                raise BridgeError('navigation_map_activation_unconfirmed', 503) from error
            changed = chosen != self.identity
            self.identity, self.phase, self.detail = chosen, 'idle', 'navigation_map_activated'
            self.ticket = self.point_id = self.goal = None
            if changed or type(selection) is MapHandoff:
                self.last_pose, self.last_checks = dict(x=0., y=0., yaw=0.), dict.fromkeys(CHECKS, False)
                self.last_pose_observed_at = None
                self.requires_stop = True
                self._retire('idle', 'navigation_map_activation_pending')
                return None
            return self.state(poll=False)
        fields = {'id', 'action', 'session', 'expectedEpoch', 'taskId', 'mapId', 'evidence'}
        if action == 'start':
            fields |= {'pointId', 'goal'}
        elif self.point_id is not None:
            fields.add('pointId')
        if action not in ('start', 'pause', 'resume', 'stop') or set(request) != fields:
            raise BridgeError('navigation_command_fields_invalid', 400)
        if (request['mapId'] != self.identity.mapId or request['evidence'] != asdict(self.identity)
                or request['expectedEpoch'] != (self.ticket.epoch if self.ticket else None)):
            raise BridgeError('navigation_map_or_epoch_mismatch')
        task = request['taskId']
        if type(task) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,120}', task):
            raise BridgeError('navigation_task_id_invalid', 400)
        if self.ticket and action != 'start' and task != self.ticket.task_id:
            raise BridgeError('navigation_task_mismatch')
        if action != 'start' and self.point_id is not None and request['pointId'] != self.point_id:
            raise BridgeError('navigation_point_mismatch')
        if action == 'stop':
            if self.retiring is not None:
                raise BridgeError('navigation_retirement_in_progress')
            self._retire('idle', 'explicit_stop_pending')
            return None
        if self.requires_stop or not all(self.last_checks.values()):
            raise BridgeError('navigation_admission_not_ready')
        if action in ('start', 'resume'):
            if (not self.output_quiescent or self.retiring is not None
                    or (action == 'start' and self.phase not in ('idle', 'finished', 'failed'))
                    or (action == 'resume' and self.phase != 'paused')):
                raise BridgeError('navigation_task_not_startable')
            goal = pose(request['goal']) if action == 'start' else self.goal
            point_id = request['pointId'] if action == 'start' else self.point_id
            if type(point_id) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,120}', point_id):
                raise BridgeError('navigation_point_id_invalid', 400)
            allocate = getattr(self.backend, 'new_ticket', None)
            try:
                ticket = allocate(task) if allocate is not None else Ticket(task, secrets.token_hex(16))
                if (type(ticket) is not Ticket or ticket.task_id != task
                        or (self.ticket is not None and ticket.epoch == self.ticket.epoch)):
                    raise ValueError('fresh_owned_navigation_ticket_required')
            except Exception as error:
                raise BridgeError('navigation_preparation_not_ready', 503) from error
            self.ticket, self.point_id, self.goal = ticket, point_id, goal
            self.phase, self.detail, self.output_quiescent = 'requested', 'navigation_requested', False
            self.requested_at = self.clock()
            try:
                self.backend.start(self.ticket, dict(goal))
            except Exception as error:
                self._fault('navigation_start_unconfirmed')
                raise BridgeError('navigation_start_unconfirmed', 503) from error
            return self.state(poll=False)
        if self.phase not in ('requested', 'running'):
            raise BridgeError('navigation_task_not_pausable')
        self._retire('paused', 'navigation_pause_pending')
        return None

    def close(self):
        self._owned()
        if not self.closed:
            self._retire('idle', 'bridge_shutdown_pending')
            self.closed = True
            self.last_checks = dict.fromkeys(CHECKS, False)
