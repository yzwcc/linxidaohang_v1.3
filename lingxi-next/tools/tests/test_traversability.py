"""Map/support fusion, not sensor/ground-estimation or physical acceptance."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.navfn import CostmapSnapshot
from lingxi_navigation.occupancy import (ClearanceProjection, DeclaredClearanceResult,
    LidarReturn, ObservationBatch, OccupancyResult, ScanObservation)
from lingxi_navigation.traversability import GroundSupport, SupportPreservingFeed


def fixture(costs=None, ground=None, revision=1):
    costs = bytes(64) if costs is None else bytes(costs)
    ground = bytes(64) if ground is None else bytes(ground)
    grid = CostmapSnapshot('slam-' + 'a' * 32, 'b' * 64, revision, 8, 8, .1, 0., 0., costs)
    support = GroundSupport(replace(grid, costs=ground, revision=0), 'c' * 64, 'd' * 64,
                            'isolated_synthetic_ground')
    observations = ObservationBatch(grid.map_id, grid.receipt_sha256, revision, 'd' * 64,
        (ScanObservation(0, 1., 'e' * 64, (LidarReturn((0., 0., .2), (1., 0., .2)),)),),
        'isolated_synthetic_rays')
    projection = ClearanceProjection(8, 8, .1, 0., 0.)
    clearance = OccupancyResult(grid, observations.sha256, projection, 1, 1, 1,
        *(costs.count(value) for value in (0, 254, 255)), 1)
    return support, observations, clearance


class FusionTests(unittest.TestCase):
    def test_complete_truth_table_unknown_is_never_free(self):
        air = [a for a in (0, 254, 255) for g in (0, 254, 255)]
        ground = [g for a in (0, 254, 255) for g in (0, 254, 255)]
        support, observations, clearance = fixture(air + [255] * 55, ground + [255] * 55)
        result = SupportPreservingFeed(support).update(observations, clearance)
        self.assertEqual(bytes([0, 254, 255, 254, 254, 254, 255, 254, 255]), result.grid.costs[:9])
        self.assertEqual([0, 100, -1, 100, 100, 100, -1, 100, -1], result.occupancy_values()[:9])
        self.assertTrue(result.offline_only)
        self.assertFalse(result.navigation_ready)
        self.assertEqual(clearance.costmap.costs[:9], bytes(air))

    def test_only_new_observed_free_revision_can_remove_obstacle(self):
        support, observations, clearance = fixture([254] + [255] * 63)
        feed = SupportPreservingFeed(support)
        first = feed.update(observations, clearance)
        _, second_observations, second_clearance = fixture([0] + [255] * 63, revision=2)
        second = feed.update(second_observations, second_clearance)
        self.assertEqual(254, first.grid.costs[0])
        self.assertEqual(0, second.grid.costs[0])
        self.assertEqual(first.grid.costs[1:], second.grid.costs[1:])
        self.assertNotEqual(first.sha256, second.sha256)
        for obs, result in ((observations, clearance), (second_observations, second_clearance)):
            with self.assertRaisesRegex(ValueError, 'current_same_map'):
                feed.update(obs, result)

    def test_clearing_does_not_open_unsupported_or_prohibited_ground(self):
        support, obs, clearance = fixture([254, 254] + [255] * 62, [255, 254] + [0] * 62)
        feed = SupportPreservingFeed(support)
        feed.update(obs, clearance)
        _, obs, clearance = fixture([0, 0] + [255] * 62, revision=2)
        self.assertEqual(bytes([255, 254]), feed.update(obs, clearance).grid.costs[:2])

    def test_cross_map_geometry_units_and_metric_provenance_are_rejected(self):
        support, obs, clearance = fixture()
        for grid in (replace(clearance.costmap, map_id='slam-' + 'f' * 32),
                     replace(clearance.costmap, receipt_sha256='f' * 64),
                     replace(clearance.costmap, origin_x_metres=.1),
                     replace(clearance.costmap, resolution_metres=.2)):
            with self.subTest(grid=grid), self.assertRaises(ValueError):
                SupportPreservingFeed(support).update(obs, replace(clearance, costmap=grid))
        for changes in (dict(metric_evidence_sha256='f' * 64),
                        dict(source_kind='registered_per_return_lidar'), dict(revision=2)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                SupportPreservingFeed(support).update(replace(obs, **changes), clearance)

    def test_incomplete_or_declared_projection_cannot_enter_metric_feed(self):
        support, obs, clearance = fixture()
        declared = DeclaredClearanceResult(obs.sha256, clearance.projection, bytes(64), (1,) * 7)
        for result in (declared, replace(clearance, observations_sha256='f' * 64),
                       replace(clearance, free_cells=63), replace(clearance, rays=2),
                       replace(clearance, scans=2),
                       replace(clearance, projection=replace(clearance.projection, width=9)),
                       replace(clearance, costmap=replace(clearance.costmap, costs=bytes([1] * 64)))):
            with self.subTest(result=type(result)), self.assertRaises(ValueError):
                SupportPreservingFeed(support).update(obs, result)

    def test_invalid_revision_does_not_consume_feed_and_foreign_thread_rejected(self):
        support, obs, clearance = fixture()
        feed = SupportPreservingFeed(support)
        with self.assertRaises(ValueError):
            feed.update(obs, replace(clearance, scans=2))
        errors = []
        def run():
            try:
                feed.update(obs, clearance)
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(2.)
        self.assertEqual(['single_traversability_feed_owner_required'], errors)
        self.assertEqual(1, feed.update(obs, clearance).grid.revision)

    def test_support_contract_and_identity_are_explicit(self):
        support, obs, clearance = fixture()
        for changes in (dict(source_kind='point_free_space'), dict(evidence_sha256=''),
                        dict(grid=replace(support.grid, costs=bytes([1] * 64)))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(support, **changes)
        with self.assertRaises(ValueError):
            SupportPreservingFeed(clearance)
        a = SupportPreservingFeed(support).update(obs, clearance)
        b = SupportPreservingFeed(replace(support, evidence_sha256='f' * 64)).update(obs, clearance)
        self.assertNotEqual(a.sha256, b.sha256)


if __name__ == '__main__':
    unittest.main()
