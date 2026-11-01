"""Supervised recorded rolling terrain production. No robot IO or metric promotion."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from evaluate_recorded_clearance import ROOT, configuration, digest, source_names

SOURCES = ('robot-navigation/lingxi_navigation/terrain.py', 'tools/evaluate_recorded_terrain.py')
FALSE_FLAGS = ('robotContacted', 'physicalMotionSent', 'physicalScaleValidated', 'groundSupportValidated', 'navigationReady')


def worker(config, output):
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
    sys.path[:0] = [str(ROOT / 'robot-navigation'), str(ROOT / 'robot-slam')]
    from refine_saved_map import inventory
    from check_ground_native import verified_build
    from lingxi_navigation.ground import GroundDeclaration, PatchworkSegmenter, validate_declared_ground_frame
    from lingxi_navigation.occupancy import ClearanceProjection, OctomapProjector
    from lingxi_navigation.recorded_occupancy import OfflineGeometry, RaySelection, prepare_recorded_clearance
    from lingxi_navigation.terrain import TerrainBudget, TerrainProducer, recorded_windows
    started, config_sha256 = time.monotonic(), digest(config)
    value = configuration(config)
    if 'ground' not in value:
        raise ValueError('explicit_ground_declaration_required')
    sources = {name: digest(ROOT / name) for name in source_names(value) + SOURCES}
    ground = value['ground']
    library = Path(ground['library'])
    verified_build(library, ground['sha256'], library.parent / 'build-report.json')
    geometry = value['geometry']
    prepare = lambda: prepare_recorded_clearance(Path(value['savedMap']), receipt_sha256=value['mapReceiptSha256'],
        binding_sha256=value['bindingSha256'], implementation_sha256=value['observationImplementationSha256'],
        inspect_map=inventory, geometry=OfflineGeometry(geometry['metresPerRawUnit'], tuple(geometry['declaredFromRaw'])),
        selection=RaySelection(**value['selection']))
    prepared = prepare()
    declaration = GroundDeclaration(**ground['declaration'])
    # A recording-wide invalid frame must not publish a partial accepted replay.
    validate_declared_ground_frame(prepared.batch, declaration)
    projection = ClearanceProjection(**value['projection'])
    budget = TerrainBudget(maximum_work_units=value['maximumWorkUnits'])
    windows = list(recorded_windows(prepared.batch, budget))
    size = projection.width * projection.height
    if len(windows) > 256 or size * len(windows) > 32 * 1024**2:
        raise ValueError('bounded_recorded_terrain_output_required')
    native = value['octomap']
    producer = TerrainProducer(OctomapProjector(Path(native['library']), native['sha256'], native['dependencies']),
        PatchworkSegmenter(library, ground['sha256']), projection, declaration, budget)
    rows, grids, covered = [], bytearray(), set()
    for window in windows:
        result = producer.update(window.observations, sequence=window.sequence, received_at=time.monotonic())
        if producer.current() is not result:
            raise TimeoutError('terrain_expired_before_export')
        rows.append(dict(**result.summary(), sourceStart=window.source_start, sourceEnd=window.source_end))
        grids.extend(result.fused.costs)
        covered.update(range(window.source_start, window.source_end))
    if (prepare().summary() != prepared.summary() or digest(config) != config_sha256
            or any(digest(ROOT / name) != expected for name, expected in sources.items())):
        raise ValueError('unchanged_recorded_terrain_sources_required')
    artifact = output / 'private-terrain-grids.bin'
    with artifact.open('xb') as stream:
        stream.write(grids)
        stream.flush()
        os.fsync(stream.fileno())
    report = dict(schemaVersion=1, result='recorded_rolling_terrain_not_navigation_acceptance',
        input=prepared.summary(), projection=projection.__dict__, budget=budget.__dict__,
        configurationSha256=config_sha256, sourcesSha256=sources,
        nativeSha256=dict(ground=ground['sha256'], octomap=native['sha256']),
        dependenciesSha256=native['dependencies'], windows=rows, coveredSourceScans=len(covered),
        unrepresentedSourceScans=len(prepared.batch.scans) - len(covered),
        artifactSha256=digest(artifact), artifactBytes=len(grids),
        workerDurationSeconds=time.monotonic() - started,
        peakRssKiB=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        sensorPaced=False, offlineOnly=True, **dict.fromkeys(FALSE_FLAGS, False))
    with (output / 'report.pending').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    return report


def validate_report(value, report, output):
    sys.path.insert(0, str(ROOT / 'robot-navigation'))
    from lingxi_navigation.occupancy import ClearanceProjection
    from lingxi_navigation.terrain import TerrainBudget
    if (type(report.get('schemaVersion')) is not int or report['schemaVersion'] != 1
            or report.get('result') != 'recorded_rolling_terrain_not_navigation_acceptance'
            or report.get('offlineOnly') is not True or report.get('sensorPaced') is not False
            or any(report.get(key) is not False for key in FALSE_FLAGS)
            or report.get('sourcesSha256') != {name: digest(ROOT / name) for name in source_names(value) + SOURCES}
            or report.get('nativeSha256') != dict(ground=value['ground']['sha256'], octomap=value['octomap']['sha256'])
            or report.get('dependenciesSha256') != value['octomap']['dependencies']):
        raise ValueError('source_bound_offline_terrain_report_required')
    source = report.get('input', {})
    if (source.get('mapReceiptSha256') != value['mapReceiptSha256'] or source.get('bindingSha256') != value['bindingSha256']
            or source.get('offlineOnly') is not True or any(source.get(key) is not False for key in
                ('physicalScaleValidated', 'groundFrameValidated', 'groundSupportValidated', 'obstacleCompletenessValidated', 'navigationReady'))
            or type(source.get('scans')) is not int or not 1 <= source['scans'] <= 4096
            or type(source.get('revision')) is not int or source['revision'] < 1):
        raise ValueError('same_declared_recording_required')
    projection = report['projection']
    if (projection != ClearanceProjection(**value['projection']).__dict__
            or report.get('budget') != TerrainBudget(maximum_work_units=value['maximumWorkUnits']).__dict__):
        raise ValueError('same_terrain_projection_required')
    size = projection['width'] * projection['height']
    rows = report.get('windows')
    artifact = output / 'private-terrain-grids.bin'
    if (type(rows) is not list or not 1 <= len(rows) <= 256 or artifact.is_symlink() or not artifact.is_file()
            or artifact.stat().st_size != len(rows) * size or artifact.stat().st_size > 32 * 1024**2
            or digest(artifact) != report.get('artifactSha256') or report.get('artifactBytes') != artifact.stat().st_size):
        raise ValueError('complete_terrain_revision_artifact_required')
    costs, covered, previous_end = artifact.read_bytes(), set(), 0
    budget = report['budget']
    for index, row in enumerate(rows):
        block = costs[index * size:(index + 1) * size]
        if (row.get('sequence') != index or type(row.get('sourceStart')) is not int or type(row.get('sourceEnd')) is not int
                or not 0 <= row['sourceStart'] < row['sourceEnd'] <= source['scans'] or row['sourceEnd'] <= previous_end
                or row.get('scans') != row['sourceEnd'] - row['sourceStart'] or row['scans'] > budget['maximum_scans']
                or row.get('mapRevision') != source['revision'] or row.get('offlineOnly') is not True
                or any(row.get(key) is not False for key in ('physicalScaleValidated', 'groundSupportValidated', 'navigationReady'))
                or not 1 <= row.get('rays', 0) <= budget['maximum_rays']
                or any(type(row.get(key)) not in (int, float) or not math.isfinite(row[key])
                    for key in ('processingSeconds', 'ingressToCompletionSeconds'))
                or not 0 <= row['processingSeconds'] <= budget['maximum_processing_seconds']
                or not row['processingSeconds'] <= row['ingressToCompletionSeconds'] < budget['maximum_age_seconds']
                or not set(block) <= {0, 254, 255} or hashlib.sha256(block).hexdigest() != row.get('traversabilityBytesSha256')
                or row.get('cells') != dict(zip(('free', 'occupied', 'unknown'), (block.count(i) for i in (0, 254, 255))))):
            raise ValueError('complete_ordered_fresh_terrain_revisions_required')
        covered.update(range(row['sourceStart'], row['sourceEnd']))
        previous_end = row['sourceEnd']
    if (previous_end != source['scans'] or len(covered) != report.get('coveredSourceScans')
            or source['scans'] - len(covered) != report.get('unrepresentedSourceScans')):
        raise ValueError('complete_terrain_source_coverage_accounting_required')


def run(config, output):
    from refine_saved_map import private_output
    config = Path(config).absolute()
    value = configuration(config)
    if 'ground' not in value or config.resolve(strict=True) != config:
        raise ValueError('direct_recorded_ground_configuration_required')
    config_sha256 = digest(config)
    output = private_output(output, Path(value['savedMap']))
    output.mkdir(mode=0o700)
    environment = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    try:
        with (output / 'private-worker.log').open('x') as log:
            child = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--worker',
                '--config', str(config), '--output', str(output)], stdout=log, stderr=log,
                env=environment, timeout=value['wallTimeoutSeconds'])
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('terrain_worker_timed_out_no_accepted_report') from error
    pending = output / 'report.pending'
    if child.returncode or pending.is_symlink() or not pending.is_file() or not 1 <= pending.stat().st_size <= 1024**2:
        raise RuntimeError('terrain_worker_failed_no_accepted_report_see_private_log')
    report = json.loads(pending.read_bytes())
    if report.get('configurationSha256') != config_sha256 or digest(config) != config_sha256:
        raise ValueError('unchanged_terrain_configuration_required')
    validate_report(value, report, output)
    os.replace(pending, output / 'report.json')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        report = worker(args.config, args.output) if args.worker else run(args.config, args.output)
        print(json.dumps(dict(result=report['result'], windows=len(report['windows']),
            coveredSourceScans=report['coveredSourceScans'], navigationReady=False)))
    finally:
        os.umask(previous)
