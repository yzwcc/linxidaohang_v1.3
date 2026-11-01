"""Source-bound synthetic native/ROS evidence and independent export grading."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).parent)]
from export_nav2_evidence import audit
from run_nav2_offline import assess_fused_costmaps, assess_native_costmaps, world
from test_export_nav2_evidence import fixture


def native_fixture():
    report = fixture()
    source, evidence = world('open'), dict(fixtureSha256='a' * 64)
    costs = bytes(255 if value == -1 else value for value in source['cells'])
    sample = dict(unknown=-1, obstacle=0, removed=False,
        nativeCosts=base64.b64encode(costs).decode(),
        nativeGeometry=dict(width=source['width'], height=source['height'], resolution=source['resolution'],
                            origin=list(source['origin'])))
    report.update(purpose='synthetic_native_terrain_nav2_integration_not_field_acceptance',
        nativeTerrain=evidence, dynamicObstaclePolicy='full_support_fused_revisions_no_secondary_clearing',
        costmapSamples=[sample])
    report['checks'].update(assess_fused_costmaps('open', [sample]))
    report['checks'].update(assess_native_costmaps(source, [sample]))
    return source, evidence, report


class ExportGradingTests(unittest.TestCase):
    def test_native_export_requires_the_unchanged_frozen_fixture(self):
        source, evidence, report = native_fixture()
        with patch('terrain_nav2_fixture.load', return_value=(source, evidence)):
            self.assertTrue(audit(report, terrain_fixture=Path('/synthetic.json'))['passed'])
            changed = copy.deepcopy(report)
            changed['nativeTerrain'] = {}
            with self.assertRaisesRegex(ValueError, 'unchanged_actual_native'):
                audit(changed, terrain_fixture=Path('/synthetic.json'))
        with self.assertRaisesRegex(ValueError, 'source_bound'):
            audit(report)

    def test_costmap_and_raw_trajectory_are_not_trusted_from_summary_flags(self):
        source, evidence, report = native_fixture()
        for change in (lambda r: r['costmapSamples'].clear(),
                       lambda r: r['costmapSamples'][0].update(nativeCosts=base64.b64encode(bytes(len(source['cells']))).decode()),
                       lambda r: r.update(finalPose=[0., 0., 0.]),
                       lambda r: r.update(syntheticOracleNotOctomap=True),
                       lambda r: r.update(fusedMapRevision=1)):
            changed = copy.deepcopy(report)
            change(changed)
            with patch('terrain_nav2_fixture.load', return_value=(source, evidence)), self.assertRaises(ValueError):
                audit(changed, terrain_fixture=Path('/synthetic.json'))


class CurrentEvidenceTests(unittest.TestCase):
    def test_all_actual_native_scenes_are_source_bound_and_offline_only(self):
        report = json.loads((ROOT / 'docs/evidence/native-terrain-nav2-20261101.json').read_bytes())
        self.assertEqual('synthetic_native_terrain_nav2_integration_not_field_acceptance', report['purpose'])
        for name, sha in report['sourceSha256'].items():
            self.assertEqual(sha, hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), name)
        self.assertEqual({'open', 'blocked', 'detour', 'unsupported-shortcut'}, {case['case'] for case in report['cases']})
        for key in ('liveFreshnessProven', 'physicalScaleValidated', 'groundSupportValidated', 'robotContacted',
                    'physicalMotionSent', 'navigationReady', 'productionCommandAgeProven',
                    'installedBinaryMatchesPinnedSourceVerified'):
            self.assertIs(False, report[key], key)
        for case in report['cases']:
            self.assertTrue(case['passed'])
            self.assertTrue(all(case['checks'].values()))
            self.assertFalse(case['syntheticOracleNotOctomap'])
            self.assertEqual((.1, 1.4), (case['nativeTerrain']['minimumZMetres'], case['nativeTerrain']['maximumZMetres']))
            for key in ('actualNativeGridGeometry', 'actualNativeUnknownBlocked', 'actualNativeObstaclesBlocked'):
                self.assertIs(True, case['checks'][key], key)
            self.assertRegex(case['reportSha256'], '^[0-9a-f]{64}$')
            self.assertTrue(case['nativeTerrain']['nativeBuild']['freshProcessLoadVerified'])


if __name__ == '__main__':
    unittest.main()
