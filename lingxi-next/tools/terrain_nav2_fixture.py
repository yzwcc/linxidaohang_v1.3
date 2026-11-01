"""Source-bound actual-native terrain for loopback-only, synthetic Nav2 trials.

The immutable export crosses host/rootfs ABI boundaries. It is never a live
sensor feed or metric admission; all ray origins and geometry are synthetic.
"""
import argparse
import base64
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.ground import (GroundDeclaration, PatchworkSegmenter,
    DeclaredGroundCandidate, fuse_declared)
from lingxi_navigation.occupancy import (ClearanceProjection, DeclaredObservationBatch,
    DeclaredClearanceResult, LidarReturn, OctomapProjector, ScanObservation, canonical)
from lingxi_navigation.occupancy import digest_value, DEPENDENCIES
from lingxi_navigation.terrain import TerrainBudget, TerrainProducer
from prepare_nav2_offline import digest, strict_object

CASES = ('open', 'blocked', 'detour', 'unsupported-shortcut')
CLASSIFICATION = 'synthetic_acquisition_rays_actual_native_terrain_frozen_offline_only'
SOURCE_NAMES = ('tools/terrain_nav2_fixture.py',
    'robot-navigation/lingxi_navigation/terrain.py',
    'robot-navigation/lingxi_navigation/ground.py',
    'robot-navigation/lingxi_navigation/occupancy.py',
    'robot-navigation/lingxi_navigation/navfn.py',
    'robot-navigation/native-ground/ground_bridge.cpp',
    'robot-navigation/patchworkpp.lock.json',
    'robot-navigation/native-octomap/octomap_bridge.cpp',
    'tools/check_ground_native.py')


def native_provenance(ground_library, ground_sha, ground_report, octomap_library, octomap_sha, octomap_report):
    from check_ground_native import verified_build
    for path, sha in ((ground_library, ground_sha), (octomap_library, octomap_sha)):
        if (not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file()
                or not digest_value(sha) or digest(path) != sha):
            raise ValueError('direct_hash_verified_native_library_required')
    ground = verified_build(ground_library, ground_sha, ground_report)
    if (not octomap_report.is_absolute() or octomap_report.resolve(strict=True) != octomap_report
            or octomap_report.parent != octomap_library.parent or octomap_report.stat().st_size > 262144):
        raise ValueError('direct_adjacent_octomap_build_report_required')
    octomap = json.loads(octomap_report.read_bytes(), object_pairs_hook=strict_object)
    if (octomap.get('schemaVersion') != 1
            or octomap.get('purpose') != 'offline_native_build_not_target_acceptance'
            or octomap.get('octomapVersion') != '1.9.7' or octomap.get('nativeSha256') != octomap_sha
            or octomap.get('sourceSha256') != digest(ROOT / 'robot-navigation/native-octomap/octomap_bridge.cpp')
            or octomap.get('freshProcessLoadPassed') is not True
            or any(octomap.get(key) is not False for key in ('robotContacted', 'navigationReady'))
            or set(octomap.get('dependencies', {})) != DEPENDENCIES
            or not all(digest_value(sha) for sha in octomap['dependencies'].values())):
        raise ValueError('current_source_bound_octomap_build_required')
    for name, sha in octomap['dependencies'].items():
        path = octomap_library.parent / 'lib' / name
        if path.resolve(strict=True) != path or digest(path) != sha:
            raise ValueError('unchanged_octomap_dependencies_required')
    for name, sha in octomap['headersSha256'].items():
        path = octomap_library.parent / 'include/octomap' / name
        if path.resolve(strict=True) != path or digest(path) != sha:
            raise ValueError('unchanged_octomap_compiled_headers_required')
    result = dict(groundBuildReportSha256=digest(ground_report), octomapBuildReportSha256=digest(octomap_report),
        groundRevision=ground['revision'], groundLockSha256=ground['lockSha256'],
        groundAdapterSha256=ground['adapterSha256'], octomapAdapterSha256=octomap['sourceSha256'],
        dependenciesSha256=octomap['dependencies'], freshProcessLoadVerified=True,
        installedBinaryMatchesPinnedSourceVerified=False)
    validate_provenance(result)
    return result


def validate_provenance(value):
    lock = json.loads((ROOT / 'robot-navigation/patchworkpp.lock.json').read_bytes())
    if (type(value) is not dict or set(value) != {'groundBuildReportSha256', 'octomapBuildReportSha256',
            'groundRevision', 'groundLockSha256', 'groundAdapterSha256', 'octomapAdapterSha256',
            'dependenciesSha256', 'freshProcessLoadVerified', 'installedBinaryMatchesPinnedSourceVerified'}
            or not all(digest_value(value[key]) for key in ('groundBuildReportSha256', 'octomapBuildReportSha256'))
            or value['groundRevision'] != lock['revision']
            or value['groundLockSha256'] != digest(ROOT / 'robot-navigation/patchworkpp.lock.json')
            or value['groundAdapterSha256'] != digest(ROOT / 'robot-navigation/native-ground/ground_bridge.cpp')
            or value['octomapAdapterSha256'] != digest(ROOT / 'robot-navigation/native-octomap/octomap_bridge.cpp')
            or type(value['dependenciesSha256']) is not dict or set(value['dependenciesSha256']) != DEPENDENCIES
            or not all(digest_value(sha) for sha in value['dependenciesSha256'].values())
            or value['freshProcessLoadVerified'] is not True
            or value['installedBinaryMatchesPinnedSourceVerified'] is not False):
        raise ValueError('complete_current_native_build_provenance_required')


def projection():
    return ClearanceProjection(64, 60, .1, -2., -3.)


def obstacle(case):
    if case == 'blocked':
        return (.8, 1.3, -3., 3., 0., 1.6)
    if case == 'detour':
        return (.8, 1.3, -.35, .35, 0., 1.6)
    return None


def missing_floor(case, point):
    return case == 'unsupported-shortcut' and .6 <= point[0] < 1.4 and -.4 <= point[1] < .4


def cast(origin, target, box):
    """Analytic fixture geometry, not a planner or robot perception algorithm."""
    delta = tuple(b - a for a, b in zip(origin, target))
    stop = 1.
    if delta[2] < 0:
        stop = min(stop, -origin[2] / delta[2])
    if box is not None:
        near, far = 0., stop
        for axis in range(3):
            low, high = box[2 * axis:2 * axis + 2]
            if abs(delta[axis]) < 1e-12:
                if not low <= origin[axis] <= high:
                    far = -1.
                    break
            else:
                a, b = sorted(((low - origin[axis]) / delta[axis], (high - origin[axis]) / delta[axis]))
                near, far = max(near, a), min(far, b)
        if 0 < near <= far:
            stop = near
    return tuple(a + stop * d for a, d in zip(origin, delta))


def acquisition(case):
    if case not in CASES:
        raise ValueError('known_synthetic_native_terrain_case_required')
    scans, box = [], obstacle(case)

    def append(origin, targets, *, include_floor=False):
        rays = []
        for target in targets:
            endpoint = cast(origin, target, box)
            if abs(endpoint[2]) < 1e-8 and missing_floor(case, endpoint):
                continue
            rays.append(LidarReturn(origin, endpoint))
        floor_rays = []
        if include_floor:
            for target in floor:
                endpoint = cast(origin, target, box)
                if not missing_floor(case, endpoint) or abs(endpoint[2]) >= 1e-8:
                    floor_rays.append(LidarReturn(origin, endpoint))
        chunk = 4000 if include_floor else 8192
        for start in range(0, len(rays), chunk):
            values = tuple(floor_rays + rays[start:start + chunk])
            source = hashlib.sha256(canonical([asdict(ray) for ray in values])).hexdigest()
            index = len(scans)
            scans.append(ScanObservation(index, 1. + .1 * index, source, values))

    # Repeated dense floor observations, without filling the declared hole.
    floor = tuple((-.8 + x * .1 + dx, -1.8 + y * .1 + dy, 0.)
        for y in range(36) for x in range(38)
        for dx, dy in ((.02, .02), (.05, .05), (.08, .08)))
    for origin in ((-1.5, 0., .6), (3.5, 0., .6)):
        append(origin, floor)
    # Aim through every clearance voxel; all endpoints lie on a wall outside
    # the grid, the floor, or the first box intersection. No through-wall rays.
    for origin, end_x in (((-3., 0., .6), 5.), ((5., 0., .6), -3.)):
        targets = []
        for y in range(36):
            for x in range(38):
                px, py = -.75 + x * .1, -1.75 + y * .1
                for z in range(13):
                    pz = .15 + .1 * z
                    scale = (end_x - origin[0]) / (px - origin[0])
                    targets.append((end_x, origin[1] + (py - origin[1]) * scale,
                                    origin[2] + (pz - origin[2]) * scale))
        append(origin, targets, include_floor=True)
    identity = hashlib.sha256(canonical(dict(recipe=1, case=case))).hexdigest()
    return DeclaredObservationBatch('slam-' + identity[:32], identity, 1, identity,
                                   identity, identity, tuple(scans))


def build(case, segmenter, projector, octomap_sha256, provenance):
    validate_provenance(provenance)
    data, spec, declaration = acquisition(case), projection(), GroundDeclaration(.6, 0.)
    producer = TerrainProducer(projector, segmenter, spec, declaration,
        TerrainBudget(maximum_rays=131072, maximum_processing_seconds=10., maximum_age_seconds=15., maximum_work_units=16000000))
    snapshot = producer.update(data, sequence=0, received_at=time.monotonic())
    return dict(schemaVersion=1, classification=CLASSIFICATION, case=case,
        physicalScaleValidated=False, groundSupportValidated=False, navigationReady=False,
        physicalMotionSent=False, robotContacted=False, liveFreshnessProven=False,
        projection=asdict(spec), declaration=asdict(declaration),
        sourcesSha256={name: digest(ROOT / name) for name in SOURCE_NAMES},
        observationsSha256=data.sha256, scanSha256=[scan.sha256 for scan in data.scans],
        groundLibrarySha256=segmenter.sha256, octomapLibrarySha256=octomap_sha256,
        nativeBuild=provenance,
        summary=snapshot.summary(),
        support={**{key: value for key, value in asdict(snapshot.support).items() if key != 'costs'},
                 'projection': asdict(spec), 'costs': base64.b64encode(snapshot.support.costs).decode()},
        clearance={**{key: value for key, value in asdict(snapshot.clearance).items() if key != 'costs'},
                   'projection': asdict(spec), 'costs': base64.b64encode(snapshot.clearance.costs).decode()})


def load(path, case):
    path = Path(path)
    if (not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file()
            or path.stat().st_size > 128 * 1024):
        raise ValueError('direct_bounded_frozen_terrain_fixture_required')
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=strict_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('finite_terrain_json_required')))
    expected = {'schemaVersion', 'classification', 'case', 'projection', 'declaration', 'sourcesSha256',
        'observationsSha256', 'scanSha256', 'groundLibrarySha256', 'octomapLibrarySha256', 'summary',
        'support', 'clearance', 'physicalScaleValidated', 'groundSupportValidated', 'navigationReady',
        'physicalMotionSent', 'robotContacted', 'liveFreshnessProven', 'nativeBuild'}
    if (type(value) is not dict or set(value) != expected or value['schemaVersion'] != 1
            or value['classification'] != CLASSIFICATION or value['case'] != case
            or any(value[key] is not False for key in ('physicalScaleValidated', 'groundSupportValidated',
                'navigationReady', 'physicalMotionSent', 'robotContacted', 'liveFreshnessProven'))
            or value['sourcesSha256'] != {name: digest(ROOT / name) for name in SOURCE_NAMES}
            or value['projection'] != asdict(projection()) or value['declaration'] != asdict(GroundDeclaration(.6, 0.))):
        raise ValueError('current_source_bound_declared_native_terrain_required')
    validate_provenance(value['nativeBuild'])
    if not all(digest_value(value[key]) for key in ('groundLibrarySha256', 'octomapLibrarySha256')):
        raise ValueError('native_library_identities_required')
    data, spec = acquisition(case), projection()
    if (value['observationsSha256'] != data.sha256
            or value['scanSha256'] != [scan.sha256 for scan in data.scans]):
        raise ValueError('unchanged_synthetic_acquisition_recipe_required')
    support, clearance = dict(value['support']), dict(value['clearance'])
    for obj, flags in ((support, ('offline_only', 'ground_support_validated', 'navigation_ready')),
                       (clearance, ('offline_only', 'physical_scale_validated', 'navigation_ready'))):
        if any(obj.pop(key, None) is not (key == 'offline_only') for key in flags):
            raise ValueError('complete_offline_candidate_flags_required')
        obj['projection'] = ClearanceProjection(**obj['projection'])
        obj['costs'] = base64.b64decode(obj['costs'], validate=True)
    clearance['statistics'] = tuple(clearance['statistics'])
    support = DeclaredGroundCandidate(**support)
    clearance = DeclaredClearanceResult(**clearance)
    rays = sum(len(scan.returns) for scan in data.scans)
    stats = clearance.statistics
    if (support.projection != spec or clearance.projection != spec or support.scans != len(data.scans)
            or support.ground_returns + support.nonground_returns + support.unclassified_returns != rays
            or type(stats) is not tuple or len(stats) != 7
            or any(type(number) is not int or number < 0 for number in stats)
            or stats[:2] != (rays, len(data.scans)) or not 0 < stats[2] <= 4000000
            or stats[3:6] != tuple(clearance.costs.count(i) for i in (0, 254, 255))
            or not 0 < stats[6] <= 16000000):
        raise ValueError('complete_native_geometry_counts_and_statistics_required')
    fused = fuse_declared(clearance, support)
    summary = value['summary']
    if (support.observations_sha256 != data.sha256 or support.library_sha256 != value['groundLibrarySha256']
            or support.declaration_sha256 != GroundDeclaration(.6, 0.).sha256
            or support.sha256 != summary['supportSha256'] or clearance.sha256 != summary['clearanceSha256']
            or fused.sha256 != summary['traversabilitySha256']
            or hashlib.sha256(fused.costs).hexdigest() != summary['traversabilityBytesSha256']
            or summary['cells'] != dict(zip(('free', 'occupied', 'unknown'), (fused.costs.count(i) for i in (0, 254, 255))))
            or summary['observationsSha256'] != data.sha256 or summary['scans'] != len(data.scans)
            or summary['rays'] != sum(len(scan.returns) for scan in data.scans)
            or summary['sequence'] != 0 or summary['mapRevision'] != data.revision
            or any(summary[key] is not False for key in ('navigationReady', 'physicalScaleValidated', 'groundSupportValidated'))
            or summary['offlineOnly'] is not True
            or any(type(summary[key]) not in (int, float) or not math.isfinite(summary[key])
                   or not 0 <= summary[key] <= 10. for key in ('processingSeconds', 'ingressToCompletionSeconds'))):
        raise ValueError('complete_native_terrain_bytes_and_provenance_required')
    cells = tuple({0: 0, 254: 100, 255: -1}[cost] for cost in fused.costs)
    points = [(spec.origin_x_metres + (index % spec.width + .5) * spec.resolution_metres,
               spec.origin_y_metres + (index // spec.width + .5) * spec.resolution_metres, .6)
              for index, cost in enumerate(fused.costs) if cost == 254]
    source = dict(width=spec.width, height=spec.height, resolution=spec.resolution_metres,
        origin=(spec.origin_x_metres, spec.origin_y_metres), cells=cells, points=points)
    evidence = dict(fixtureSha256=hashlib.sha256(raw).hexdigest(), classification=CLASSIFICATION,
        observationsSha256=data.sha256, traversabilitySha256=fused.sha256,
        groundLibrarySha256=value['groundLibrarySha256'], octomapLibrarySha256=value['octomapLibrarySha256'],
        sourcesSha256=value['sourcesSha256'], cells=summary['cells'],
        nativeBuild=value['nativeBuild'],
        minimumZMetres=spec.minimum_z_metres, maximumZMetres=spec.maximum_z_metres,
        liveFreshnessProven=False, physicalScaleValidated=False, groundSupportValidated=False)
    return source, evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ground-library', 'ground-build-report', 'octomap-library', 'octomap-build-report', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    for name in ('ground-sha256', 'octomap-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--case', choices=CASES, required=True)
    args = parser.parse_args()
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (60, 65))
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024, 128 * 1024))
    os.umask(0o077)
    provenance = native_provenance(args.ground_library, args.ground_sha256, args.ground_build_report,
        args.octomap_library, args.octomap_sha256, args.octomap_build_report)
    report = json.loads(args.octomap_build_report.read_bytes())
    projector = OctomapProjector(args.octomap_library, args.octomap_sha256, report['dependencies'])
    value = build(args.case, PatchworkSegmenter(args.ground_library, args.ground_sha256), projector, args.octomap_sha256, provenance)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
    _, evidence = load(args.output, args.case)
    print(json.dumps(evidence))
