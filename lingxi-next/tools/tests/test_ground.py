"""Declared ground candidates never become metric navigation approval."""
import ctypes
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.ground import (GroundDeclaration, GroundLabels, PatchworkSegmenter,
    DeclaredGroundCandidate, DeclaredTraversability, GroundFrameMismatch,
    declared_ground_candidates, fuse_declared, validate_declared_ground_frame)
from lingxi_navigation.occupancy import (ClearanceProjection, DeclaredClearanceResult,
    DeclaredObservationBatch, LidarReturn, OctomapProjector, ScanObservation, canonical)
from lingxi_navigation.navfn import CostmapSnapshot


def observations(points=None, scans=2):
    points = points or ((1.01, .01, 0.), (1.03, .02, 0.), (1.02, .03, 0.))
    returns = tuple(LidarReturn((0., 0., .6), point) for point in points)
    return DeclaredObservationBatch('slam-' + 'a' * 32, 'b' * 64, 1, 'c' * 64,
        'd' * 64, 'e' * 64, tuple(ScanObservation(i, i + 1., 'f' * 64, returns) for i in range(scans)))


def spec():
    return ClearanceProjection(64, 64, .1, -3.2, -3.2)


def mocked_segmenter():
    value = PatchworkSegmenter.__new__(PatchworkSegmenter)
    value.sha256 = '1' * 64
    return value


def floor_points():
    return tuple((radius * math.cos(angle), radius * math.sin(angle), -.6)
                 for radius in (.5 + .08 * i for i in range(45))
                 for angle in (2 * math.pi * j / 128 for j in range(128)))


class GroundContractTests(unittest.TestCase):
    def test_explicit_bounded_ground_height_is_not_a_metric_calibration(self):
        base = GroundDeclaration(.6, 0.)
        for changes in ({'sensor_height_metres': True}, {'sensor_height_metres': .1},
                {'ground_z_metres': float('nan')}, {'maximum_range_metres': 100},
                {'minimum_range_metres': 4., 'maximum_range_metres': 4.5},
                {'height_tolerance_metres': .11}, {'maximum_height_spread_metres': 0.},
                {'minimum_points_per_cell': 2}, {'minimum_scans_per_cell': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(base, **changes)
        self.assertNotEqual(base.sha256, replace(base, sensor_height_metres=.7).sha256)

    def test_labels_require_complete_immutable_accounting_and_source_identity(self):
        for labels in (bytearray([0, 0, 0]), b'', bytes([0, 1, 255]), bytes(8193)):
            with self.subTest(size=len(labels)), self.assertRaises(ValueError):
                GroundLabels(labels, 'a' * 64, 'b' * 64, 'c' * 64)

    def candidate(self, data=None, declaration=None, labels=None, projection=None):
        data, declaration = data or observations(), declaration or GroundDeclaration(.6, 0.)
        def segment(points, declared):
            codes = labels if labels is not None else bytes(len(points))
            return GroundLabels(codes, hashlib.sha256(canonical(points)).hexdigest(), declared.sha256, '1' * 64)
        with patch.object(PatchworkSegmenter, 'segment', side_effect=segment):
            return declared_ground_candidates(data, projection or spec(), declaration, mocked_segmenter())

    def test_only_repeated_dense_observed_floor_supports_a_cell_no_hole_filling(self):
        result = self.candidate()
        self.assertEqual(1, result.costs.count(0))
        self.assertEqual(4095, result.costs.count(255))
        self.assertEqual(6, result.ground_returns)
        self.assertTrue(result.offline_only)
        self.assertFalse(result.navigation_ready)
        self.assertFalse(result.ground_support_validated)
        self.assertNotIsInstance(result, CostmapSnapshot)

    def test_one_scan_and_insufficient_density_remain_unknown(self):
        self.assertNotIn(0, self.candidate(observations(scans=1)).costs)
        self.assertNotIn(0, self.candidate(observations(points=((1., .01, 0.),))).costs)
        self.assertNotIn(0, self.candidate(declaration=GroundDeclaration(.6, 0., minimum_points_per_cell=7)).costs)

    def test_tiny_scans_remain_unclassified_without_native_invocation(self):
        data = observations(points=((1., .01, 0.),))
        with patch.object(PatchworkSegmenter, 'segment', side_effect=AssertionError('must not call')):
            result = declared_ground_candidates(data, spec(), GroundDeclaration(.6, 0.), mocked_segmenter())
        self.assertEqual(2, result.unclassified_returns)
        self.assertNotIn(0, result.costs)

    def test_partial_or_stale_labels_never_support_cells(self):
        data = observations()
        declaration = GroundDeclaration(.6, 0.)
        points = tuple((x, y, z - .6) for x, y, z in
                       (ray.endpoint_metres for ray in data.scans[0].returns))
        valid = GroundLabels(bytes(3), hashlib.sha256(canonical(points)).hexdigest(),
                             declaration.sha256, '1' * 64)
        for labels in (replace(valid, labels=bytes(4)), replace(valid, input_sha256='2' * 64),
                       replace(valid, declaration_sha256='3' * 64),
                       replace(valid, library_sha256='4' * 64), None):
            with self.subTest(labels=labels), patch.object(PatchworkSegmenter, 'segment', return_value=labels):
                with self.assertRaisesRegex(ValueError, 'scan_bound_ground_labels'):
                    declared_ground_candidates(data, spec(), declaration, mocked_segmenter())

    def test_step_platform_and_drop_returns_veto_a_previously_supported_cell(self):
        for z in (-2., -.12, .12, .5):
            points = observations().scans[0].returns
            data = observations(tuple(ray.endpoint_metres for ray in points) + ((1.02, .02, z),))
            result = self.candidate(data)
            with self.subTest(z=z):
                self.assertNotIn(0, result.costs)
                self.assertEqual(1, result.costs.count(254))

    def test_rough_height_or_disputed_classification_is_not_support(self):
        for codes in (bytes([0, 254, 0]), bytes([0, 255, 0])):
            self.assertNotIn(0, self.candidate(labels=codes).costs)
        points = ((1.01, .01, -.03), (1.03, .02, .03), (1.02, .03, 0.))
        self.assertNotIn(0, self.candidate(observations(points)).costs)

    def test_out_of_range_and_unobserved_cells_stay_unknown_even_if_labeled_ground(self):
        self.assertNotIn(0, self.candidate(declaration=GroundDeclaration(.6, 0., minimum_range_metres=2.)).costs)
        self.assertNotIn(0, self.candidate(observations(((12., .01, 0.),) * 3)).costs)

    def test_origin_height_and_per_scan_deskew_bounds_are_checked(self):
        data = observations()
        far = replace(data.scans[0].returns[0], origin_metres=(1., 0., .6))
        moved = replace(data, scans=(replace(data.scans[0], returns=(far,) + data.scans[0].returns[1:]),))
        for current, declaration in ((data, GroundDeclaration(.8, 0.)), (moved, GroundDeclaration(.6, 0.))):
            with self.subTest(case=declaration), self.assertRaises(ValueError):
                self.candidate(current, declaration)

    def test_all_scan_frames_are_checked_before_first_native_call(self):
        data = observations()
        bad = tuple(replace(ray, origin_metres=(0., 0., .8)) for ray in data.scans[-1].returns)
        data = replace(data, scans=data.scans[:-1] + (replace(data.scans[-1], returns=bad),))
        with patch.object(PatchworkSegmenter, 'segment') as native:
            with self.assertRaises(GroundFrameMismatch) as failure:
                declared_ground_candidates(data, spec(), GroundDeclaration(.6, 0.), mocked_segmenter())
            native.assert_not_called()
        self.assertEqual(1, failure.exception.ordinal)
        self.assertAlmostEqual(.2, failure.exception.height_error_metres)
        self.assertAlmostEqual(0., failure.exception.origin_span_metres)
        self.assertIn('not_physical_calibration', str(failure.exception))

    def test_preflight_rejects_wrong_types_and_reports_deskew_displacement(self):
        for data, declaration in ((None, GroundDeclaration(.6, 0.)), (observations(), None)):
            with self.assertRaises(ValueError):
                validate_declared_ground_frame(data, declaration)
        data = observations()
        scan = data.scans[0]
        shifted = replace(scan.returns[-1], origin_metres=(.36, 0., .6))
        data = replace(data, scans=(replace(scan, returns=scan.returns[:-1] + (shifted,)),))
        with self.assertRaises(GroundFrameMismatch) as failure:
            validate_declared_ground_frame(data, GroundDeclaration(.6, 0.))
        self.assertAlmostEqual(.36, failure.exception.origin_span_metres)

    def test_candidate_identity_binds_provenance_geometry_and_all_cells(self):
        result = self.candidate()
        for changed in (replace(result, observations_sha256='2' * 64), replace(result, declaration_sha256='2' * 64),
                replace(result, library_sha256='2' * 64), replace(result, costs=bytes([255] * 4096)),
                replace(result, ground_returns=7)):
            self.assertNotEqual(changed.sha256, result.sha256)
        for changes in ({'costs': b''}, {'costs': bytes([1] * 4096)}, {'scans': True},
                        {'ground_returns': -1}, {'library_sha256': 'missing'}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                replace(result, **changes)

    def test_fusion_requires_both_support_and_known_clearance(self):
        support = self.candidate()
        air = DeclaredClearanceResult(support.observations_sha256, spec(), bytes(4096), ())
        self.assertEqual(support.costs, fuse_declared(air, support).costs)
        self.assertNotIn(0, fuse_declared(replace(air, costs=bytes([255] * 4096)), support).costs)
        self.assertNotIn(0, fuse_declared(replace(air, costs=bytes([254] * 4096)), support).costs)
        self.assertFalse(fuse_declared(air, support).navigation_ready)

    def test_fusion_rejects_mismatched_sources_geometry_lengths_and_invalid_codes(self):
        support = self.candidate()
        air = DeclaredClearanceResult(support.observations_sha256, spec(), bytes(4096), ())
        for changes in ({'observations_sha256': '2' * 64}, {'projection': replace(spec(), origin_x_metres=-3.3)},
                        {'costs': b''}, {'costs': bytes([1] * 4096)}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                fuse_declared(replace(air, **changes), support)
        with self.assertRaises(ValueError):
            DeclaredTraversability('a' * 64, 'b' * 64, b'')


@unittest.skipUnless(os.environ.get('LINGXI_TEST_GROUND_LIBRARY'), 'requires actual pinned Patchwork++ library')
class NativeGroundTests(unittest.TestCase):
    def setUp(self):
        self.library = Path(os.environ['LINGXI_TEST_GROUND_LIBRARY'])
        self.sha256 = os.environ['LINGXI_TEST_GROUND_SHA256']
        self.native = PatchworkSegmenter(self.library, self.sha256)
        self.declaration = GroundDeclaration(.6, 0.)

    def test_actual_core_labels_floor_repeatably_after_unrelated_scan(self):
        points = floor_points()
        first = self.native.segment(points, self.declaration)
        self.assertGreater(first.labels.count(0), 1000)
        self.native.segment(tuple((x, y, z + .5) for x, y, z in points), self.declaration)
        self.assertEqual(first, self.native.segment(points, self.declaration))

    def test_actual_ground_core_candidate_support_not_metric_costmap(self):
        points = tuple((x, y, z + .6) for x, y, z in floor_points())
        result = declared_ground_candidates(observations(points), spec(), self.declaration, self.native)
        self.assertGreater(result.costs.count(0), 100)
        self.assertGreater(result.costs.count(255), 100)
        self.assertFalse(result.navigation_ready)

    def test_actual_sparse_plane_does_not_invent_supported_cells(self):
        result = self.native.segment(((1., 0., -.6), (2., 0., -.6), (3., 0., -.6)), self.declaration)
        self.assertNotIn(0, result.labels)
        candidate = declared_ground_candidates(observations(((1., 0., 0.), (2., 0., 0.), (3., 0., 0.))),
            spec(), self.declaration, self.native)
        self.assertNotIn(0, candidate.costs)

    @unittest.skipUnless(os.environ.get('LINGXI_TEST_OCTOMAP_LIBRARY'), 'requires actual pinned OctoMap library')
    def test_actual_patchwork_and_octomap_require_observed_support_and_clearance(self):
        projector = OctomapProjector(Path(os.environ['LINGXI_TEST_OCTOMAP_LIBRARY']),
            os.environ['LINGXI_TEST_OCTOMAP_SHA256'], json.loads(os.environ['LINGXI_TEST_OCTOMAP_DEPENDENCIES']))
        floor = observations(tuple((x, y, z + .6) for x, y, z in floor_points()))
        projection = replace(spec(), minimum_z_metres=.5, maximum_z_metres=.7)
        horizontal = tuple(LidarReturn((0., .01, z), (3., .01, z)) for z in (.55, .65))
        data = replace(floor, scans=floor.scans + (ScanObservation(2, 3., 'f' * 64, horizontal),))
        air = projector.project_declared(data, projection)
        support = declared_ground_candidates(data, projection, self.declaration, self.native)
        fused = fuse_declared(air, support)
        self.assertGreater(fused.costs.count(0), 0)
        self.assertLessEqual(fused.costs.count(0), min(air.costs.count(0), support.costs.count(0)))
        self.assertTrue(all(cost != 0 or a == b == 0
            for cost, a, b in zip(fused.costs, air.costs, support.costs)))
        self.assertNotIn(0, fuse_declared(air, replace(support, costs=bytes([255] * 4096))).costs)
        self.assertFalse(fused.navigation_ready)

    def test_native_failure_has_no_partial_output(self):
        xyz, labels, counts = (ctypes.c_double * 9)(*([float('nan')] * 9)), (ctypes.c_uint8 * 3)(7, 7, 7), (ctypes.c_uint64 * 3)(9, 9, 9)
        status = self.native.native.lingxi_ground_segment(xyz, 3, .6, .3, 10., labels, 3, counts)
        self.assertEqual(-1, status)
        self.assertEqual((7, 7, 7), tuple(labels))
        self.assertEqual((9, 9, 9), tuple(counts))
        self.assertIn(b'finite_bounded', self.native.native.lingxi_ground_error())

    def test_python_boundary_rejects_bad_inputs_hashes_and_thread_owner(self):
        for points in ((), floor_points() + floor_points(), ((True, 0., 0.),) * 3, ((0., 0., math.inf),) * 3):
            with self.subTest(count=len(points)), self.assertRaises(ValueError):
                self.native.segment(points, self.declaration)
        with self.assertRaises(ValueError):
            PatchworkSegmenter(self.library, '0' * 64)
        failures = []
        def other_thread():
            try:
                self.native.segment(floor_points(), self.declaration)
            except Exception as error:
                failures.append(str(error))
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(5)
        self.assertEqual(['single_patchwork_segmenter_owner_required'], failures)


if __name__ == '__main__':
    unittest.main()
