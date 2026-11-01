"""Nonactuating DDS publication-age probe, only inside loopback isolation."""
import json
import hashlib
import os
from pathlib import Path
import sys
import time

from humble_velocity_source import HumbleTwistSource, SourceTimeError


def main():
    if set(os.listdir('/sys/class/net')) != {'lo'} or os.environ.get('ROS_LOCALHOST_ONLY') != '1':
        raise ValueError('isolated_loopback_required')
    import rclpy
    import rclpy.executors
    from geometry_msgs.msg import Twist
    rclpy.init()
    publisher_node = rclpy.create_node('probe_publisher', namespace='publication_probe')
    publisher = publisher_node.create_publisher(Twist, 'observed_cmd_vel', 1)
    reader = HumbleTwistSource('publication_probe')
    message = Twist()
    message.linear.x = .1
    deadline = time.monotonic() + 5.
    while publisher.get_subscription_count() != 1:
        if time.monotonic() >= deadline:
            raise RuntimeError('bounded_discovery_timeout')
        time.sleep(.01)
    publisher.publish(message)
    fresh = None
    while fresh is None:
        fresh = reader.take()
        if time.monotonic() >= deadline:
            raise RuntimeError('bounded_sample_timeout')
        time.sleep(.001)
    publisher.publish(message)
    time.sleep(.25)
    rejection = None
    try:
        queued = reader.take()
        if queued is None:
            raise RuntimeError('delayed_sample_missing')
    except SourceTimeError as error:
        rejection = str(error)
    publisher.publish(message)
    time.sleep(.02)
    poisoned = False
    try:
        reader.take()
    except SourceTimeError as error:
        poisoned = str(error) == rejection
    reader.close()
    closed = reader.take() is None
    reader = HumbleTwistSource('publication_probe')
    # New generation must discover before the fresh publication.
    time.sleep(.3)
    publisher.publish(message)
    recovered, deadline = None, time.monotonic() + 2.
    while recovered is None:
        recovered = reader.take()
        if time.monotonic() >= deadline:
            raise RuntimeError('fresh_generation_sample_timeout')
        time.sleep(.001)
    root = Path(__file__).resolve().parents[1]
    sources = ('tools/humble_velocity_source.py', 'tools/probe_humble_publication.py',
               'tools/nav2-offline-isolated.sh')
    report = dict(schemaVersion=1, purpose='isolated_humble_dds_publication_probe_not_field_acceptance',
        sourceSha256={name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources},
        installedExecutorSha256=hashlib.sha256(Path(rclpy.executors.__file__).read_bytes()).hexdigest(),
        rosDistro=os.environ['ROS_DISTRO'], middleware=os.environ['RMW_IMPLEMENTATION'],
        passed=bool(fresh and recovered and closed and poisoned
        and rejection == 'dds_publication_expired_or_future'),
        freshPublicationAccepted=bool(fresh), delayedPublicationRejected=rejection,
        invalidSourceCannotRevive=poisoned,
        retiredReaderInert=closed, freshGenerationAccepted=bool(recovered),
        ddsPublicationTimeVerified=True, controllerComputationAgeProven=False,
        physicalMotionSent=False, robotContact=False, navigationReady=False)
    reader.close()
    publisher_node.destroy_node()
    rclpy.shutdown()
    print(json.dumps(report, indent=2))
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if not report['passed']:
        raise RuntimeError('publication_age_probe_failed')


if __name__ == '__main__':
    main()
