"""Real Humble NavigateToPose client; deliberately no velocity publisher.

action_retired() is only ROS action evidence. A separate output owner must fence
and confirm its generation before the product bridge acknowledges a stop.
"""
from collections import deque
from dataclasses import dataclass
import math
import threading
import time

from .physical_bridge import Ticket, pose
from .navfn import finite


@dataclass
class _Generation:
    ticket: Ticket
    accepted_at: float | None = None
    retiring: bool = False
    terminal: bool = False
    uncertain: bool = False
    handle: object = None
    send: object = None
    result: object = None
    cancel: object = None


class Nav2ActionDriver:
    def __init__(self, node, map_frame, *, clock=time.monotonic, lifecycle_max_age=.6):
        import re
        from rclpy.action import ActionClient
        from nav2_msgs.action import NavigateToPose
        from lifecycle_msgs.srv import GetState
        if (type(map_frame) is not str or not re.fullmatch('[A-Za-z][A-Za-z0-9_/]{0,119}', map_frame)
                or not finite(lifecycle_max_age) or not .1 <= lifecycle_max_age <= 1.):
            raise ValueError('explicit_map_frame_and_bounded_lifecycle_age_required')
        self.node, self.map_frame, self.clock = node, map_frame, clock
        self.owner, self.lifecycle_max_age = threading.get_ident(), lifecycle_max_age
        self.closed, self.generation, self.pending_events = False, None, deque()
        self.next_lifecycle = 0.
        self.lifecycle = {}
        self.clients = {}
        self.pending_states = {}
        self.action_type, self.state_type = NavigateToPose, GetState
        self.client = ActionClient(node, NavigateToPose, 'navigate_to_pose')
        try:
            for name in ('planner_server', 'controller_server', 'bt_navigator', 'velocity_smoother'):
                self.clients[name] = node.create_client(GetState, name + '/get_state')
        except BaseException:
            self.close()
            raise

    def _owned(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('same_ros_action_executor_owner_required')

    def _event(self, generation, kind):
        self._owned()
        if self.closed:
            return
        if len(self.pending_events) >= 32:
            generation.uncertain = True
            self.pending_events.clear()
            kind = 'unconfirmed'
        self.pending_events.append((generation.ticket, kind))

    def start(self, ticket, goal):
        self._owned()
        if self.closed or type(ticket) is not Ticket or (self.generation is not None
                and not self.action_retired(self.generation.ticket)):
            raise RuntimeError('previous_nav2_action_retirement_required')
        goal = pose(goal)
        if not self.ready():
            raise RuntimeError('current_nav2_lifecycle_and_server_required')
        message = self.action_type.Goal()
        message.pose.header.frame_id = self.map_frame
        message.pose.header.stamp = self.node.get_clock().now().to_msg()
        message.pose.pose.position.x, message.pose.pose.position.y = goal['x'], goal['y']
        message.pose.pose.orientation.z = math.sin(goal['yaw'] / 2)
        message.pose.pose.orientation.w = math.cos(goal['yaw'] / 2)
        generation = self.generation = _Generation(ticket)
        try:
            generation.send = self.client.send_goal_async(message)
            generation.send.add_done_callback(lambda future: self._accepted(generation, future))
        except Exception:
            generation.uncertain = True
            self._event(generation, 'unconfirmed')
            raise

    def _accepted(self, generation, future):
        self._owned()
        if self.closed:
            return
        try:
            generation.handle = future.result()
            if not generation.handle.accepted:
                generation.terminal = True
                self._event(generation, 'failed')
                return
            generation.result = generation.handle.get_result_async()
            generation.result.add_done_callback(lambda result: self._finished(generation, result))
            if generation.retiring:
                self._cancel_handle(generation)
            else:
                generation.accepted_at = self.clock()
                self._event(generation, 'accepted')
        except Exception:
            generation.uncertain = True
            self._event(generation, 'unconfirmed')

    def _finished(self, generation, future):
        self._owned()
        if self.closed:
            return
        try:
            status = future.result().status
            if type(status) is not int or status not in (4, 5, 6):
                raise ValueError('terminal_nav2_goal_status_required')
            generation.terminal = True
            self._event(generation, {4: 'succeeded', 5: 'canceled', 6: 'failed'}[status])
        except Exception:
            generation.uncertain = True
            self._event(generation, 'unconfirmed')

    def _cancel_handle(self, generation):
        if generation.cancel is not None or generation.terminal:
            return
        try:
            generation.cancel = generation.handle.cancel_goal_async()
            generation.cancel.add_done_callback(lambda future: self._cancelled(generation, future))
        except Exception:
            generation.uncertain = True
            self._event(generation, 'unconfirmed')

    def _cancelled(self, generation, future):
        self._owned()
        if self.closed:
            return
        try:
            # A cancellation ACK is not a terminal result or an output receipt.
            future.result()
        except Exception:
            generation.uncertain = True
            self._event(generation, 'unconfirmed')

    def cancel(self, ticket):
        self._owned()
        generation = self.generation
        if generation is None:
            if ticket is not None:
                raise RuntimeError('nav2_action_ticket_mismatch')
            return
        if ticket != generation.ticket:
            raise RuntimeError('nav2_action_ticket_mismatch')
        generation.retiring = True
        if generation.handle is not None and generation.handle.accepted:
            self._cancel_handle(generation)

    def action_retired(self, ticket):
        self._owned()
        generation = self.generation
        if generation is None:
            return ticket is None
        return (ticket == generation.ticket and generation.terminal and not generation.uncertain
                and generation.send is not None and generation.send.done()
                and (generation.cancel is None or generation.cancel.done()))

    def poll(self):
        self._owned()
        if self.closed:
            return
        now = self.clock()
        for name, future in tuple(self.pending_states.items()):
            if future.done():
                try:
                    self.lifecycle[name] = (future.result().current_state.id == 3, now)
                except Exception:
                    self.lifecycle.pop(name, None)
                del self.pending_states[name]
        if now >= self.next_lifecycle:
            for name, client in self.clients.items():
                if name not in self.pending_states and client.service_is_ready():
                    self.pending_states[name] = client.call_async(self.state_type.Request())
            self.next_lifecycle = now + .2

    def ready(self):
        self._owned()
        return (not self.closed and self.client.server_is_ready() and len(self.lifecycle) == 4
                and all(active and 0 <= self.clock() - observed <= self.lifecycle_max_age
                        for active, observed in self.lifecycle.values()))

    def events(self):
        self._owned()
        events = tuple(self.pending_events)
        self.pending_events.clear()
        return events

    def close(self):
        self._owned()
        if self.closed:
            return
        if self.generation is not None:
            self.cancel(self.generation.ticket)
        self.closed = True
        self.pending_events.clear()
        for future in self.pending_states.values():
            future.cancel()
        self.pending_states.clear()
        for client in self.clients.values():
            self.node.destroy_client(client)
        self.clients.clear()
        self.client.destroy()


class RosNavigationBackend:
    """Composition point; no synthetic evidence or permissive output fallback.

    sensors.observe() retains sensor acquisition age and map binding. output.arm
    must confirm exclusive generation ownership before an action is submitted;
    output.retirement must be a measured, matching nonce-bound Retirement.
    activate selects a verified map and updates the sensor/output owners together.
    The caller's existing single-threaded ROS executor owns tick/spinning.
    """
    def __init__(self, driver, sensors, output, activate, spin, *, controller=None, validate_observation=None):
        from .physical_velocity import ControllerVelocityChannel
        if type(driver) is not Nav2ActionDriver or not callable(activate) or not callable(spin):
            raise ValueError('owned_ros_action_evidence_and_output_sources_required')
        if controller is not None and (type(controller) is not ControllerVelocityChannel or controller.output is not output):
            raise ValueError('owned_controller_velocity_channel_required')
        self.driver, self.sensors, self.output = driver, sensors, output
        self.activate, self.spin = activate, spin
        self.controller = controller
        if validate_observation is not None and not callable(validate_observation):
            raise ValueError('callable_localization_observation_veto_required')
        self.validate_observation = validate_observation
        self.retiring_ticket = None

    def tick(self):
        self.spin()
        self.output.poll()
        self.driver.poll()
        if self.controller is not None and self.driver.generation is not None:
            self.controller.poll(self.driver.generation, self.observe())

    def observe(self):
        from .physical_bridge import Observation
        value = self.sensors.observe()
        if type(value) is not Observation:
            raise ValueError('current_sensor_observation_required')
        checks = dict(value.checks)
        if self.validate_observation is not None:
            checks['localization'] = (checks['localization']
                and self.validate_observation(self.sensors, value) is True)
        checks['nav2Lifecycle'] = self.driver.ready()
        # Sensor evidence cannot vouch for the independent AIMDK output owner.
        # Readiness is granted only by its current, nonce-fenced feedback.
        checks['velocityOutput'] = self.output.ready(value.identity)
        from .physical_ros import AimdkVelocityOutput
        if isinstance(self.output, AimdkVelocityOutput):
            checks['velocityOutput'] = (checks['velocityOutput'] and self.controller is not None
                                        and self.controller.ready(value.identity))
        sent = self.output.physical_motion_sent
        if type(sent) is not bool:
            raise ValueError('typed_output_motion_receipt_required')
        return Observation(value.identity, value.observed_at, value.pose, checks, sent)

    def events(self):
        return self.driver.events()

    def start(self, ticket, goal):
        current = self.driver.generation
        if current is not None and not self.driver.action_retired(current.ticket):
            raise RuntimeError('previous_nav2_action_retirement_required')
        value = self.observe()
        if not all(value.checks.values()):
            raise RuntimeError('exclusive_output_generation_not_confirmed')
        if self.controller is not None:
            self.controller.arm(ticket, value.identity, self.driver.node.get_namespace())
        elif self.output.arm(ticket, value.identity) is not True:
            raise RuntimeError('exclusive_output_generation_not_confirmed')
        self.driver.start(ticket, goal)

    def retire(self, ticket, nonce):
        self.retiring_ticket = self.driver.generation.ticket if self.driver.generation is not None else None
        try:
            try:
                if self.controller is not None:
                    self.controller.retire()
            finally:
                self.output.retire(ticket, nonce)
        finally:
            self.driver.cancel(self.retiring_ticket)

    def retirement(self, nonce):
        if (not self.driver.action_retired(self.retiring_ticket)
                or (self.controller is not None and not self.controller.quiescent())):
            return None
        return self.output.complete_retirement(nonce)

    def activate_map(self, map_id, initial_pose):
        return self.activate(map_id, initial_pose)
