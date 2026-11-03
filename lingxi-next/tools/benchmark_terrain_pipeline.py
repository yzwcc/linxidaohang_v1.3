"""Measure actual native terrain on isolated synthetic rays, never target acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


class NativeTimer:
    def __init__(self, native, name):
        self.native, self.name, self.seconds = native, name, 0.
        self.function = getattr(native, name)

    def __getattr__(self, name):
        return self.call if name == self.name else getattr(self.native, name)

    def call(self, *args):
        started = time.perf_counter()
        try:
            return self.function(*args)
        finally:
            self.seconds += time.perf_counter() - started


def run(args):
    sys.path[:0] = [str(args.python_source), str(ROOT / 'tools')]
    from dataclasses import replace
    from lingxi_navigation.ground import GroundDeclaration, PatchworkSegmenter
    from lingxi_navigation.occupancy import OctomapProjector
    from lingxi_navigation.terrain import TerrainBudget, TerrainProducer
    from lingxi_navigation.terrain_protocol import encode_observations, decode_observations, frame, decode
    from terrain_nav2_fixture import acquisition, projection

    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    projector = OctomapProjector(args.octomap_library, sha(args.octomap_library),
        json.loads(args.octomap_build_report.read_bytes())['dependencies'])
    segmenter = PatchworkSegmenter(args.ground_library, sha(args.ground_library))
    ground = NativeTimer(segmenter.native, 'lingxi_ground_segment')
    octomap = NativeTimer(projector.native, 'lingxi_octomap_project')
    segmenter.native, projector.native = ground, octomap
    budget = TerrainBudget(maximum_rays=131072, maximum_processing_seconds=10.,
                           maximum_age_seconds=15., maximum_work_units=16000000)
    report = dict(schemaVersion=1, classification='synthetic_actual_native_host_throughput_only',
        robotContacted=False, navigationReady=False, targetThroughputValidated=False,
        pythonVersion=sys.version, groundLibrarySha256=sha(args.ground_library),
        octomapLibrarySha256=sha(args.octomap_library), trials=args.trials,
        encoding=args.encoding, dependenciesSha256=json.loads(args.octomap_build_report.read_bytes())['dependencies'],
        fixtureSha256=sha(ROOT / 'tools/terrain_nav2_fixture.py'),
        benchmarkSha256=sha(Path(__file__).resolve()),
        sourcesSha256={name: sha(args.python_source / 'lingxi_navigation' / name)
                       for name in ('occupancy.py', 'ground.py', 'terrain.py', 'terrain_protocol.py')}, cases=[])
    for case in args.cases:
        data = acquisition(case)
        for scans in args.scans:
            value = replace(data, scans=data.scans[:scans])
            if len(value.scans) != scans:
                raise ValueError('requested_synthetic_scan_count_unavailable')
            timings, expected = [], None
            for _ in range(args.trials):
                producer = TerrainProducer(projector, segmenter, projection(), GroundDeclaration(.6, 0.), budget)
                ground.seconds = octomap.seconds = 0.
                start = time.perf_counter()
                encoded = (encode_observations(value, budget, packed=True) if args.encoding == 'f64le'
                           else encode_observations(value, budget))
                encoded_at = time.perf_counter()
                raw = frame(encoded)
                serialized_at = time.perf_counter()
                parsed = decode(raw[4:])
                parsed_at = time.perf_counter()
                restored = decode_observations(parsed, budget)
                decoded_at = time.perf_counter()
                result = producer.update(restored, sequence=0, received_at=time.monotonic())
                finished = time.perf_counter()
                identity = dict(observationsSha256=restored.sha256,
                    clearanceSha256=result.clearance.sha256, supportSha256=result.support.sha256,
                    traversabilitySha256=result.fused.sha256,
                    costsSha256=sha_bytes(result.fused.costs), statistics=result.clearance.statistics,
                    cells=[result.fused.costs.count(code) for code in (0, 254, 255)])
                if restored != value or restored.sha256 != value.sha256 or expected not in (None, identity):
                    raise ValueError('repeatable_exact_terrain_output_required')
                expected = identity
                timings.append(dict(encoding=encoded_at-start, serialization=serialized_at-encoded_at,
                    parsing=parsed_at-serialized_at, reconstruction=decoded_at-parsed_at,
                    pipeline=finished-decoded_at, nativeGround=ground.seconds, nativeOctomap=octomap.seconds,
                    pythonPipeline=finished-decoded_at-ground.seconds-octomap.seconds, total=finished-start))
            report['cases'].append(dict(case=case, scans=scans,
                rays=sum(len(scan.returns) for scan in value.scans), wireBytes=len(raw), identity=expected,
                medianSeconds={key: statistics.median(row[key] for row in timings) for key in timings[0]},
                maximumSeconds={key: max(row[key] for row in timings) for key in timings[0]}, timings=timings))
    return report


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ground-library', 'octomap-library', 'octomap-build-report', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--python-source', type=Path, default=ROOT / 'robot-navigation')
    parser.add_argument('--encoding', choices=('json', 'f64le'), default='f64le')
    parser.add_argument('--cases', nargs='+', choices=('open', 'blocked', 'detour', 'unsupported-shortcut'),
                        default=['open', 'blocked', 'detour', 'unsupported-shortcut'])
    parser.add_argument('--scans', nargs='+', type=int, choices=(2, 4, 12), default=[2, 4, 12])
    parser.add_argument('--trials', type=int, choices=range(1, 21), default=5)
    args = parser.parse_args()
    result = run(args)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(dict(classification=result['classification'], encoding=args.encoding,
        cases=[dict(case=row['case'], scans=row['scans'], wireBytes=row['wireBytes'],
                    medianSeconds=row['medianSeconds']) for row in result['cases']])))
