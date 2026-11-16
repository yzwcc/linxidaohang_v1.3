"""Steady-clock full replacement publisher for declared-frame offline terrain.

No velocity interface and no metric admission. The independent command fence
is still required: changing a ROS map is not an acknowledgement of a stop.
"""
import threading
import time

from .navfn import finite
from .occupancy import ClearanceProjection
from .terrain_feed import ExpiringTerrainFeed
from .runtime_fault import record_fault
from .terrain import SAVED_MAP_TERRAIN_MAXIMUM_AGE_NS


class TerrainRosPublisher:
    def __init__(self, node, lane, projection, *, period_seconds=.05, saved_map=False):
        if (type(projection) is not ClearanceProjection or not finite(period_seconds)
                or not .01 <= period_seconds <= .2 or type(saved_map) is not bool):
            raise ValueError('bounded_full_terrain_publication_required')
        from rclpy.clock import Clock, ClockType
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from nav_msgs.msg import OccupancyGrid
        self.owner, self.node, self.lane = threading.get_ident(), node, lane
        self.projection, self.message_type = projection, OccupancyGrid
        namespace = node.get_namespace().strip('/')
        if (not namespace.startswith('lingxi_offline_') or len(namespace) != 47
                or any(char not in '0123456789abcdef' for char in namespace[15:])):
            raise ValueError('isolated_offline_terrain_namespace_required')
        self.saved_map = saved_map
        self.frame_id, self.feed, self.last_grid = 'lingxi_saved_map' if saved_map else namespace + '_map', None, None
        self.closed, self.publications, self.last_failure = False, 0, 'no_terrain'
        self.last_diagnostic = None
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.publisher = node.create_publisher(OccupancyGrid, 'traversable_map', qos)
        self.steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self.timer = node.create_timer(period_seconds, self.publish, clock=self.steady_clock)
        self.publish()

    def publish(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_ros_terrain_owner_required')
        if self.closed:
            return
        snapshot = self.lane.snapshot()
        if self.feed is None and snapshot.session is not None:
            self.feed = ExpiringTerrainFeed(self.projection, snapshot.session)
        if self.feed is not None:
            if snapshot.grid is None:
                self.feed.revoke(snapshot.failure or 'terrain_unavailable')
                self.last_grid = None
            elif snapshot.grid is not self.last_grid:
                try:
                    self.feed.replace(snapshot.grid)
                    self.last_grid = snapshot.grid
                except (ValueError, TimeoutError):
                    self.lane.retire('invalid_ros_terrain_replacement')
                    self.feed.revoke('invalid_ros_terrain_replacement')
                    self.last_grid = None
            costs = self.feed.offline_occupancy_values()
            self.last_failure = self.feed.failure
        else:
            costs = (-1,) * (self.projection.width * self.projection.height)
            self.last_failure = snapshot.failure or snapshot.state
        spec = self.projection
        msg = self.message_type()
        msg.header.frame_id = self.frame_id
        now = self.node.get_clock().now().nanoseconds
        stamp = now
        if self.saved_map and snapshot.grid is not None and self.last_failure is None:
            grid = snapshot.grid
            if (grid.coordinate_frame == self.frame_id
                    and type(grid.oldest_scan_ns) is int and type(grid.latest_scan_ns) is int
                    and 0 <= now - grid.oldest_scan_ns <= SAVED_MAP_TERRAIN_MAXIMUM_AGE_NS
                    and 0 <= now - grid.latest_scan_ns <= SAVED_MAP_TERRAIN_MAXIMUM_AGE_NS):
                stamp = grid.latest_scan_ns
            else:
                costs = (-1,) * (spec.width * spec.height)
                self.last_failure = 'original_terrain_scan_time_unavailable'
        msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(stamp, 10**9)
        msg.info.width, msg.info.height, msg.info.resolution = spec.width, spec.height, spec.resolution_metres
        msg.info.origin.position.x, msg.info.origin.position.y = spec.origin_x_metres, spec.origin_y_metres
        msg.info.origin.orientation.w = 1.
        msg.data = costs
        self.publisher.publish(msg)
        self.publications += 1
        # Bounded private diagnostics distinguish missing/expired results from
        # a current grid whose measured ground support remains unknown.
        monotonic_now = time.monotonic()
        if self.last_diagnostic is None or monotonic_now - self.last_diagnostic >= 5.:
            self.last_diagnostic = monotonic_now
            diagnostics = dict(free_cells=costs.count(0), occupied_cells=costs.count(100),
                               unknown_cells=costs.count(-1))
            if snapshot.grid is not None:
                grid = snapshot.grid
                diagnostics['grid_remaining_ms'] = (grid.expires_at - monotonic_now) * 1000
                for key, scan_ns in (('oldest_scan_age_ms', grid.oldest_scan_ns),
                                     ('latest_scan_age_ms', grid.latest_scan_ns)):
                    if type(scan_ns) is int:
                        diagnostics[key] = (now - scan_ns) / 1e6
            record_fault('terrain_ros_output', code=self.last_failure or 'current_terrain', diagnostics=diagnostics)

    def close(self):
        self.lane.retire()
        self.publish()
        self.closed = True
        self.node.destroy_timer(self.timer)
        self.node.destroy_publisher(self.publisher)
