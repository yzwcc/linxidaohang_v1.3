"""Owned private ROS output for passive declared-frame localization terrain.

This is not metric admission or a motion interface. The existing sensor executor
owns the node/timer; the native worker and terrain child never run ROS callbacks.
"""
import secrets
import threading

from lingxi_navigation.occupancy import ClearanceProjection
from lingxi_navigation.terrain_lane import LaneSnapshot
from lingxi_navigation.terrain_ros import TerrainRosPublisher

MODE = 'private_declared_terrain'


class LiveTerrainRosOutput:
    def __init__(self, projection, snapshot, retire, *, observations=None):
        if type(projection) is not ClearanceProjection or not callable(snapshot) or not callable(retire):
            raise ValueError('explicit_passive_terrain_source_and_projection_required')
        self.projection, self.read_snapshot, self.retire_source = projection, snapshot, retire
        self.namespace = 'lingxi_offline_' + secrets.token_hex(16)
        self.saved_map = observations is not None
        if self.saved_map:
            from live_localization_ros import LiveLocalizationRosOutput
            if type(observations) is not LiveLocalizationRosOutput:
                raise ValueError('owned_saved_map_observations_required')
            self.namespace = observations.namespace
        self.owner = self.node = self.executor = self.publisher = None
        self.started = self.closed = self.failed = False
        self.revoked = False
        self.final_publications = 0

    def snapshot(self):
        if self.closed or self.revoked:
            return LaneSnapshot(0, 'retired', failure='ros_output_closed')
        return self.read_snapshot()

    def retire(self, reason=None):
        if reason is not None:
            self.failed = True
        if not self.revoked:
            self.revoked = True
            try:
                self.retire_source()
            except BaseException:
                self.failed = True
                raise

    def setup(self, sensor_node):
        if self.started or self.closed:
            raise RuntimeError('passive_ros_terrain_output_cannot_restart')
        self.owner, self.started = threading.get_ident(), True
        try:
            import rclpy
            self.executor = sensor_node.executor
            if self.executor is None:
                raise ValueError('existing_sensor_executor_required')
            self.node = rclpy.create_node('declared_terrain', namespace=self.namespace,
                context=sensor_node.context, use_global_arguments=False,
                enable_rosout=False, start_parameter_services=False)
            options = dict(saved_map=True) if self.saved_map else {}
            self.publisher = TerrainRosPublisher(self.node, self, self.projection, **options)
            if self.executor.add_node(self.node) is False:
                raise RuntimeError('terrain_ros_node_not_attached')
            return self.close
        except BaseException:
            self.failed = True
            self.close()
            raise

    def close(self):
        if self.owner is not None and threading.get_ident() != self.owner:
            raise RuntimeError('same_sensor_executor_terrain_cleanup_required')
        if self.closed:
            return
        failed = False
        try:
            self.retire()
        except BaseException:
            failed = True
        if self.publisher is not None:
            try:
                # Revoke first, then publish unknown before destroying the timer/node.
                self.publisher.close()
            except BaseException:
                failed = True
            self.final_publications = self.publisher.publications
        if self.node is not None:
            for cleanup in (lambda: self.executor.remove_node(self.node), self.node.destroy_node):
                try:
                    if cleanup() is False:
                        failed = True
                except BaseException:
                    failed = True
        self.closed = True
        self.failed = self.failed or failed
        if failed:
            raise RuntimeError('passive_ros_terrain_cleanup_failed')

    def summary(self):
        return dict(mode='private_saved_map_terrain' if self.saved_map else MODE,
            started=self.started, closed=self.closed, failed=self.failed,
            publications=self.publisher.publications if self.publisher is not None else self.final_publications,
            physicalScaleValidated=False, groundSupportValidated=False,
            physicalMotionSent=False, navigationReady=False)


def setup_localization_ros(node, base_owner=None, terrain_owner=None, observation_owner=None):
    """Compose the base-TF reader and optional ROS outputs atomically."""
    cleanups = []

    def close():
        failed = False
        while cleanups:
            try:
                cleanups.pop()()
            except BaseException:
                failed = True
        if failed:
            raise RuntimeError('localization_ros_cleanup_failed')

    try:
        for owner in (base_owner, terrain_owner, observation_owner):
            if owner is not None:
                cleanup = owner.setup(node)
                if not callable(cleanup):
                    raise ValueError('owned_localization_ros_cleanup_required')
                cleanups.append(cleanup)
        return close
    except BaseException:
        close()
        raise
