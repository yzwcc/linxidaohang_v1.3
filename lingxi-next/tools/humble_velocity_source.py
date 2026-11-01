"""Humble-specific, unspun Twist reader retaining DDS publication timestamps.

DDS write time is NOT controller computation time or a physical-motion receipt.
The installed Humble executor discards take_message metadata; this dedicated
node must never be added to an executor. Only the generation owner polls it.
"""
import os
import time


class SourceTimeError(ValueError):
    pass


class PublicationClock:
    def __init__(self, *, wall=time.time_ns, monotonic=time.monotonic_ns,
                 maximum_age_ns=150_000_000, clock_tolerance_ns=5_000_000):
        if (type(maximum_age_ns) is not int or not 0 < maximum_age_ns <= 1_000_000_000
                or type(clock_tolerance_ns) is not int or not 0 < clock_tolerance_ns <= 5_000_000):
            raise ValueError('bounded_publication_clock_required')
        self.wall, self.monotonic = wall, monotonic
        self.maximum_age_ns, self.tolerance = maximum_age_ns, clock_tolerance_ns
        self.anchor_wall, self.anchor_mono = self.sample()
        self.previous_source = None
        self.failure = None
        self.accepted, self.maximum_age_observed_ns = 0, 0

    def sample(self):
        before = self.monotonic()
        wall = self.wall()
        after = self.monotonic()
        if (any(type(value) is not int or value <= 0 for value in (before, wall, after))
                or not 0 <= after - before <= self.tolerance):
            raise SourceTimeError('clock_sample_invalid')
        return wall, before

    def convert(self, metadata):
        if self.failure:
            raise SourceTimeError(self.failure)
        try:
            wall, mono = self.sample()
            if (mono < self.anchor_mono or abs((wall - self.anchor_wall)
                    - (mono - self.anchor_mono)) > self.tolerance):
                raise SourceTimeError('publication_clock_discontinuity')
            if type(metadata) is not dict:
                raise SourceTimeError('dds_publication_metadata_required')
            source = metadata.get('source_timestamp')
            if type(source) is not int or not 0 < source < 2**63:
                raise SourceTimeError('dds_publication_timestamp_required')
            if source < self.anchor_wall:
                raise SourceTimeError('dds_publication_predates_generation')
            if self.previous_source is not None and source <= self.previous_source:
                raise SourceTimeError('dds_publication_replayed')
            age = wall - source
            if not 0 <= age <= self.maximum_age_ns:
                raise SourceTimeError('dds_publication_expired_or_future')
            # Never renew command freshness at callback time. Choose the older
            # estimate when bounded wall/monotonic offset jitter is present.
            generated = min(self.anchor_mono + source - self.anchor_wall, mono - age)
            if not 0 < generated <= mono:
                raise SourceTimeError('dds_monotonic_mapping_invalid')
            self.previous_source = source
            self.accepted += 1
            self.maximum_age_observed_ns = max(self.maximum_age_observed_ns, mono - generated)
            return generated / 1e9
        except SourceTimeError as error:
            # A later fresh sample cannot revive an invalid source generation.
            self.failure = str(error)
            raise


class HumbleTwistSource:
    def __init__(self, namespace, *, maximum_age_ns=150_000_000):
        if (os.environ.get('ROS_DISTRO') != 'humble'
                or os.environ.get('RMW_IMPLEMENTATION') != 'rmw_fastrtps_cpp'):
            raise ValueError('verified_humble_fastdds_required')
        import rclpy
        from geometry_msgs.msg import Twist
        self.message_type, self.closed = Twist, False
        self.clock = PublicationClock(maximum_age_ns=maximum_age_ns)
        self.node = rclpy.create_node('publication_observer', namespace=namespace)
        try:
            self.subscription = self.node.create_subscription(
                Twist, 'observed_cmd_vel', self.executor_forbidden, 1)
        except BaseException:
            self.node.destroy_node()
            raise

    @staticmethod
    def executor_forbidden(_message):
        raise RuntimeError('metadata_observer_must_not_be_spun')

    def take(self):
        if self.closed:
            return None
        if self.node.executor is not None:
            raise SourceTimeError('metadata_observer_attached_to_executor')
        with self.subscription.handle:
            sample = self.subscription.handle.take_message(self.message_type, False)
        if sample is None:
            return None
        if type(sample) is not tuple or len(sample) != 2:
            raise SourceTimeError('humble_take_metadata_contract_changed')
        message, metadata = sample
        generated = self.clock.convert(metadata)
        return message, generated

    def close(self):
        if not self.closed:
            self.closed = True
            self.node.destroy_node()
