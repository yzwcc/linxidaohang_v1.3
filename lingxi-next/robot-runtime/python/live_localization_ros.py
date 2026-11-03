"""Original-scan passive ROS observations, not physical calibration or admission."""
from dataclasses import dataclass
import secrets
import threading

import numpy as np
from scipy.spatial.transform import Rotation

from lingxi_slam.base_pose import timestamp, metric_map_pose
from lingxi_slam.localization_session import LocalizationSession
from lingxi_slam.registered_acquisition import RegisteredAcquisition
from lingxi_slam.seeded_localization import rigid_pose

MODE = 'private_saved_map_observations'
MAP, BASE, SENSOR = 'lingxi_saved_map', 'base_link', 'lidar_chest_front'


@dataclass(frozen=True)
class ScanObservation:
    generation: int
    frame: int
    stamp_ns: int
    received_at: float
    map_from_base: np.ndarray
    base_from_sensor: np.ndarray
    sensor_points: np.ndarray
    linear: tuple
    angular: tuple


class LiveLocalizationRosOutput:
    """One native producer and the existing ROS executor; a single-slot mailbox.

    Acquisition arrives before SensorWorker's timed base projection. Capture is
    called after that projection, on the same worker, to join exactly one scan.
    A second measured pose is required for finite-difference twist. No zero
    velocity, latest-TF or reconstructed epoch-time fallback is supplied.
    """
    def __init__(self, session):
        if type(session) is not LocalizationSession or session.binding.input_mode != 'live' or session.base_scale is None:
            raise ValueError('explicit_live_scaled_localization_required')
        self.session, self.lock = session, threading.Lock()
        self.namespace = 'lingxi_offline_' + secrets.token_hex(16)
        self.pending = self.previous = self.sample = self.generation = None
        self.producer = self.owner = self.node = self.executor = self.timer = None
        self.publishers = []
        self.closed = self.revoked = self.started = self.failed = False
        self.publications, self.last_published = 0, 0

    def _producer(self):
        if self.producer is None:
            self.producer = threading.get_ident()
        if threading.get_ident() != self.producer or self.producer != self.session.owner:
            raise RuntimeError('same_native_localization_observation_owner_required')

    def _tracking(self, state):
        return (state['state'] == 'tracking' and not state['requestPending']
            and state['workerReady'] and not state['workerClosed']
            and (self.generation is None or self.generation == state['generation']))

    def offer(self, packet):
        self._producer()
        if type(packet) is not RegisteredAcquisition or packet.binding != self.session.binding:
            raise ValueError('same_session_registered_scan_required')
        with self.lock:
            if not self.closed and not self.revoked:
                self.pending = packet

    def capture(self, header, algorithm):
        self._producer()
        state = self.session.state()
        with self.lock:
            packet, self.pending = self.pending, None
            if self.closed or self.revoked:
                return
            self.sample = None
            if not self._tracking(state):
                self.previous = None
                if self.generation is not None:
                    self.revoked = True
                return
            base = state.get('basePose', {})
            if (packet is None or packet.generation != state['generation']
                    or packet.native_frame_id != state['lastFrame']
                    or packet.algorithm_stamp != algorithm or base.get('status') != 'available'
                    or base.get('lastFrame') != packet.native_frame_id or base.get('scanHeaderNs') != str(header)
                    or base.get('metresPerMapUnit') != self.session.base_scale
                    or not np.array_equal(base.get('navigationFromRaw'), self.session.base_navigation_from_raw)
                    or base.get('coordinateFrame') != MAP or base.get('bodyFrame') != BASE):
                self.previous = None
                return
            stamp = timestamp(int(base['effectiveScanNs']))
            now = self.session.clock()
            if not 0 <= now - packet.received_at <= .3:
                self.previous = None
                return
            raw_pose = rigid_pose(packet.map_lidar_pose)
            if not np.allclose(raw_pose, state['mapFromSensor'], rtol=0, atol=1e-8):
                raise ValueError('same_frame_map_sensor_pose_required')
            map_base = rigid_pose(np.asarray(base['mapFromBase'], dtype=np.float64))
            map_sensor = metric_map_pose(raw_pose, self.session.base_scale, self.session.base_navigation_from_raw)
            base_sensor = rigid_pose(np.linalg.inv(map_base) @ map_sensor)
            points = (packet.endpoints - raw_pose[:3, 3]) @ raw_pose[:3, :3] * self.session.base_scale
            if points.ndim != 2 or points.shape[1] != 3 or not 1 <= len(points) <= 65536 or not np.isfinite(points).all():
                raise ValueError('bounded_finite_same_scan_cloud_required')
            previous = self.previous
            if previous is not None and (packet.native_frame_id <= previous[0] or stamp <= previous[1]):
                self.revoked = True
                self.previous = None
                raise ValueError('increasing_original_scan_observations_required')
            self.generation = packet.generation
            self.previous = packet.native_frame_id, stamp, map_base.copy()
            if previous is None or not 0 < (stamp - previous[1]) / 1e9 <= .3:
                return
            dt = (stamp - previous[1]) / 1e9
            linear = map_base[:3, :3].T @ ((map_base[:3, 3] - previous[2][:3, 3]) / dt)
            angular = Rotation.from_matrix(map_base[:3, :3].T @ previous[2][:3, :3]).as_rotvec() / -dt
            if not np.isfinite(linear).all() or not np.isfinite(angular).all():
                raise ValueError('finite_measured_pose_derivative_required')
            for value in (map_base, base_sensor, points):
                value.flags.writeable = False
            self.sample = ScanObservation(packet.generation, packet.native_frame_id, stamp, packet.received_at,
                map_base, base_sensor, points, tuple(linear), tuple(angular))

    def snapshot(self):
        state, now = self.session.state(), self.session.clock()
        with self.lock:
            if self.closed or self.revoked:
                return None
            if self.generation is not None and not self._tracking(state):
                self.revoked = True
            if self.revoked or self.sample is None or not 0 <= now - self.sample.received_at <= .3:
                self.sample = None
            return self.sample

    def retire(self):
        with self.lock:
            self.revoked = True
            self.pending = self.previous = self.sample = None

    def setup(self, sensor_node):
        if self.started or self.closed:
            raise RuntimeError('localization_ros_output_cannot_restart')
        self.owner, self.started = threading.get_ident(), True
        try:
            import rclpy
            from rclpy.clock import Clock, ClockType
            from rclpy.qos import QoSProfile, ReliabilityPolicy
            from nav_msgs.msg import Odometry
            from sensor_msgs.msg import PointCloud2
            from tf2_msgs.msg import TFMessage
            self.executor = sensor_node.executor
            if self.executor is None:
                raise ValueError('existing_sensor_executor_required')
            self.node = rclpy.create_node('saved_map_observations', namespace=self.namespace,
                context=sensor_node.context, use_global_arguments=False, enable_rosout=False, start_parameter_services=False)
            qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)
            for message, topic in ((Odometry, 'odometry'), (PointCloud2, 'points'), (TFMessage, 'tf')):
                self.publishers.append(self.node.create_publisher(message, topic, qos))
            self.steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
            self.timer = self.node.create_timer(.02, self.publish, clock=self.steady_clock)
            if self.executor.add_node(self.node) is False:
                raise RuntimeError('localization_ros_node_not_attached')
            return self.close
        except BaseException:
            self.failed = True
            self.close()
            raise

    def publish(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('same_sensor_executor_observation_publish_required')
        sample = self.snapshot()
        if sample is None or self.closed or sample.frame <= self.last_published:
            return
        now = self.node.get_clock().now().nanoseconds
        if not 0 <= now - sample.stamp_ns <= 300_000_000:
            return
        from geometry_msgs.msg import TransformStamped
        from nav_msgs.msg import Odometry
        from sensor_msgs.msg import PointCloud2, PointField
        from tf2_msgs.msg import TFMessage
        def header(message, frame):
            message.header.frame_id = frame
            message.header.stamp.sec, message.header.stamp.nanosec = divmod(sample.stamp_ns, 10**9)
        def pose(matrix, position, rotation):
            for key, value in zip(('x', 'y', 'z'), matrix[:3, 3]):
                setattr(position, key, float(value))
            for key, value in zip(('x', 'y', 'z', 'w'), Rotation.from_matrix(matrix[:3, :3].copy()).as_quat()):
                setattr(rotation, key, float(value))
        odom = Odometry()
        header(odom, MAP)
        odom.child_frame_id = BASE
        pose(sample.map_from_base, odom.pose.pose.position, odom.pose.pose.orientation)
        for name, values in (('linear', sample.linear), ('angular', sample.angular)):
            for key, value in zip(('x', 'y', 'z'), values):
                setattr(getattr(odom.twist.twist, name), key, float(value))
        # Quality is diagnostic, not perfect certainty. These are not calibrated covariances.
        odom.pose.covariance = odom.twist.covariance = [1e6 if i % 7 == 0 else 0. for i in range(36)]
        transforms = []
        for parent, child, matrix in ((MAP, BASE, sample.map_from_base), (BASE, SENSOR, sample.base_from_sensor)):
            value = TransformStamped()
            header(value, parent)
            value.child_frame_id = child
            pose(matrix, value.transform.translation, value.transform.rotation)
            transforms.append(value)
        cloud = PointCloud2()
        header(cloud, SENSOR)
        cloud.height, cloud.width = 1, len(sample.sensor_points)
        cloud.fields = [PointField(name=name, offset=index * 8, datatype=PointField.FLOAT64, count=1)
                        for index, name in enumerate(('x', 'y', 'z'))]
        cloud.point_step, cloud.row_step = 24, 24 * cloud.width
        cloud.is_bigendian, cloud.is_dense = False, True
        cloud.data = np.asarray(sample.sensor_points, dtype='<f8').tobytes()
        # Recheck after serialization; no old scan is restamped to the timer time.
        if self.snapshot() is not sample or not 0 <= self.node.get_clock().now().nanoseconds - sample.stamp_ns <= 300_000_000:
            return
        for publisher, message in zip(self.publishers, (odom, cloud, TFMessage(transforms=transforms))):
            publisher.publish(message)
        self.last_published = sample.frame
        self.publications += 1

    def close(self):
        if self.owner is not None and threading.get_ident() != self.owner:
            raise RuntimeError('same_sensor_executor_observation_cleanup_required')
        if self.closed:
            return
        self.retire()
        self.closed = True
        cleanups = []
        if self.node is not None:
            if self.timer is not None:
                cleanups.append(lambda: self.node.destroy_timer(self.timer))
            cleanups.extend(lambda p=p: self.node.destroy_publisher(p) for p in self.publishers)
            cleanups.extend((lambda: self.executor.remove_node(self.node), self.node.destroy_node))
        failed = False
        for cleanup in cleanups:
            try:
                if cleanup() is False:
                    failed = True
            except BaseException:
                failed = True
        self.failed = self.failed or failed
        if failed:
            raise RuntimeError('localization_ros_output_cleanup_failed')

    def summary(self):
        return dict(mode=MODE, started=self.started, closed=self.closed, failed=self.failed,
            publications=self.publications, namespace=self.namespace,
            physicalScaleValidated=False, physicalMotionSent=False, navigationReady=False)
