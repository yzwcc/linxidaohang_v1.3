"""Current six-scene integration receipt, not a replacement for ROS trials."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class EvidenceTests(unittest.TestCase):
    def test_current_round_is_source_bound_and_retains_physical_gaps(self):
        report = json.loads((ROOT / 'docs/evidence/nav2-support-fusion-20261101.json').read_bytes())
        for name, sha in report['sourceSha256'].items():
            self.assertEqual(sha, hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), name)
        self.assertTrue(report['controllerExecuted'])
        self.assertTrue(report['syntheticInputs'])
        for key in ('groundSupportMeasured', 'observedTwistHasSourceTimestamp',
                    'productionCommandAgeProven', 'physicalMotionSent', 'navigationReady'):
            self.assertIs(False, report[key], key)
        self.assertIs(False, report['upstream']['installedBinaryMatchesPinnedSourceVerified'])
        self.assertEqual({'open', 'blocked', 'detour', 'appearing-wall',
                          'disappearing-obstacle', 'unsupported-shortcut'},
                         {value['case'] for value in report['cases']})
        for value in report['cases']:
            self.assertTrue(value['passed'])
            self.assertTrue(all(value['checks'].values()))
            self.assertRegex(value['reportSha256'], '^[0-9a-f]{64}$')
            minimum = value['minimumSweptObstacleDistanceMetres']
            if minimum is not None:
                self.assertGreaterEqual(minimum, report['requiredEnvelopeRadiusMetres'])
            fused = value['case'] in ('disappearing-obstacle', 'unsupported-shortcut')
            self.assertIs(fused, value['syntheticOracleNotOctomap'])
            self.assertEqual('full_support_fused_revisions_no_secondary_clearing' if fused
                             else 'mark_only_latched_no_automatic_clearing',
                             value['dynamicObstaclePolicy'])


if __name__ == '__main__':
    unittest.main()
