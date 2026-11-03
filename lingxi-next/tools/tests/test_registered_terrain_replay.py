"""Recorded-chain accounting and raw-grid grades cannot imply navigation readiness."""
import base64
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'robot-navigation')]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'scipy', 'small_gicp'))
if AVAILABLE:
    import run_registered_terrain_replay as runner
    from lingxi_navigation.occupancy import ClearanceProjection
    from lingxi_navigation.terrain import TerrainBudget


@unittest.skipUnless(AVAILABLE, 'requires isolated numerical localization dependencies')
class RecordedChainTests(unittest.TestCase):
    def setUp(self):
        self.projection = asdict(ClearanceProjection(8, 8, .1, 0., 0.))
        costs = bytes([255] * 63 + [254])
        counts = dict(callbacks=6, initializationSkips=1, copies=5, takes=5, discardedOnClose=0)
        self.value = dict(packets=5, registeredPackets=2, registeredReturns=6, seedAttempts=1,
            pointStateCounts=dict(unseeded=1, acquiring=2, tracking=2), projection=self.projection,
            budget=asdict(TerrainBudget()), maximumPendingNativeJobs=1, nativeFailureReason=None,
            submissions=[dict(sequence=4, oldestReceipt=10., latestReceipt=10.1, firstStamp=100.,
                lastStamp=100.1, scans=2, sourceReturns=6, selectedReturns=6,
                observationsSha256='a' * 64, projectionWorkUnits=1200,
                workEvictedScans=0, workEvictedReturns=0)],
            observedResults=[dict(sequence=4, receivedAt=10., observedAt=10.2, expiresAt=13.,
                costsSha256=hashlib.sha256(costs).hexdigest(), costsBase64=base64.b64encode(costs).decode(),
                cells={'0': 0, '254': 1, '255': 63})],
            final=dict(beforeState='tracking', laneState='ready', laneFailure=None,
                retired=True, gridAbsent=True, workerClosed=True, terrainOwnerClosed=True,
                terrainChildCleaned=True, pointCounts=counts.copy(), acquisitionCounts=counts.copy()))

    def grade(self, value=None):
        return runner.grade_chain(self.value if value is None else value, 6, self.projection)

    def test_complete_default_diagnostic_passes_without_promoting_unknown_cells(self):
        result = self.grade()
        self.assertTrue(result['defaultBudgetAccepted'])
        self.assertEqual(1, result['observedTerrainResults'])
        self.assertFalse(result['supportedFreeCellsObserved'])
        self.assertFalse(result['navigationReady'])
        self.assertFalse(result['pendingAtEnd'])

    def test_committed_aggregate_matches_current_sources_and_keeps_acceptance_boundaries(self):
        value = json.loads((ROOT / 'docs/evidence/registered-replay-20261103.json').read_bytes())
        self.assertEqual('paced_recorded_registered_terrain_diagnostic_not_navigation_acceptance', value['purpose'])
        self.assertEqual({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in runner.SOURCES}, value['sourcesSha256'])
        self.assertEqual(runner.BOUNDARIES, value['boundaries'])
        self.assertEqual(asdict(TerrainBudget()), value['budget'])
        self.assertEqual((4144, 3947, 197, 196, 165, 164, 161, 1223474), tuple(value[key] for key in
            ('sensorMessages', 'imuMessages', 'cloudMessages', 'processedScans', 'estimatedPoses',
             'pointPackets', 'registeredPackets', 'registeredReturns')))
        self.assertEqual((24, 23, 0), tuple(value[key] for key in
            ('submissions', 'observedResults', 'observedSupportedFreeCells')))
        self.assertTrue(value['grade']['defaultBudgetAccepted'])
        self.assertTrue(value['grade']['pendingAtEnd'])
        self.assertFalse(value['grade']['navigationReady'])
        self.assertFalse(value['grade']['supportedFreeCellsObserved'])
        self.assertIsNone(value['nativeFailureReason'])
        self.assertTrue(all(len(digest) == 64 for digest in value['privateEvidenceSha256'].values()))

    def test_workload_failure_is_valid_closed_diagnostic_not_success(self):
        self.value['nativeFailureReason'] = 'octomap_work_budget_exhausted'
        self.value['final'].update(laneState='retired', laneFailure='terrain_lane_failed')
        result = self.grade()
        self.assertTrue(result['retirementVerified'])
        self.assertFalse(result['defaultBudgetAccepted'])
        self.assertFalse(result['navigationReady'])

    def test_eof_pending_work_is_exposed_not_claimed_completed(self):
        self.value['final']['laneState'] = 'busy'
        result = self.grade()
        self.assertTrue(result['pendingAtEnd'])
        self.assertFalse(result['navigationReady'])

    def test_mutated_counts_retirement_budget_or_projection_are_rejected(self):
        changes = [lambda item: item.update(packets=4),
            lambda item: item['pointStateCounts'].update(tracking=1),
            lambda item: item['final'].update(gridAbsent=False),
            lambda item: item['final'].update(terrainChildCleaned=False),
            lambda item: item['final']['acquisitionCounts'].update(takes=4),
            lambda item: item['budget'].update(maximum_work_units=16000000),
            lambda item: item['projection'].update(width=16)]
        for change in changes:
            item = deepcopy(self.value)
            change(item)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.grade(item)

    def test_partial_nonfinite_overbudget_or_retimed_windows_are_rejected(self):
        for change in ({'oldestReceipt': float('nan')}, {'latestReceipt': float('inf')},
                {'firstStamp': 0.}, {'lastStamp': 105.}, {'sourceReturns': 65537},
                {'selectedReturns': 5}, {'projectionWorkUnits': 4000001},
                {'scans': True}, {'observationsSha256': 'bad'}, {'workEvictedScans': 3}):
            item = deepcopy(self.value)
            item['submissions'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.grade(item)

    def test_stale_duplicate_unsubmitted_or_nonfinite_results_are_rejected(self):
        for change in ({'receivedAt': 10.01}, {'observedAt': 13.}, {'observedAt': float('nan')},
                {'expiresAt': float('inf')}, {'sequence': 3}):
            item = deepcopy(self.value)
            item['observedResults'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.grade(item)
        item = deepcopy(self.value)
        item['observedResults'] *= 2
        with self.assertRaises(ValueError):
            self.grade(item)

    def test_actual_raw_bytes_not_summary_counts_control_acceptance(self):
        row = self.value['observedResults'][0]
        for change in ({'costsBase64': ''}, {'costsBase64': '!' * len(row['costsBase64'])},
                {'costsBase64': base64.b64encode(bytes([1] * 64)).decode()},
                {'costsBase64': base64.b64encode(bytes(64)).decode()}, {'costsSha256': 'b' * 64},
                {'cells': {'0': 1, '254': 0, '255': 63}}):
            item = deepcopy(self.value)
            item['observedResults'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.grade(item)


if __name__ == '__main__':
    unittest.main()
