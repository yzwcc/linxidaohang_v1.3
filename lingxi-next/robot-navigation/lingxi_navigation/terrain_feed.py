"""ROS-neutral full replacement with monotonic expiry and explicit revocation.

The ROS owner samples this on every publication timer, including idle periods.
Unknown replacement does not replace the independent command/stop watchdog.
This declared-frame feed is only for shadow/offline work, never metric admission.
"""
import threading
import time

from .navfn import finite
from .occupancy import ClearanceProjection
from .terrain_process import TerrainGrid


class ExpiringTerrainFeed:
    def __init__(self, projection, session, *, clock=time.monotonic):
        if (type(projection) is not ClearanceProjection or type(session) is not str or len(session) != 32
                or any(char not in '0123456789abcdef' for char in session) or not callable(clock)):
            raise ValueError('session_bound_terrain_feed_required')
        self.projection, self.session, self.clock = projection, session, clock
        self.owner, self.last_clock, self.sequence, self.revision = threading.get_ident(), None, -1, -1
        self.grid, self.failure, self.epoch = None, 'no_terrain', None
        self.unknown = bytes([255]) * (projection.width * projection.height)

    def _now(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_terrain_feed_owner_required')
        now = self.clock()
        if not finite(now) or now < 0 or self.last_clock is not None and now < self.last_clock:
            self.grid, self.failure = None, 'invalid_clock'
            raise ValueError('monotonic_terrain_feed_clock_required')
        self.last_clock = now
        return now

    def replace(self, grid):
        now = self._now()
        self.grid, self.failure = None, 'invalid_replacement'
        if (type(grid) is not TerrainGrid or grid.session != self.session or grid.projection != self.projection
                or grid.sequence <= self.sequence or grid.map_revision < self.revision
                or self.epoch is not None and grid.epoch_sha256 != self.epoch):
            raise ValueError('current_ordered_full_terrain_replacement_required')
        self.sequence, self.revision = grid.sequence, grid.map_revision
        self.epoch = grid.epoch_sha256
        if not grid.received_at <= now < grid.expires_at:
            raise TimeoutError('terrain_replacement_expired_or_future')
        self.grid, self.failure = grid, None

    def revoke(self, reason='source_revoked'):
        self._now()
        self.grid, self.failure = None, reason

    def sample(self):
        now = self._now()
        if self.grid is not None and now >= self.grid.expires_at:
            self.grid, self.failure = None, 'terrain_expired'
        return self.grid.costs if self.grid is not None else self.unknown

    def offline_occupancy_values(self):
        return tuple({0: 0, 254: 100, 255: -1}[value] for value in self.sample())
