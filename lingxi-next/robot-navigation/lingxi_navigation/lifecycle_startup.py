"""Bounded official Nav2 startup; no motion, retry or standalone timeout owner."""
from .navfn import finite


class LifecycleStartup:
    """Confirm discovery round trips before one official manager startup request.

    This mitigates bringup ordering, not FastDDS's underlying discovery issue.
    The owning runtime retains its startup deadline and all admission checks.
    """
    names = ('planner_server', 'controller_server', 'bt_navigator', 'velocity_smoother')

    def __init__(self, clients, manager, state_request, startup_request, *, maximum_age=.6):
        if len(clients) != 4 or not finite(maximum_age) or not .1 <= maximum_age <= 1.:
            raise ValueError('four_managed_nodes_and_bounded_lifecycle_age_required')
        self.clients, self.manager = clients, manager
        self.state_request, self.startup_request = state_request, startup_request
        self.maximum_age, self.closed = maximum_age, False
        self.futures, self.states, self.rounds = [None] * 4, [None] * 4, [0] * 4
        self.observed, self.issued = [None] * 4, [None] * 4
        self.last_poll = None
        self.next_poll, self.startup, self.sent, self.confirmed = 0., None, False, False
        self.failure = None

    def poll(self, now):
        if self.closed or self.failure is not None:
            return
        if not finite(now) or (self.last_poll is not None and now < self.last_poll):
            self.failure = 'lifecycle_clock_invalid'
            return
        self.last_poll = now
        try:
            if self.startup is not None and self.startup.done():
                if self.startup.result().success is not True:
                    raise RuntimeError('lifecycle_startup_refused')
                self.confirmed, self.startup = True, None
            for i, pending in enumerate(self.futures):
                if pending is not None and pending.done():
                    state = pending.result().current_state.id
                    if type(state) is not int or state not in (1, 2, 3):
                        raise RuntimeError('lifecycle_state_invalid')
                    issued = self.issued[i]
                    self.futures[i], self.issued[i] = None, None
                    # A delayed reply cannot renew evidence after the query's age limit.
                    if issued is None or not 0 <= now - issued <= self.maximum_age:
                        self.states[i], self.observed[i] = None, None
                        continue
                    self.states[i] = state
                    self.observed[i] = issued
                    self.rounds[i] += 1
            if (not self.sent and self.states == [1] * 4 and min(self.rounds) >= 2
                    and self.current(now) and self.manager.service_is_ready()):
                # Never resend an uncertain non-idempotent STARTUP request.
                self.sent = True
                self.startup = self.manager.call_async(self.startup_request())
            if now >= self.next_poll:
                for i, client in enumerate(self.clients):
                    if self.futures[i] is None and client.service_is_ready():
                        self.issued[i] = now
                        self.futures[i] = client.call_async(self.state_request())
                self.next_poll = now + .2
        except Exception as error:
            self.failure = str(error) if isinstance(error, RuntimeError) else 'lifecycle_service_failed'

    def current(self, now):
        return (not self.closed and finite(now) and self.last_poll is not None and now >= self.last_poll
                and all(observed is not None and 0 <= now - observed <= self.maximum_age
                        and client.service_is_ready()
                        for observed, client in zip(self.observed, self.clients)))

    def ready(self, now):
        return self.failure is None and self.confirmed and self.states == [3] * 4 and self.current(now)

    def close(self):
        self.closed = True
        for pending in (*self.futures, self.startup):
            if pending is not None:
                pending.cancel()
        self.futures, self.startup = [None] * 4, None
        self.issued = [None] * 4
