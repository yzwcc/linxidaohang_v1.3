"""Offline navigation orchestration, not a replacement for Nav2 control logic.

Every backend generation must fence its action callbacks AND output queue before
retirement is acknowledged. A zero command is not proof of physical stopping.
No physical transport can be attached to this offline-only runtime.
"""
from dataclasses import dataclass, field
import math
import re
import secrets
import threading
import time
from typing import Protocol

from .navfn import finite


def digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


@dataclass(frozen=True)
class MapIdentity:
    map_id: str
    receipt_sha256: str
    metric_evidence_sha256: str
    traversability_sha256: str

    def __post_init__(self):
        if (type(self.map_id) is not str or re.fullmatch('slam-[0-9a-f]{32}', self.map_id) is None
                or not all(digest(value) for value in (self.receipt_sha256,
                    self.metric_evidence_sha256, self.traversability_sha256))):
            raise ValueError('explicit_metric_traversability_bound_map_required')


@dataclass(frozen=True)
class Goal:
    identity: MapIdentity
    x_metres: float
    y_metres: float
    yaw_radians: float

    def __post_init__(self):
        if (type(self.identity) is not MapIdentity or any(not finite(value) or abs(value) > 500
                for value in (self.x_metres, self.y_metres))
                or not finite(self.yaw_radians) or abs(self.yaw_radians) > math.pi):
            raise ValueError('bounded_metric_goal_required')


@dataclass(frozen=True)
class RuntimeInputs:
    identity: MapIdentity
    sequence: int
    received_monotonic: float
    localization_valid: bool
    transforms_current: bool
    costmap_current: bool
    traversable_support: bool
    actions_idle: bool

    def __post_init__(self):
        if (type(self.identity) is not MapIdentity or type(self.sequence) is not int
                or not 0 <= self.sequence < 2**53 or not finite(self.received_monotonic)
                or self.received_monotonic < 0 or any(type(getattr(self, name)) is not bool for name in
                    ('localization_valid', 'transforms_current', 'costmap_current',
                     'traversable_support', 'actions_idle'))):
            raise ValueError('typed_current_navigation_inputs_required')


@dataclass(frozen=True)
class Ticket:
    task_id: str
    epoch: str

    def __post_init__(self):
        if (type(self.task_id) is not str or re.fullmatch('[A-Za-z0-9_-]{1,100}', self.task_id) is None
                or type(self.epoch) is not str or re.fullmatch('[0-9a-f]{32}', self.epoch) is None):
            raise ValueError('bounded_task_and_unique_epoch_required')


@dataclass(frozen=True)
class ObservedVelocity:
    ticket: Ticket
    forward: float
    lateral: float
    yaw: float
    reason: str
    physical_motion_sent: bool = field(default=False, init=False)


class OfflineBackend(Protocol):
    """Callbacks carry the epoch bound when their source was created, never at delivery.

    quiescent means the generation's action callbacks and velocity source/queue
    have retired. An action cancel acknowledgement alone is insufficient. A ROS
    Twist has no task ID: never relabel a shared cmd_vel subscription with the
    currently active ticket. Use a generation-owned source and drain it first.
    """
    offline_only: bool

    def submit(self, ticket: Ticket, goal: Goal) -> None: ...
    def retire(self, ticket: Ticket) -> None: ...
    def quiescent(self, ticket: Ticket) -> bool: ...


class OfflineOutput(Protocol):
    offline_only: bool

    def write(self, observation: ObservedVelocity) -> None: ...


class OfflineNavigationRuntime:
    def __init__(self, backend, output, platform, *, clock=time.monotonic,
                 input_timeout=.3, command_timeout=.15, retirement_timeout=2.,
                 startup_timeout=5., first_command_timeout=1.):
        from .nav2_profile import OfflinePlatform
        if (getattr(backend, 'offline_only', None) is not True
                or getattr(output, 'offline_only', None) is not True
                or type(platform) is not OfflinePlatform
                or any(not finite(value) or not .01 <= value <= 5. for value in
                       (input_timeout, command_timeout, retirement_timeout,
                        startup_timeout, first_command_timeout))):
            raise ValueError('isolated_offline_backend_observer_and_bounded_timeouts_required')
        self.backend, self.output, self.platform, self.clock = backend, output, platform, clock
        self.input_timeout, self.command_timeout = input_timeout, command_timeout
        self.retirement_timeout = retirement_timeout
        self.startup_timeout, self.first_command_timeout = startup_timeout, first_command_timeout
        self.owner = threading.get_ident()
        self.phase, self.detail, self.requires_stop = 'idle', 'idle', False
        self.ticket, self.goal, self.inputs = None, None, None
        self.retiring, self.retired, self.deadline = None, True, None
        self.last_command, self.command_sequence = None, -1
        self.requested_at, self.accepted_at = None, None

    def _owned(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_navigation_event_owner_required')

    def _valid_inputs(self, inputs, goal):
        return (type(inputs) is RuntimeInputs and inputs.identity == goal.identity
                and 0 <= self.clock() - inputs.received_monotonic <= self.input_timeout
                and inputs.localization_valid and inputs.transforms_current and inputs.costmap_current
                and inputs.traversable_support and inputs.actions_idle)

    def start(self, task_id, goal, inputs):
        self._owned()
        if self.phase not in ('idle', 'finished', 'failed') or not self.retired or self.requires_stop:
            raise RuntimeError('previous_navigation_generation_must_retire')
        if (type(task_id) is not str or re.fullmatch('[A-Za-z0-9_-]{1,100}', task_id) is None
                or type(goal) is not Goal or not self._valid_inputs(inputs, goal)):
            raise ValueError('current_map_localization_obstacles_and_ground_support_required')
        self.ticket = Ticket(task_id, secrets.token_hex(16))
        self.goal, self.inputs, self.retired = goal, inputs, False
        self.phase, self.detail, self.retiring, self.deadline = 'requested', 'goal_requested', None, None
        self.requested_at, self.accepted_at = self.clock(), None
        self.last_command, self.command_sequence = None, -1
        try:
            self.backend.submit(self.ticket, goal)
        except Exception:
            self._fault('goal_dispatch_unconfirmed')
            raise
        return self.ticket

    def accepted(self, ticket):
        self._owned()
        if ticket != self.ticket or self.phase != 'requested':
            return False
        if (not self._valid_inputs(self.inputs, self.goal)
                or not 0 <= self.clock() - self.requested_at <= self.startup_timeout):
            self._fault('inputs_expired_before_acceptance')
            return False
        self.phase, self.detail, self.accepted_at = 'running', 'following_path', self.clock()
        return True

    def update_inputs(self, inputs):
        self._owned()
        if type(inputs) is not RuntimeInputs:
            raise ValueError('typed_navigation_inputs_required')
        if self.goal is not None and inputs.identity != self.goal.identity:
            if self.phase in ('requested', 'running'):
                self._fault('navigation_map_identity_changed')
            return False
        if self.inputs is not None and (inputs.sequence <= self.inputs.sequence
                or inputs.received_monotonic < self.inputs.received_monotonic):
            return False
        self.inputs = inputs
        if self.phase in ('requested', 'running') and not self._valid_inputs(inputs, self.goal):
            self._fault('navigation_inputs_invalid')
        return True

    def velocity(self, ticket, sequence, generated_monotonic, forward, lateral, yaw):
        self._owned()
        if ticket != self.ticket or self.phase != 'running':
            return False
        if not self._controller_current():
            return False
        p = self.platform
        limits = ((0., p.maximum_forward_metres_per_second),
                  (-p.maximum_lateral_metres_per_second, p.maximum_lateral_metres_per_second),
                  (-p.maximum_yaw_radians_per_second, p.maximum_yaw_radians_per_second))
        values = (forward, lateral, yaw)
        if (type(sequence) is not int or not 0 <= sequence < 2**53 or sequence <= self.command_sequence
                or not finite(generated_monotonic)
                or not 0 <= self.clock() - generated_monotonic <= self.command_timeout
                or not self._valid_inputs(self.inputs, self.goal)
                or any(not finite(value) or value < low - 8 * math.ulp(high)
                       or value > high + 8 * math.ulp(high)
                       for value, (low, high) in zip(values, limits))):
            self._fault('controller_command_rejected')
            return False
        # Nav2 floating arithmetic may overshoot a bound by a few ULPs. Never
        # pass that overshoot on, and never silently clamp an actual violation.
        forward, lateral, yaw = (min(high, max(low, value))
                                for value, (low, high) in zip(values, limits))
        self.command_sequence, self.last_command = sequence, self.clock()
        try:
            self.output.write(ObservedVelocity(ticket, forward, lateral, yaw, 'controller_output'))
        except Exception:
            self._fault('output_observer_failed')
            raise
        return True

    def terminal(self, ticket, result):
        self._owned()
        if ticket != self.ticket or self.phase not in ('requested', 'running'):
            return False
        if result not in ('succeeded', 'failed', 'canceled'):
            raise ValueError('explicit_navigation_action_result_required')
        if self.phase == 'running' and not self._controller_current():
            return False
        if result == 'succeeded' and (self.phase != 'running'
                or not self._valid_inputs(self.inputs, self.goal)):
            self._fault('arrival_inputs_invalid')
            return False
        self._retire('finished' if result == 'succeeded' else 'failed', 'action_' + result)
        return True

    def pause(self):
        self._owned()
        if self.phase not in ('requested', 'running'):
            raise RuntimeError('active_navigation_required_for_pause')
        self._retire('paused', 'manual_pause')

    def resume(self, inputs):
        self._owned()
        if self.phase != 'paused' or not self.retired or self.requires_stop:
            raise RuntimeError('confirmed_pause_required_for_resume')
        task_id, goal = self.ticket.task_id, self.goal
        self.phase = 'idle'
        try:
            return self.start(task_id, goal, inputs)
        except Exception:
            if self.retired:
                self.phase = 'paused'
            raise

    def stop(self):
        self._owned()
        if self.ticket is None or self.retired:
            self.phase, self.detail, self.requires_stop = 'idle', 'stopped', False
            return
        if self.retiring is not None:
            self.retiring = 'idle'
            self.detail = 'explicit_stop_pending_retirement'
            return
        self._retire('idle', 'explicit_stop')

    def _fault(self, reason):
        self.requires_stop = True
        self._retire('failed', reason)

    def _retire(self, final_phase, reason):
        # Fence callbacks before the first external call, including observer errors.
        self.phase, self.detail, self.retiring = 'stopping', reason, final_phase
        self.deadline = self.clock() + self.retirement_timeout
        self.retired = False
        try:
            self.output.write(ObservedVelocity(self.ticket, 0., 0., 0., reason))
        except Exception:
            self.requires_stop, self.retiring, self.detail = True, 'failed', 'zero_output_unobserved'
        try:
            self.backend.retire(self.ticket)
        except Exception:
            self.requires_stop, self.retiring, self.detail = True, 'failed', 'retirement_unconfirmed'

    def poll(self):
        self._owned()
        if self.phase in ('requested', 'running'):
            if not self._valid_inputs(self.inputs, self.goal):
                self._fault('navigation_inputs_expired')
            elif self.phase == 'requested':
                if not 0 <= self.clock() - self.requested_at <= self.startup_timeout:
                    self._fault('goal_acceptance_timeout')
            else:
                self._controller_current()
        if self.retiring is not None:
            try:
                quiescent = self.backend.quiescent(self.ticket) is True
            except Exception:
                quiescent = False
            if quiescent:
                self.phase, self.retiring, self.retired = self.retiring, None, True
                if self.phase == 'idle':
                    self.requires_stop = False
            elif self.clock() >= self.deadline:
                self.phase, self.requires_stop, self.detail = 'failed', True, 'retirement_timeout'
                # Late retirement must not turn a timed-out stop/pause into success.
                self.retiring = 'failed'
        return self.state()

    def _controller_current(self):
        # Check on callback entry too: a delayed owner loop must not revive an
        # expired controller with a new command or a late success callback.
        origin = self.accepted_at if self.last_command is None else self.last_command
        timeout = self.first_command_timeout if self.last_command is None else self.command_timeout
        if not 0 <= self.clock() - origin <= timeout:
            self._fault('first_controller_output_timeout' if self.last_command is None
                        else 'controller_output_expired')
            return False
        return True

    def state(self):
        self._owned()
        return {'phase': self.phase, 'detail': self.detail, 'ticket': self.ticket,
                'requiresStop': self.requires_stop, 'outputQuiescent': self.retired,
                'offlineOnly': True, 'physicalMotionSent': False, 'navigationReady': False}
