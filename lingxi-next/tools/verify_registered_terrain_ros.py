"""Regrade registered-source actual ROS samples without accepting physical motion."""
import argparse
import json
from pathlib import Path

from verify_terrain_ros_evidence import verify_common
from prepare_nav2_offline import strict_object

MODES = ('pause', 'stop', 'reseed', 'loss')
PURPOSE = 'registered_synthetic_terrain_ros_retirement_not_field_acceptance'
SOURCES = ('tools/probe_registered_terrain_ros.py', 'tools/registered-terrain-ros-isolated.sh',
    'tools/verify_registered_terrain_ros.py', 'tools/verify_terrain_ros_evidence.py',
    'robot-runtime/python/registered_terrain.py', 'robot-slam/lingxi_slam/registered_acquisition.py',
    'robot-slam/lingxi_slam/localization_session.py', 'robot-slam/lingxi_slam/localization_tracking.py',
    'robot-slam/lingxi_slam/base_pose.py',
    'robot-slam/lingxi_slam/seeded_localization.py', 'robot-slam/lingxi_slam/point_data.py',
    'robot-slam/lingxi_slam/point_output.py', 'robot-slam/lingxi_slam/acquisition_output.py',
    'robot-slam/lingxi_slam/observation_archive.py', 'robot-navigation/lingxi_navigation/registered_window.py',
    'robot-navigation/lingxi_navigation/recorded_occupancy.py',
    'robot-navigation/lingxi_navigation/terrain_lane.py', 'robot-navigation/lingxi_navigation/terrain_ros.py',
    'robot-navigation/lingxi_navigation/terrain_feed.py', 'robot-navigation/lingxi_navigation/terrain_process.py',
    'robot-navigation/lingxi_navigation/terrain_worker.py', 'robot-navigation/lingxi_navigation/terrain_protocol.py',
    'robot-navigation/lingxi_navigation/terrain.py', 'tools/terrain_nav2_fixture.py', 'tools/run_nav2_offline.py')


def verify(report):
    result = verify_common(report, purpose=PURPOSE, modes=MODES, sources=SOURCES)
    from lingxi_navigation.navfn import finite
    from lingxi_navigation.occupancy import digest_value
    value = report['registeredInput']
    receipts = value['originalReceipts']
    if (type(receipts) is not list or not 1 <= len(receipts) <= 32
            or not all(finite(receipt) and receipt >= 0 for receipt in receipts)
            or receipts != sorted(receipts) or value['acceptedReceipt'] != receipts[0]
            or value['sourceScans'] != len(receipts) or value['selectedScans'] != len(receipts)
            or type(value['sourceReturns']) is not int or value['sourceReturns'] <= 0
            or value['selectedReturns'] != value['sourceReturns']
            or not all(digest_value(value[key]) for key in ('bindingSha256', 'epochSha256', 'observationsSha256'))
            or value['observationsSha256'] != report['actualNativeGrid']['observationsSha256']
            or value['units'] != 'declared-metres' or value['registrar'] != 'explicit_synthetic_identity'
            or type(value['generation']) is not int or value['generation'] < 1
            or type(value['finalGeneration']) is not int or value['finalGeneration'] <= value['generation']
            or type(value['sequence']) is not int or value['sequence'] < 1
            or any(value[key] is not True for key in ('sourceRetired', 'finalGridAbsent', 'noAdditionalRaysAfterSubmission'))):
        raise ValueError('original_receipt_registered_epoch_and_terminal_retirement_required')
    expected = dict(pause='paused', stop='stopped', reseed='tracking', loss='lost')
    if value['finalState'] != expected[report['mode']]:
        raise ValueError('expected_localization_retirement_state_required')
    return dict(result, registeredSourceRegraded=True)


def load(path):
    path = Path(path)
    if path.stat().st_size > 256 * 1024:
        raise ValueError('bounded_registered_ros_report_required')
    return verify(json.loads(path.read_bytes(), object_pairs_hook=strict_object))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps([load(path) for path in args.reports], indent=2))
