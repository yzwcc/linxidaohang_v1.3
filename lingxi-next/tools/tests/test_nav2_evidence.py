"""Current six-scene integration receipt, not a replacement for ROS trials."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class EvidenceTests(unittest.TestCase):
    def test_native_dds_probe_is_source_bound_and_retains_timing_scope(self):
        report = json.loads((ROOT / 'docs/evidence/humble-publication-probe-20261101.json').read_bytes())
        self.assertEqual(1, report['schemaVersion'])
        self.assertEqual('isolated_humble_dds_publication_probe_not_field_acceptance', report['purpose'])
        self.assertEqual({'tools/humble_velocity_source.py', 'tools/probe_humble_publication.py',
                          'tools/nav2-offline-isolated.sh'}, set(report['sourceSha256']))
        for name, sha in report['sourceSha256'].items():
            self.assertEqual(sha, hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), name)
        self.assertRegex(report['installedExecutorSha256'], '^[0-9a-f]{64}$')
        self.assertEqual('humble', report['rosDistro'])
        self.assertEqual('rmw_fastrtps_cpp', report['middleware'])
        self.assertEqual('dds_publication_expired_or_future', report['delayedPublicationRejected'])
        for key in ('passed', 'freshPublicationAccepted', 'invalidSourceCannotRevive',
                    'retiredReaderInert', 'freshGenerationAccepted', 'ddsPublicationTimeVerified'):
            self.assertIs(True, report[key], key)
        for key in ('controllerComputationAgeProven', 'physicalMotionSent', 'robotContact',
                    'navigationReady'):
            self.assertIs(False, report[key], key)

    def test_current_round_is_source_bound_and_retains_physical_gaps(self):
        report = json.loads((ROOT / 'docs/evidence/nav2-publication-age-20261101.json').read_bytes())
        for name, sha in report['sourceSha256'].items():
            self.assertEqual(sha, hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), name)
        self.assertTrue(report['controllerExecuted'])
        self.assertTrue(report['syntheticInputs'])
        self.assertTrue(report['observedDdsPublicationTimestamp'])
        self.assertEqual('velocity_smoother_dds_write_not_controller_compute', report['publicationTimestampScope'])
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
            self.assertGreaterEqual(value['publicationSamplesVerified'], value['commandCount'])
            self.assertLessEqual(value['maximumPublicationAgeSeconds'], .15)
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
