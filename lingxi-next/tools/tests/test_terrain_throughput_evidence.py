"""Synthetic host timing evidence preserves exact outputs, not target acceptance."""
import hashlib
import json
import math
from pathlib import Path
import statistics
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ThroughputEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.before, self.after = [json.loads((ROOT / 'docs/evidence' /
            ('terrain-throughput-' + name + '-20261103.json')).read_bytes())
            for name in ('baseline', 'packed')]

    def test_current_sources_and_common_actual_native_identities(self):
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(sha(ROOT / 'tools/benchmark_terrain_pipeline.py'), self.after['benchmarkSha256'])
        self.assertEqual(sha(ROOT / 'tools/terrain_nav2_fixture.py'), self.after['fixtureSha256'])
        self.assertEqual({name: sha(ROOT / 'robot-navigation/lingxi_navigation' / name)
            for name in ('occupancy.py', 'ground.py', 'terrain.py', 'terrain_protocol.py')},
            self.after['sourcesSha256'])
        for key in ('benchmarkSha256', 'fixtureSha256', 'dependenciesSha256',
                    'groundLibrarySha256', 'octomapLibrarySha256', 'trials'):
            self.assertEqual(self.before[key], self.after[key])
        self.assertEqual(('json', 'f64le'), (self.before['encoding'], self.after['encoding']))

    def test_all_twelve_output_identities_and_statistics_match(self):
        expected = {(case, scans) for case in ('open', 'blocked', 'detour', 'unsupported-shortcut')
                    for scans in (2, 4, 12)}
        for report in (self.before, self.after):
            self.assertEqual(expected, {(row['case'], row['scans']) for row in report['cases']})
            self.assertEqual(12, len(report['cases']))
        for old, new in zip(self.before['cases'], self.after['cases']):
            for key in ('case', 'scans', 'rays', 'identity'):
                self.assertEqual(old[key], new[key])

    def test_recorded_samples_control_medians_and_maxima(self):
        for report in (self.before, self.after):
            self.assertEqual(5, report['trials'])
            for row in report['cases']:
                self.assertEqual(report['trials'], len(row['timings']))
                for key in row['medianSeconds']:
                    samples = [value[key] for value in row['timings']]
                    self.assertTrue(all(math.isfinite(value) and value >= 0 for value in samples))
                    self.assertEqual(statistics.median(samples), row['medianSeconds'][key])
                    self.assertEqual(max(samples), row['maximumSeconds'][key])

    def test_no_target_or_navigation_acceptance_and_no_universal_compression(self):
        for report in (self.before, self.after):
            self.assertEqual('synthetic_actual_native_host_throughput_only', report['classification'])
            for key in ('robotContacted', 'navigationReady', 'targetThroughputValidated'):
                self.assertIs(False, report[key])
        changes = [new['wireBytes'] - old['wireBytes']
                   for old, new in zip(self.before['cases'], self.after['cases'])]
        self.assertTrue(any(value > 0 for value in changes))
        self.assertTrue(any(value < 0 for value in changes))


if __name__ == '__main__':
    unittest.main()
