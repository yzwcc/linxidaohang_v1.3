"""Continuous terrain replacement/freshness is distinct from field acceptance."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'robot-navigation'), str(Path(__file__).parent)]
from lingxi_navigation.terrain import TerrainBudget, TerrainProducer, recorded_windows
from lingxi_navigation.ground import (GroundDeclaration, GroundLabels, PatchworkSegmenter,
    declared_ground_candidates, fuse_declared)
from lingxi_navigation.occupancy import (DeclaredClearanceResult, LidarReturn, OctomapProjector,
    ScanObservation, canonical)
from test_ground import floor_points, observations, spec


class Clock:
    value = 10.

    def __call__(self):
        return self.value


class TerrainTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.projector = OctomapProjector.__new__(OctomapProjector)
        self.segmenter = PatchworkSegmenter.__new__(PatchworkSegmenter)
        self.segmenter.sha256 = '1' * 64
        self.projection, self.declaration = spec(), GroundDeclaration(.6, 0.)
        def segment(points, declaration):
            return GroundLabels(bytes(len(points)), hashlib.sha256(canonical(points)).hexdigest(),
                                declaration.sha256, self.segmenter.sha256)
        def project(data, projection, **kwargs):
            costs = bytes(projection.width * projection.height)
            return DeclaredClearanceResult(data.sha256, projection, costs,
                (sum(len(scan.returns) for scan in data.scans), len(data.scans), 1, len(costs), 0, 0, 1))
        self.segment = self.enterContext(patch.object(PatchworkSegmenter, 'segment', side_effect=segment))
        self.project = self.enterContext(patch.object(OctomapProjector, 'project_declared', side_effect=project))
        self.producer = TerrainProducer(self.projector, self.segmenter, self.projection, self.declaration, clock=self.clock)

    def update(self, data=None, sequence=0, received_at=None):
        return self.producer.update(data or observations(), sequence=sequence,
                                    received_at=self.clock.value if received_at is None else received_at)

    def test_default_clearance_remains_point_one_to_one_point_four(self):
        self.assertEqual((.1, 1.4), (self.projection.minimum_z_metres, self.projection.maximum_z_metres))
        result = self.update()
        self.assertEqual(1, result.fused.costs.count(0))
        self.assertEqual(result.fused.costs.count(255), result.offline_occupancy_values().count(-1))
        self.assertFalse(result.summary()['navigationReady'])
        self.assertTrue(result.summary()['offlineOnly'])
        self.assertIs(self.producer.current(), result)

    def test_bounded_budget_and_component_types(self):
        for change in ({'maximum_scans': True}, {'maximum_scans': 129}, {'maximum_rays': 262145},
                {'maximum_span_seconds': float('inf')}, {'maximum_processing_seconds': 0.},
                {'maximum_age_seconds': True}, {'maximum_work_units': 16000001}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(TerrainBudget(), **change)
        with self.assertRaises(ValueError):
            TerrainProducer(object(), self.segmenter, self.projection, self.declaration)

    def test_complete_replacement_removes_support_at_old_corrected_coordinates(self):
        first = self.update()
        old = first.fused.costs.index(0)
        data = observations(((2.01, .01, 0.), (2.03, .02, 0.), (2.02, .03, 0.)))
        second = self.update(replace(data, revision=2), sequence=1)
        self.assertEqual(255, second.fused.costs[old])
        self.assertEqual(1, second.fused.costs.count(0))
        self.assertNotEqual(first.fused.sha256, second.fused.sha256)

    def test_new_clear_air_without_new_ground_cannot_reuse_old_support(self):
        self.update()
        data = observations(((1., .01, .6),), scans=2)
        result = self.update(replace(data, revision=2), sequence=1)
        self.assertNotIn(0, result.fused.costs)

    def test_expiration_never_leaves_a_cached_navigable_grid(self):
        self.update()
        self.clock.value = 13.
        self.assertIsNone(self.producer.current())
        self.assertIsNone(self.producer.snapshot)

    def test_same_capture_correction_cannot_refresh_observation_age(self):
        first = self.update()
        self.clock.value = 12.
        result = self.update(replace(observations(), revision=2), sequence=1)
        self.assertEqual(first.expires_at, result.expires_at)
        self.clock.value = 13.
        with self.assertRaisesRegex(TimeoutError, 'expired'):
            self.update(replace(observations(), revision=3), sequence=2)
        self.assertIsNone(self.producer.current())

    def test_new_capture_gets_its_own_ingress_age(self):
        self.update()
        self.clock.value = 12.
        data = replace(observations(), scans=tuple(replace(scan, stamp=scan.stamp + 1) for scan in observations().scans))
        result = self.update(data, sequence=1)
        self.assertEqual(15., result.expires_at)

    def test_old_or_duplicate_sequence_and_retired_correction_are_not_published(self):
        self.update(replace(observations(), revision=2))
        for sequence, data in ((0, observations()), (1, observations())):
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                self.update(data, sequence=sequence)
            self.assertIsNone(self.producer.current())

    def test_changed_map_receipt_binding_or_geometry_requires_a_new_owner(self):
        for key, value in (('map_id', 'slam-' + '2' * 32), ('receipt_sha256', '2' * 64),
                ('binding_sha256', '2' * 64), ('geometry_declaration_sha256', '2' * 64)):
            self.update(sequence=self.producer.sequence + 1)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.update(replace(observations(), **{key: value}), sequence=self.producer.sequence + 1)
            self.assertIsNone(self.producer.current())

    def test_old_timestamp_or_changed_capture_identity_cannot_reenter(self):
        self.update()
        for data in (replace(observations(), scans=tuple(replace(scan, stamp=scan.stamp - .5) for scan in observations().scans)),
                replace(observations(), scans=tuple(replace(scan, source_sha256='2' * 64) for scan in observations().scans))):
            with self.assertRaisesRegex(ValueError, 'capture'):
                self.update(data, sequence=1)
            self.assertIsNone(self.producer.current())

    def test_oversized_windows_fail_before_native_work(self):
        for budget, data in ((TerrainBudget(maximum_rays=3), observations()),
                (TerrainBudget(maximum_scans=2), observations(scans=3)),
                (TerrainBudget(maximum_span_seconds=.5), observations())):
            self.producer = TerrainProducer(self.projector, self.segmenter, self.projection, self.declaration,
                                           budget, clock=self.clock)
            with self.assertRaisesRegex(ValueError, 'budget'):
                self.update(data)
        self.segment.assert_not_called()
        self.project.assert_not_called()

    def test_future_old_ingress_or_backward_clock_invalidates_cache(self):
        self.update()
        for received in (11., -1., float('nan')):
            with self.assertRaises(ValueError):
                self.update(sequence=1, received_at=received)
            self.assertIsNone(self.producer.current())
        with self.assertRaises(TimeoutError):
            self.update(sequence=1, received_at=1.)
        self.clock.value = 9.
        with self.assertRaisesRegex(ValueError, 'monotonic'):
            self.producer.current()

    def test_native_failures_invalidate_result_and_consume_attempted_generation(self):
        self.update()
        self.project.side_effect = RuntimeError('native failure')
        with self.assertRaises(RuntimeError):
            self.update(replace(observations(), revision=2), sequence=1)
        self.assertIsNone(self.producer.current())
        with self.assertRaises(ValueError):
            self.update(sequence=2)

    def test_ground_deadline_prevents_clearance_call_and_publication(self):
        segment = self.segment.side_effect
        def slow(points, declaration):
            self.clock.value += 2.1
            return segment(points, declaration)
        self.segment.side_effect = slow
        with self.assertRaises(TimeoutError):
            self.update()
        self.project.assert_not_called()
        self.assertIsNone(self.producer.current())

    def test_late_clearance_result_is_not_published(self):
        project = self.project.side_effect
        def slow(*args, **kwargs):
            result = project(*args, **kwargs)
            self.clock.value += 2.1
            return result
        self.project.side_effect = slow
        with self.assertRaises(TimeoutError):
            self.update()
        self.assertIsNone(self.producer.current())

    def test_incomplete_or_mismatched_native_results_are_not_published(self):
        project = self.project.side_effect
        for change in ({'statistics': ()}, {'statistics': (6, 2, 1, 4096, 0, 0, 4000001)},
                {'statistics': (5, 2, 1, 4096, 0, 0, 1)}, {'observations_sha256': '2' * 64},
                {'projection': replace(self.projection, origin_x_metres=-3.3)}):
            self.project.side_effect = lambda *a, **kw: replace(project(*a, **kw), **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.update(sequence=self.producer.sequence + 1)
            self.assertIsNone(self.producer.current())

    def test_wrong_ground_height_does_not_reach_clearance(self):
        scans = tuple(replace(scan, returns=tuple(replace(ray, origin_metres=(0., 0., .8))
            for ray in scan.returns)) for scan in observations().scans)
        with self.assertRaises(ValueError):
            self.update(replace(observations(), scans=scans))
        self.project.assert_not_called()

    def test_owner_thread_required(self):
        errors = []
        def run():
            try:
                self.producer.current()
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(2)
        self.assertEqual(['single_terrain_producer_owner_required'], errors)


class RecordedWindowTests(unittest.TestCase):
    def test_bounds_reindexing_original_source_identity_and_map_revision(self):
        data = observations(scans=31)
        budget = TerrainBudget(maximum_scans=8, maximum_rays=18, maximum_span_seconds=4.)
        windows = list(recorded_windows(data, budget, stride=4))
        self.assertEqual(31, windows[-1].source_end)
        self.assertEqual(list(range(len(windows))), [window.sequence for window in windows])
        covered = set()
        for window in windows:
            batch = window.observations
            covered.update(range(window.source_start, window.source_end))
            self.assertEqual(data.revision, batch.revision)
            self.assertEqual(data.binding_sha256, batch.binding_sha256)
            self.assertLessEqual(len(batch.scans), 8)
            self.assertLessEqual(sum(len(scan.returns) for scan in batch.scans), 18)
            self.assertLessEqual(batch.scans[-1].stamp - batch.scans[0].stamp, 4.)
            self.assertEqual(list(range(len(batch.scans))), [scan.ordinal for scan in batch.scans])
            self.assertEqual(data.scans[window.source_start].returns, batch.scans[0].returns)
            self.assertNotEqual(data.selection_sha256, batch.selection_sha256)
        self.assertEqual(set(range(31)), covered)

    def test_no_silent_dropping_of_a_single_over_budget_scan(self):
        data = observations(((1., 0., 0.),) * 4)
        with self.assertRaises(ValueError):
            list(recorded_windows(data, TerrainBudget(maximum_rays=3)))
        for stride in (0, True, 33):
            with self.assertRaises(ValueError):
                list(recorded_windows(observations(), stride=stride))


@unittest.skipUnless(os.environ.get('LINGXI_TEST_GROUND_LIBRARY') and os.environ.get('LINGXI_TEST_OCTOMAP_LIBRARY'),
                     'requires actual pinned ground and OctoMap libraries')
class NativeTerrainTests(unittest.TestCase):
    def setUp(self):
        self.segmenter = PatchworkSegmenter(Path(os.environ['LINGXI_TEST_GROUND_LIBRARY']), os.environ['LINGXI_TEST_GROUND_SHA256'])
        self.projector = OctomapProjector(Path(os.environ['LINGXI_TEST_OCTOMAP_LIBRARY']),
            os.environ['LINGXI_TEST_OCTOMAP_SHA256'], json.loads(os.environ['LINGXI_TEST_OCTOMAP_DEPENDENCIES']))
        self.projection, self.declaration = replace(spec(), minimum_z_metres=.5, maximum_z_metres=.7), GroundDeclaration(.6, 0.)
        self.producer = TerrainProducer(self.projector, self.segmenter, self.projection, self.declaration,
            TerrainBudget(maximum_processing_seconds=10., maximum_age_seconds=15.))
        floor = observations(tuple((x, y, z + .6) for x, y, z in floor_points()))
        self.horizontal = tuple(LidarReturn((0., .01, z), (3., .01, z)) for z in (.55, .65))
        self.data = replace(floor, scans=floor.scans + (ScanObservation(2, 3., 'f' * 64, self.horizontal),))

    def test_actual_native_producer_supports_only_both_observed_floor_and_air(self):
        import time
        result = self.producer.update(self.data, sequence=0, received_at=time.monotonic())
        self.assertGreater(result.fused.costs.count(0), 0)
        self.assertTrue(all(cost != 0 or a == b == 0 for cost, a, b in
            zip(result.fused.costs, result.clearance.costs, result.support.costs)))
        self.assertFalse(result.fused.navigation_ready)

    def test_actual_next_window_does_not_retain_old_floor_support(self):
        import time
        self.producer.update(self.data, sequence=0, received_at=time.monotonic())
        scans = tuple(ScanObservation(i, 4. + i, 'f' * 64, self.horizontal) for i in range(2))
        result = self.producer.update(replace(self.data, scans=scans), sequence=1, received_at=time.monotonic())
        self.assertNotIn(0, result.fused.costs)
        self.assertGreater(result.clearance.costs.count(0), 0)

    def test_actual_corrected_window_matches_fresh_native_rebuild(self):
        import time
        self.producer.update(self.data, sequence=0, received_at=time.monotonic())
        corrected = replace(self.data, revision=2, scans=tuple(replace(scan, returns=tuple(
            replace(ray, endpoint_metres=(ray.endpoint_metres[0] + .2, *ray.endpoint_metres[1:])) for ray in scan.returns))
            for scan in self.data.scans))
        result = self.producer.update(corrected, sequence=1, received_at=time.monotonic())
        fresh = fuse_declared(self.projector.project_declared(corrected, self.projection),
            declared_ground_candidates(corrected, self.projection, self.declaration, self.segmenter))
        self.assertEqual(fresh.sha256, result.fused.sha256)


if __name__ == '__main__':
    unittest.main()
