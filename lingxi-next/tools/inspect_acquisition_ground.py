"""Inspect preserved native-odom rays twice; never fit or install a calibration."""
import argparse
from dataclasses import asdict
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'robot-navigation'), str(ROOT / 'robot-slam')]
import numpy as np
from check_ground_native import verified_build
from evaluate_recorded_clearance import configuration
from prepare_nav2_offline import strict_object
from refine_saved_map import private_output
from run_glim_supervised_replay import private_json
from lingxi_navigation.ground import GroundDeclaration, PatchworkSegmenter, UPSTREAM_REVISION
from lingxi_navigation.ground_inspection import inspect_ground_labels
from lingxi_navigation.occupancy import digest_value
from lingxi_navigation.recorded_occupancy import OfflineGeometry
from lingxi_slam.observation_archive import rigid
from verify_slam_capture import sha256

PURPOSE = 'recorded_native_odom_ground_inspection_not_registered_navigation'
RESULT = 'repeated_ground_inspection_not_physical_calibration'
SOURCES = ('tools/inspect_acquisition_ground.py', 'tools/check_ground_native.py',
    'robot-navigation/lingxi_navigation/ground_inspection.py', 'robot-navigation/lingxi_navigation/ground.py',
    'robot-navigation/lingxi_navigation/recorded_occupancy.py', 'robot-navigation/lingxi_navigation/occupancy.py',
    'robot-slam/lingxi_slam/observation_archive.py')
BOUNDARIES = dict.fromkeys(('robotContacted', 'sensorsSubscribed', 'rosNodeStarted', 'physicalMotionSent',
    'physicalScaleValidated', 'physicalFloorIdentified', 'groundSupportValidated', 'configurationApplied',
    'navigationReady', 'arm64Executed', 'independentLocalizationAccuracyValidated'), False)


def require_host():
    if (sys.platform != 'linux' or platform.machine() != 'x86_64'
            or os.geteuid() == 0 or sys.prefix == sys.base_prefix):
        raise ValueError('isolated_ordinary_user_linux_x86_ground_inspection_required')


def direct(path, maximum):
    path = Path(path)
    if (not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file()
            or not 0 < path.stat().st_size <= maximum):
        raise ValueError('bounded_direct_ground_inspection_input_required')
    return path


def sample(path, expected_sha256):
    """Only the six numeric/string fields emitted by the native acquisition trial."""
    path = direct(path, 1024 * 1024)
    if not digest_value(expected_sha256) or sha256(path) != expected_sha256:
        raise ValueError('pinned_acquisition_sample_required')
    names = {'origins', 'endpoints', 'world_lidar_pose', 'frame_id', 'algorithm_stamp', 'source_sha256'}
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if (len(entries) != len(names) or {item.filename for item in entries} != {name + '.npy' for name in names}
                or sum(item.file_size for item in entries) > 8192 * 48 + 65536):
            raise ValueError('bounded_complete_acquisition_npz_required')
    with np.load(path, allow_pickle=False, max_header_size=4096) as archive:
        origins, endpoints = archive['origins'], archive['endpoints']
        if (any(value.dtype != np.float64 or value.ndim != 2 or value.shape[1] != 3
                or not np.isfinite(value).all() or np.max(np.abs(value), initial=0.) > 500
                for value in (origins, endpoints))
                or origins.shape != endpoints.shape or not 3 <= len(origins) <= 8192):
            raise ValueError('complete_bounded_measured_acquisition_pairs_required')
        rigid(archive['world_lidar_pose'])
        frame, stamp, source = (archive[name] for name in ('frame_id', 'algorithm_stamp', 'source_sha256'))
        if (frame.shape != () or frame.dtype.kind not in 'iu' or not 1 <= int(frame) < 2**53
                or stamp.shape != () or stamp.dtype.kind not in 'fi' or not np.isfinite(stamp) or float(stamp) <= 0
                or source.shape != () or source.dtype.kind != 'U' or not digest_value(source.item())):
            raise ValueError('original_acquisition_frame_stamp_and_source_required')
    if sha256(path) != expected_sha256:
        raise ValueError('unchanged_acquisition_sample_required')
    return origins, endpoints, int(frame), float(stamp)


def inputs(args):
    trial = Path(args.trial)
    if not trial.is_absolute() or trial.resolve(strict=True) != trial or not trial.is_dir():
        raise ValueError('direct_preserved_acquisition_trial_required')
    receipt_path = direct(trial / 'recorded-report.json', 1024 * 1024)
    if not digest_value(args.receipt_sha256) or sha256(receipt_path) != args.receipt_sha256:
        raise ValueError('explicit_acquisition_receipt_identity_required')
    receipt = json.loads(receipt_path.read_bytes(), object_pairs_hook=strict_object)
    capture = receipt['acquisition']
    if (receipt['result'] != 'complete_recorded_immediate_acquisition_not_live_or_metric_acceptance'
            or capture['coordinateFrame'] != 'lingxi_glim_odom' or capture['poseFrame'] != 'lidar_not_robot_base'
            or any(capture[key] is not True for key in ('allPreprocessedReturnsPreserved',
                'perReturnAcquisitionTimeBracketed', 'initializationFrameExcluded'))
            or capture['receiptOrMetricAdmissionSynthesized'] is not False):
        raise ValueError('complete_native_odom_acquisition_receipt_required')
    config = direct(args.config, 128 * 1024)
    value = configuration(config)
    setting = value['ground']
    library = direct(Path(setting['library']), 16 * 1024**2)
    build = library.parent / 'build-report.json'
    verified_build(library, setting['sha256'], build)
    geometry = OfflineGeometry(value['geometry']['metresPerRawUnit'], tuple(value['geometry']['declaredFromRaw']))
    declaration = GroundDeclaration(**setting['declaration'])
    frozen = dict(receiptSha256=args.receipt_sha256, configurationSha256=sha256(config),
        samplesSha256={name: capture[name + 'SampleSha256'] for name in ('first', 'latest')},
        sampleFrames={name: capture[key] for name, key in (('first', 'firstNativeFrameId'), ('latest', 'lastNativeFrameId'))},
        sourcesSha256={name: sha256(ROOT / name) for name in SOURCES},
        nativeLibrarySha256=setting['sha256'], nativeBuildSha256=sha256(build))
    for name, digest in frozen['samplesSha256'].items():
        if not digest_value(digest) or sha256(direct(trial / ('private-' + name + '-rays.npz'), 1024 * 1024)) != digest:
            raise ValueError('unchanged_paired_acquisition_samples_required')
    return frozen, geometry, declaration, library


def worker(args):
    require_host()
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    frozen, geometry, declaration, library = inputs(args)
    segmenter = PatchworkSegmenter(library, frozen['nativeLibrarySha256'])
    rows, previous_frame, previous_stamp = [], 0, 0.
    for name, digest in frozen['samplesSha256'].items():
        origins, endpoints, frame, stamp = sample(args.trial / ('private-' + name + '-rays.npz'), digest)
        if frame != frozen['sampleFrames'][name] or frame <= previous_frame or stamp <= previous_stamp:
            raise ValueError('ordered_original_acquisition_end_samples_required')
        previous_frame, previous_stamp = frame, stamp
        origins, endpoints = geometry.apply(origins), geometry.apply(endpoints)
        points = tuple(tuple(map(float, row)) for row in endpoints - origins[0])
        labels = segmenter.segment(points, declaration)
        rows.append(dict(sample=name, frame=frame, originalOriginSpanDeclaredMetres=float(
            np.max(np.linalg.norm(origins - origins[0], axis=1))),
            **inspect_ground_labels(points, labels, declaration, anchor_z_metres=float(origins[0, 2]))))
    if inputs(args)[0] != frozen:
        raise ValueError('unchanged_ground_inspection_inputs_required')
    report = dict(schemaVersion=1, purpose=PURPOSE, host='linux-x86_64', inputIntegrity=frozen, samples=rows,
        declaration=asdict(declaration), nativeRevision=UPSTREAM_REVISION,
        versions={name: version(name) for name in ('numpy', 'scipy')}, boundaries=BOUNDARIES)
    private_json(args.output / ('worker-' + str(args.worker_pass) + '.json'), report)


def run(args):
    require_host()
    frozen = inputs(args)[0]
    output = private_output(args.output, args.trial)
    output.mkdir(mode=0o700)
    environment = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
        'MKL_NUM_THREADS': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    reports, logs = [], {}
    for ordinal in range(2):
        log = output / ('private-worker-' + str(ordinal) + '.log')
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker-pass', str(ordinal),
            '--trial', str(args.trial), '--receipt-sha256', args.receipt_sha256,
            '--config', str(args.config), '--output', str(output)]
        with log.open('xb') as stream:
            result = subprocess.run(command, stdout=stream, stderr=stream, timeout=30., env=environment)
        if result.returncode:
            raise RuntimeError('native_ground_inspection_failed_see_private_log')
        report_path = direct(output / ('worker-' + str(ordinal) + '.json'), 128 * 1024)
        reports.append(json.loads(report_path.read_bytes(), object_pairs_hook=strict_object))
        logs[log.name] = sha256(log)
    report = reports[0]
    if (report != reports[1] or report['inputIntegrity'] != frozen or inputs(args)[0] != frozen
            or report['purpose'] != PURPOSE or report['boundaries'] != BOUNDARIES):
        raise ValueError('identical_complete_current_source_ground_inspection_required')
    report.update(result=RESULT, identicalRepeat=True, workerExitCodes=[0, 0], privateLogSha256=logs)
    private_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('trial', 'config', 'output'):
        parser.add_argument('--' + name, type=lambda value: Path(value).absolute(), required=True)
    parser.add_argument('--receipt-sha256', required=True)
    parser.add_argument('--worker-pass', type=int, choices=(0, 1))
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        if args.worker_pass is not None:
            worker(args)
        else:
            result = run(args)
            print(json.dumps(dict(result=result['result'], identicalRepeat=True,
                issues={row['sample']: row['issues'] for row in result['samples']}, navigationReady=False)))
    finally:
        os.umask(previous)


if __name__ == '__main__':
    main()
