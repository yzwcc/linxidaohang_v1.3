"""Steady-clock full replacement publisher for declared-frame offline terrain.

No velocity interface and no metric admission. The independent command fence
is still required: changing a ROS map is not an acknowledgement of a stop.
"""
import threading

from .navfn import finite
from .occupancy import ClearanceProjection
from .terrain_feed import ExpiringTerrainFeed


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
                    and 0 <= now - grid.oldest_scan_ns <= 300_000_000
                    and 0 <= now - grid.latest_scan_ns <= 300_000_000):
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

    def close(self):
        self.lane.retire()
        self.publish()
        self.closed = True
        self.node.destroy_timer(self.timer)
        self.node.destroy_publisher(self.publisher)
