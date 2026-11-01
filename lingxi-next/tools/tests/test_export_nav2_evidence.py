"""Exporter rejection tests use fabricated reports, not ROS execution evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from export_nav2_evidence import (FALSE_FLAGS, PREPARATION_SOURCES, ROOT as SOURCE_ROOT,
                                 RUNNER, audit, collect, digest)
from run_nav2_offline import assess_trial, world


def fixture():
    poses, commands, plans = [[0., 0., 0.], [2., 0., 0.]], [[.1, 0., 0.]], [[[0., 0.], [2., 0.]]]
    state = dict(phase='finished', requiresStop=False, outputQuiescent=True,
                 offlineOnly=True, physicalMotionSent=False, navigationReady=False)
    assessment = assess_trial('open', world('open'), poses, commands, plans, state)
    return dict(case='open', purpose='synthetic_planar_nav2_integration_not_field_acceptance',
        preparationSources={name: digest(SOURCE_ROOT / name) for name in PREPARATION_SOURCES},
        runnerSha256=digest(SOURCE_ROOT / RUNNER), **assessment,
        lifecycleStates=[3] * 4, syntheticInputs=True, **dict.fromkeys(FALSE_FLAGS, False),
        dynamicObstaclePolicy='mark_only_latched_no_automatic_clearing',
        syntheticOracleNotOctomap=False, runtimeState=state, trajectory=poses,
        observedCommands=commands, globalPlans=plans, finalPose=poses[-1],
        commandCount=1, controllerExecuted=True, maximumAbsY=0., elapsedSeconds=1.,
        observedDdsPublicationTimestamp=True, publicationSamplesVerified=1,
        maximumPublicationAgeSeconds=.01,
        publicationTimestampScope='velocity_smoother_dds_write_not_controller_compute',
        fusedMapRevision=None, fusedSnapshotSha256=None)


class ExportTests(unittest.TestCase):
    def test_current_fixture_can_be_graded_without_claiming_real_execution(self):
        result = audit(fixture())
        self.assertTrue(result['passed'])
        self.assertFalse(result['syntheticOracleNotOctomap'])

    def test_source_or_runner_changes_are_not_waived(self):
        report = fixture()
        for name in PREPARATION_SOURCES:
            altered = copy.deepcopy(report)
            altered['preparationSources'][name] = '0' * 64
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'source_bound'):
                audit(altered)
        report['runnerSha256'] = '0' * 64
        with self.assertRaises(ValueError):
            audit(report)

    def test_blocked_path_may_produce_no_publications_without_fabricated_samples(self):
        report = fixture()
        report.update(case='blocked', trajectory=[[0., 0., 0.]],
                      observedCommands=[], commandCount=0, controllerExecuted=False,
                      finalPose=[0., 0., 0.], globalPlans=[], publicationSamplesVerified=0,
                      maximumPublicationAgeSeconds=0.)
        report['runtimeState']['phase'] = 'failed'
        report.update(assess_trial('blocked', world('blocked'), report['trajectory'], [], [],
                                  report['runtimeState']))
        self.assertTrue(audit(report)['passed'])
        for key, value in (('publicationSamplesVerified', -1),
                           ('maximumPublicationAgeSeconds', .01)):
            changed = copy.deepcopy(report)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit(changed)

    def test_physical_claims_and_wrong_policy_are_rejected(self):
        for key, value in [*((key, True) for key in FALSE_FLAGS),
                           ('syntheticInputs', False), ('syntheticOracleNotOctomap', True),
                           ('dynamicObstaclePolicy', 'automatic_clearing')]:
            report = fixture()
            report[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit(report)

    def test_raw_commands_and_summary_are_regraded(self):
        for change in (lambda r: r['observedCommands'].append([99., 0., 0.]),
                       lambda r: r['checks'].update(extra=True),
                       lambda r: r.update(commandCount=99),
                       lambda r: r.update(finalPose=[0., 0., 0.])):
            report = fixture()
            change(report)
            with self.assertRaises(ValueError):
                audit(report)

    def test_missing_unbounded_or_promoted_publication_claim_is_rejected(self):
        for key, value in (('observedDdsPublicationTimestamp', False),
                           ('publicationSamplesVerified', 0),
                           ('publicationSamplesVerified', True),
                           ('maximumPublicationAgeSeconds', .151),
                           ('maximumPublicationAgeSeconds', float('nan')),
                           ('maximumPublicationAgeSeconds', True),
                           ('publicationTimestampScope', 'controller_compute_proven')):
            report = fixture()
            report[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit(report)

    def test_partial_or_duplicate_collection_cannot_be_exported(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'fixture.json'
            path.write_text(json.dumps(fixture()), encoding='utf-8')
            for paths, error in (([path], 'all_six'), ([path, path], 'one_report')):
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                    collect(paths)

    def test_duplicate_json_keys_and_nonfinite_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'invalid.json'
            for data in ('{"case":"open","case":"blocked"}', '{"x":NaN}'):
                path.write_text(data, encoding='utf-8')
                with self.assertRaises(ValueError):
                    collect([path])


if __name__ == '__main__':
    unittest.main()
