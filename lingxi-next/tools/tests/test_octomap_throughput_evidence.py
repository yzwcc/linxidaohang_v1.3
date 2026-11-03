"""Native-only host evidence must preserve geometry and actual build identities."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import unittest

ROOT = Path(__file__).resolve().parents[2]


class OctomapThroughputEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.before, self.after = [json.loads((ROOT / 'docs/evidence' /
            ('terrain-native-' + name + '-20261103.json')).read_bytes()) for name in ('before', 'after')]

    def test_only_native_build_changes_and_candidate_is_current_source_bound(self):
        for field in ('benchmarkSha256', 'fixtureSha256', 'sourcesSha256', 'dependenciesSha256',
                      'groundLibrarySha256', 'encoding', 'trials', 'pythonVersion'):
            self.assertEqual(self.before[field], self.after[field])
        self.assertEqual('f64le', self.after['encoding'])
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(sha(ROOT / 'tools/benchmark_terrain_pipeline.py'), self.after['benchmarkSha256'])
        self.assertEqual(sha(ROOT / 'tools/terrain_nav2_fixture.py'), self.after['fixtureSha256'])
        self.assertEqual({name: sha(ROOT / 'robot-navigation/lingxi_navigation' / name)
            for name in ('occupancy.py', 'ground.py', 'terrain.py', 'terrain_protocol.py')},
            self.after['sourcesSha256'])
        self.assertNotEqual(self.before['octomapLibrarySha256'], self.after['octomapLibrarySha256'])
        for field in ('compiler', 'headersSha256', 'dependencies', 'octomapVersion'):
            self.assertEqual(self.before['octomapBuild'][field], self.after['octomapBuild'][field])
        self.assertEqual(sha(ROOT / 'robot-navigation/native-octomap/octomap_bridge.cpp'),
            self.after['octomapBuild']['sourceSha256'])
        self.assertEqual('b2d3c5720fdf956b167cce36bb9016fd34cd2445fd5cad3ba2a950075544999e',
            self.before['octomapBuild']['sourceSha256'])

    def test_all_twelve_raw_grids_statistics_and_wire_sizes_are_identical(self):
        expected = {(case, scans) for case in ('open', 'blocked', 'detour', 'unsupported-shortcut')
                    for scans in (2, 4, 12)}
        for report in (self.before, self.after):
            self.assertEqual(12, len(report['cases']))
            self.assertEqual(expected, {(row['case'], row['scans']) for row in report['cases']})
        for before, after in zip(self.before['cases'], self.after['cases']):
            for field in ('case', 'scans', 'rays', 'identity', 'wireBytes'):
                self.assertEqual(before[field], after[field])

    def test_actual_recorded_rerun_uses_the_same_current_native_build(self):
        replay = json.loads((ROOT / 'docs/evidence/registered-replay-20261103.json').read_bytes())
        self.assertEqual(self.after['octomapBuild'], replay['nativeOctomapBuild'])
        self.assertEqual(self.after['octomapBuildReportSha256'], replay['nativeOctomapBuildSha256'])
        self.assertTrue(replay['grade']['defaultBudgetAccepted'])
        self.assertEqual(replay['observedResults'], replay['grade']['observedTerrainResults'])
        self.assertEqual(0, replay['observedSupportedFreeCells'])
        for field in ('robotContacted', 'physicalMotionSent', 'arm64Executed', 'navigationReady'):
            self.assertIs(False, replay['boundaries'][field])

    def test_five_actual_samples_determine_each_aggregate_without_speed_gate(self):
        for report in (self.before, self.after):
            self.assertEqual(5, report['trials'])
            for row in report['cases']:
                self.assertEqual(5, len(row['timings']))
                for field in row['medianSeconds']:
                    samples = [value[field] for value in row['timings']]
                    self.assertTrue(all(math.isfinite(value) and value >= 0 for value in samples))
                    self.assertEqual(statistics.median(samples), row['medianSeconds'][field])
                    self.assertEqual(max(samples), row['maximumSeconds'][field])

    def test_build_report_integrity_and_no_target_or_navigation_acceptance(self):
        for report in (self.before, self.after):
            build = report['octomapBuild']
            self.assertEqual('offline_native_build_not_target_acceptance', build['purpose'])
            self.assertTrue(build['freshProcessLoadPassed'])
            self.assertEqual(report['octomapLibrarySha256'], build['nativeSha256'])
            self.assertEqual(report['dependenciesSha256'], build['dependencies'])
            raw = (json.dumps(build, indent=2, allow_nan=False) + '\n').encode('utf-8')
            self.assertEqual(hashlib.sha256(raw).hexdigest(), report['octomapBuildReportSha256'])
            self.assertEqual('synthetic_actual_native_host_throughput_only', report['classification'])
            for field in ('robotContacted', 'navigationReady', 'targetThroughputValidated'):
                self.assertIs(False, report[field])


if __name__ == '__main__':
    unittest.main()
