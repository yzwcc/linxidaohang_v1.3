"""Generation-bound controller transport and explicit physical velocity limits.

This is not a Nav2 launcher. A producer must own the namespace for the exact
ticket; a shared cmd_vel subscription cannot be adapted by changing a ticket.
Limits are configuration, not proof of measured robot capability or clearance.
"""
from dataclasses import dataclass
import math
import threading
import time

from .navfn import finite
from .physical_bridge import MapEvidence, Ticket


def controller_namespace(ticket):
    if type(ticket) is not Ticket:
        raise ValueError('typed_controller_ticket_required')
    return '/lingxi_nav_' + ticket.epoch


@dataclass(frozen=True)
class VelocityLimits:
    identity: MapEvidence
    forward: float
    lateral: float
    angular: float
    acceleration: float
    deceleration: float
    angular_acceleration: float
    command_age: float = .15
    first_command_timeout: float = 1.

    def __post_init__(self):
        bounds = ((self.forward, .02, .5), (self.lateral, 0., .3),
                  (self.angular, .05, 1.), (self.acceleration, .01, 1.),
                  (self.deceleration, .01, 1.), (self.angular_acceleration, .05, 2.),
                  (self.command_age, .02, .15), (self.first_command_timeout, .1, 1.))
        if (type(self.identity) is not MapEvidence
                or any(not finite(value) or not low <= value <= high for value, low, high in bounds)):
            raise ValueError('explicit_map_bound_physical_velocity_limits_required')

    def bounded(self, values, previous, elapsed):
        ranges = ((0., self.forward), (-self.lateral, self.lateral), (-self.angular, self.angular))
        if (not finite(elapsed) or elapsed <= 0 or len(values) != 3
                or any(not finite(value) or value < low - 8 * math.ulp(high)
                       or value > high + 8 * math.ulp(high)
                       for value, (low, high) in zip(values, ranges))):
            raise ValueError('controller_velocity_out_of_bounds')
        values = tuple(min(high, max(low, value)) for value, (low, high) in zip(values, ranges))
        for index, (old, new) in enumerate(zip(previous, values)):
            limit = (self.angular_acceleration if index == 2 else
                     self.acceleration if abs(new) > abs(old) else self.deceleration)
            if abs(new - old) > limit * elapsed + 8 * math.ulp(max(1., abs(old), abs(new))):
                raise ValueError('controller_acceleration_out_of_bounds')
        return values


@dataclass(frozen=True)
class ControllerCommand:
    ticket: Ticket
    identity: MapEvidence
    namespace: str
    sequence: int
    generated_at: float
    forward: float
    lateral: float
    angular: float

    def __post_init__(self):
        if (type(self.ticket) is not Ticket or type(self.identity) is not MapEvidence
                or self.namespace != controller_namespace(self.ticket)
                or type(self.sequence) is not int or not 0 < self.sequence < 2**53
                or not finite(self.generated_at) or self.generated_at <= 0
                or any(not finite(value) for value in (self.forward, self.lateral, self.angular))):
            raise ValueError('typed_generation_bound_controller_command_required')


class PhysicalTwistSource:
    """Unspun reader, fixed to a unique namespace for its entire lifetime."""
    def __init__(self, ticket, identity, *, context=None):
        from .velocity_source import HumbleTwistSource
        if type(identity) is not MapEvidence:
            raise ValueError('controller_map_identity_required')
        self.ticket, self.identity = ticket, identity
        self.namespace = controller_namespace(ticket)
        self.owner, self.sequence = threading.get_ident(), 0
        self.closed, self.quiescent = False, False
        self.publisher_gid = None
        self.reader = HumbleTwistSource(self.namespace, context=context)

    def _owned(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_controller_source_owner_required')

    def take(self):
        self._owned()
        if self.closed:
            return None
        publishers = self.reader.node.get_publishers_info_by_topic(self.namespace + '/observed_cmd_vel')
        if not publishers and self.publisher_gid is None:
            return None
        if (len(publishers) != 1 or publishers[0].node_name != 'velocity_smoother'
                or publishers[0].node_namespace != self.namespace
                or publishers[0].topic_type != 'geometry_msgs/msg/Twist'):
            raise ValueError('exclusive_generation_velocity_smoother_required')
        gid = publishers[0].endpoint_gid
        if (type(gid) not in (bytes, bytearray, list, tuple) or len(gid) != 24
                or any(type(value) is not int or not 0 <= value <= 255 for value in gid)
                or not any(gid) or (self.publisher_gid is not None and self.publisher_gid != bytes(gid))):
            raise ValueError('stable_controller_dds_publisher_required')
        # Humble Python exposes publication times, not each packet's writer GID.
        # This is graph continuity evidence, not DDS authentication. The owned
        # producer must still use a never-reused namespace for this exact ticket.
        self.publisher_gid = bytes(gid)
        sample = self.reader.take()
        if sample is None:
            return None
        message, generated = sample
        if any(getattr(vector, axis) != 0. for vector, axis in
               ((message.linear, 'z'), (message.angular, 'x'), (message.angular, 'y'))):
            raise ValueError('planar_controller_velocity_required')
        self.sequence += 1
        return ControllerCommand(self.ticket, self.identity, self.namespace, self.sequence,
                                 generated, message.linear.x, message.linear.y, message.angular.z)

    def close(self):
        self._owned()
        self.closed = True
        if not self.quiescent:
            self.reader.close()
            self.quiescent = True


class ControllerVelocityChannel:
    def __init__(self, output, limits, *, source_factory=PhysicalTwistSource, clock=time.monotonic):
        if type(limits) is not VelocityLimits or not callable(source_factory) or not callable(clock):
            raise ValueError('explicit_controller_source_and_limits_required')
        self.output, self.limits, self.factory, self.clock = output, limits, source_factory, clock
        self.owner, self.source = threading.get_ident(), None
        self.used_epochs = set()
        self.accepted_at = self.last_generated = None

    def _owned(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_controller_channel_owner_required')

    def ready(self, identity):
        self._owned()
        return identity == self.limits.identity and len(self.used_epochs) < 4096

    def arm(self, ticket, identity, namespace):
        self._owned()
        if (not self.ready(identity) or self.source is not None or ticket.epoch in self.used_epochs
                or namespace != controller_namespace(ticket)):
            raise RuntimeError('fresh_generation_owned_nav2_namespace_required')
        self.used_epochs.add(ticket.epoch)
        source = self.factory(ticket, identity)
        self.source = source
        try:
            if (type(source) is not PhysicalTwistSource or source.ticket != ticket
                    or source.identity != identity or source.namespace != namespace or source.closed):
                raise ValueError('generation_bound_physical_twist_source_required')
            if self.output.arm(ticket, identity) is not True:
                raise RuntimeError('physical_output_arm_unconfirmed')
        except BaseException:
            self.retire()
            raise
        self.accepted_at = self.last_generated = None

    def poll(self, generation, observation):
        self._owned()
        if self.source is None or self.source.closed:
            return
        if generation.ticket != self.source.ticket:
            raise RuntimeError('controller_action_generation_mismatch')
        if generation.retiring or generation.terminal or generation.accepted_at is None:
            return
        if self.accepted_at is None:
            self.accepted_at = generation.accepted_at
        origin = self.accepted_at if self.last_generated is None else self.last_generated
        timeout = self.limits.first_command_timeout if self.last_generated is None else self.limits.command_age
        try:
            # A new sample cannot revive an already-expired controller generation.
            if not 0 <= self.clock() - origin <= timeout:
                raise ValueError('controller_output_expired')
            command = self.source.take()
            if command is not None:
                if self.output.command(command, self.limits, observation) is not True:
                    raise ValueError('controller_command_not_accepted')
                self.last_generated = command.generated_at
        except Exception:
            self.output.fence_controller()
            raise

    def retire(self):
        self._owned()
        if self.source is not None:
            self.source.close()
            if not self.source.quiescent:
                raise RuntimeError('controller_source_retirement_unconfirmed')
            self.source = None

    def quiescent(self):
        self._owned()
        return self.source is None
