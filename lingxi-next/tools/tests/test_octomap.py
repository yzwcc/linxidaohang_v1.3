import ctypes
from dataclasses import replace
import json
import os
from pathlib import Path
import random
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-navigation"))
from lingxi_navigation.occupancy import (ClearanceProjection, LidarReturn, ObservationBatch,
    OctomapProjector, ScanObservation)
from lingxi_navigation.navfn import CostmapSnapshot, NavfnPlanner
from lingxi_navigation.traversability import GroundSupport, SupportPreservingFeed


def ray(origin=(.05, .55, .05), endpoint=(1.25, .55, .05)):
    return LidarReturn(origin, endpoint)


def batch(*scans):
    return ObservationBatch("slam-" + "a" * 32, "b" * 64, 1, "c" * 64,
        tuple(ScanObservation(index, index + 1., "d" * 64, tuple(returns))
              for index, returns in enumerate(scans or ((ray(),),))), "isolated_synthetic_rays")


def projection(**changes):
    return replace(ClearanceProjection(16, 16, .1, 0., 0., 0., .1, 10.), **changes)


class ObservationContractTests(unittest.TestCase):
    def test_default_metric_height_band_is_point_one_to_one_point_four(self):
        spec = ClearanceProjection(16, 16, .1, 0., 0.)
        self.assertEqual((.1, 1.4), (spec.minimum_z_metres, spec.maximum_z_metres))
        self.assertEqual((0., .1), (projection().minimum_z_metres, projection().maximum_z_metres))

    def test_returns_are_immutable_and_require_individual_origins(self):
        for origin, endpoint in (([0., 0., 0.], (1., 0., 0.)), ((0., 0.), (1., 0., 0.)),
                ((0., 0., 0.), (0., 0., 0.)), ((float("nan"), 0., 0.), (1., 0., 0.)),
                ((True, 0., 0.), (1., 0., 0.)), ((0., 0., 0.), (201., 0., 0.))):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                ray(origin, endpoint)

    def test_scan_is_nonempty_bounded_and_source_bound(self):
        scan = batch().scans[0]
        for changes in ({"returns": ()}, {"returns": [ray()]}, {"returns": (ray(),) * 8193},
                {"ordinal": True}, {"source_sha256": "missing"}, {"stamp": 0}, {"returns": ((1., 2., 3.),)}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                replace(scan, **changes)

    def test_complete_sequence_and_metric_map_identity_are_required(self):
        original = batch((ray(),), (ray(),))
        for changes in ({"map_id": "map-1.2"}, {"receipt_sha256": "none"},
                {"metric_evidence_sha256": "not-calibrated"}, {"frame": "IMU"}, {"units": "raw"},
                {"source_kind": "fused_display_pointcloud"}, {"revision": True}, {"scans": ()},
                {"scans": (original.scans[1], original.scans[0])},
                {"scans": (original.scans[0], replace(original.scans[1], stamp=1.))}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                replace(original, **changes)

    def test_hash_binds_origins_returns_timestamps_source_pose_revision_and_receipt(self):
        original = batch()
        scan = original.scans[0]
        alternatives = [replace(original, revision=2), replace(original, receipt_sha256="e" * 64),
            replace(original, metric_evidence_sha256="e" * 64), replace(original, map_id="slam-" + "e" * 32),
            replace(original, scans=(replace(scan, stamp=2.),)),
            replace(original, scans=(replace(scan, source_sha256="e" * 64),)),
            batch((ray(origin=(.15, .55, .05)),)), batch((ray(endpoint=(1.15, .55, .05)),))]
        self.assertEqual(original.sha256, batch().sha256)
        self.assertTrue(all(alternative.sha256 != original.sha256 for alternative in alternatives))

    def test_projection_is_aligned_bounded_and_explicitly_three_dimensional(self):
        for changes in ({"width": True}, {"width": 7}, {"width": 1025}, {"origin_x_metres": .05},
                {"minimum_z_metres": .05}, {"maximum_z_metres": 0.}, {"maximum_z_metres": 13.},
                {"resolution_metres": .001}, {"maximum_range_metres": 0.},
                {"maximum_range_metres": float("inf")}, {"width": 1024, "height": 1024,
                 "maximum_z_metres": 1.}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                projection(**changes)


@unittest.skipUnless(os.environ.get("LINGXI_TEST_OCTOMAP_LIBRARY"), "requires the actual pinned OctoMap library")
class NativeOctomapTests(unittest.TestCase):
    def setUp(self):
        self.library = Path(os.environ["LINGXI_TEST_OCTOMAP_LIBRARY"])
        self.sha256 = os.environ["LINGXI_TEST_OCTOMAP_SHA256"]
        self.dependencies = json.loads(os.environ["LINGXI_TEST_OCTOMAP_DEPENDENCIES"])
        self.projector = OctomapProjector(self.library, self.sha256, self.dependencies)

    def cell(self, result, x, y):
        return result.costmap.costs[y * result.costmap.width + x]

    def test_observed_free_hit_and_unknown_behind_hit_are_distinct(self):
        result = self.projector.project(batch(), projection())
        self.assertEqual([0] * 12 + [254, 255, 255, 255],
                         [self.cell(result, x, 5) for x in range(16)])
        self.assertEqual(255, self.cell(result, 5, 6))
        self.assertEqual((12, 1, 243), (result.free_cells, result.occupied_cells, result.unknown_cells))
        self.assertFalse(result.navigation_ready)
        self.assertEqual(batch().sha256, result.observations_sha256)

    def test_each_return_uses_its_own_observed_origin(self):
        observations = batch((ray(), ray((1.55, 1.05, .05), (.95, 1.05, .05))))
        result = self.projector.project(observations, projection())
        self.assertEqual(255, self.cell(result, 1, 10))
        self.assertEqual(254, self.cell(result, 9, 10))
        self.assertEqual([0] * 6, [self.cell(result, x, 10) for x in range(10, 16)])

    def test_scan_hit_wins_over_another_beams_free_traversal_regardless_of_order(self):
        near = ray(endpoint=(.65, .55, .05))
        for returns in ((near, ray()), (ray(), near)):
            result = self.projector.project(batch(returns), projection())
            self.assertEqual(254, self.cell(result, 6, 5))
            self.assertEqual(254, self.cell(result, 12, 5))

    def test_duplicate_returns_in_one_scan_do_not_accumulate_extra_evidence(self):
        near = ray(endpoint=(.65, .55, .05))
        sparse = self.projector.project(batch((near, ray())), projection())
        repeated = self.projector.project(batch((near, ray()) * 4096), projection())
        self.assertEqual(sparse.costmap, repeated.costmap)
        self.assertEqual(sparse.nodes, repeated.nodes)
        self.assertEqual(8192, repeated.rays)
        self.assertEqual(1, repeated.scans)

    def test_maximum_range_clipping_does_not_create_a_false_hit(self):
        result = self.projector.project(batch(), projection(maximum_range_metres=.6))
        self.assertEqual(0, result.occupied_cells)
        self.assertEqual(0, self.cell(result, 4, 5))
        self.assertEqual(255, self.cell(result, 7, 5))
        self.assertEqual(255, self.cell(result, 12, 5))

    def test_free_at_one_height_does_not_clear_an_unobserved_body_column(self):
        result = self.projector.project(batch(), projection(maximum_z_metres=.2))
        self.assertEqual(0, result.free_cells)
        self.assertEqual(255, self.cell(result, 4, 5))
        self.assertEqual(254, self.cell(result, 12, 5))

    def test_all_height_voxels_must_be_observed_free(self):
        top = ray((.05, .55, .15), (1.25, .55, .15))
        result = self.projector.project(batch((ray(), top)), projection(maximum_z_metres=.2))
        self.assertEqual(0, self.cell(result, 4, 5))
        self.assertEqual(254, self.cell(result, 12, 5))
        self.assertEqual(255, self.cell(result, 4, 6))

    def test_overhanging_hit_blocks_a_column_with_free_space_below(self):
        top = ray((.05, .55, .15), (.65, .55, .15))
        result = self.projector.project(batch((ray(), top)), projection(maximum_z_metres=.2))
        self.assertEqual(254, self.cell(result, 6, 5))
        self.assertEqual(255, self.cell(result, 8, 5))

    def test_negative_origin_and_lower_left_indexing_are_not_mirrored(self):
        observed = ray((-.75, -.45, .05), (.45, -.45, .05))
        result = self.projector.project(batch((observed,)), projection(origin_x_metres=-.8, origin_y_metres=-1.))
        self.assertEqual(0, self.cell(result, 2, 5))
        self.assertEqual(254, self.cell(result, 12, 5))
        self.assertEqual(255, self.cell(result, 2, 10))

    def test_probabilistic_updates_allow_new_observations_to_clear_an_old_hit(self):
        near = ray(endpoint=(.65, .55, .05))
        observations = batch((near,), *((ray(),) for _ in range(4)))
        result = self.projector.project(observations, projection())
        self.assertEqual(0, self.cell(result, 6, 5))
        self.assertEqual(254, self.cell(result, 12, 5))

    def test_native_revisions_clear_old_hits_without_opening_unsupported_air(self):
        near = batch((ray(endpoint=(.65, .55, .05)),))
        old = self.projector.project(near, projection())
        support_costs = bytearray([0] * 256)
        support_costs[5 * 16 + 4] = 255
        support = CostmapSnapshot(near.map_id, near.receipt_sha256, 0,
            16, 16, .1, 0., 0., bytes(support_costs))
        feed = SupportPreservingFeed(GroundSupport(support, 'e' * 64,
            near.metric_evidence_sha256, 'isolated_synthetic_ground'))
        first = feed.update(near, old)
        self.assertEqual(254, first.grid.costs[5 * 16 + 6])
        cleared = replace(batch((ray(endpoint=(.65, .55, .05)),),
            *((ray(),) for _ in range(4))), revision=2)
        native = self.projector.project(cleared, projection())
        second = feed.update(cleared, native)
        self.assertEqual(0, self.cell(native, 4, 5))
        self.assertEqual(255, second.grid.costs[5 * 16 + 4])
        self.assertEqual(0, second.grid.costs[5 * 16 + 6])
        self.assertEqual(255, second.grid.costs[6 * 16 + 6])
        self.assertEqual(254, second.grid.costs[5 * 16 + 12])
        self.assertNotEqual(first.sha256, second.sha256)
        self.assertEqual(native.sha256, second.clearance_sha256)
        self.assertFalse(second.navigation_ready)

    def test_pose_revision_rebuild_removes_old_free_cells(self):
        old = self.projector.project(batch(), projection())
        corrected = replace(batch((ray((.05, 1.05, .05), (1.25, 1.05, .05)),)), revision=2)
        new = self.projector.project(corrected, projection())
        self.assertEqual(0, self.cell(old, 4, 5))
        self.assertEqual(255, self.cell(new, 4, 5))
        self.assertEqual(0, self.cell(new, 4, 10))
        self.assertEqual(2, new.costmap.revision)
        self.assertNotEqual(old.sha256, new.sha256)
        self.assertEqual(old.sha256, self.projector.project(batch(), projection()).sha256)

    def test_exhausted_work_does_not_publish_partial_grid_or_stale_success(self):
        self.projector.project(batch(), projection())
        with self.assertRaisesRegex(RuntimeError, "work_budget_exhausted"):
            self.projector.project(batch(), projection(), maximum_work_units=1)
        native = self.projector.native
        rays = (ctypes.c_double * 6)(.05, .55, .05, 1.25, .55, .05)
        ends = (ctypes.c_uint32 * 1)(1)
        output, stats = (ctypes.c_uint8 * 256)(*([123] * 256)), (ctypes.c_uint64 * 7)(*([123] * 7))
        self.assertEqual(-1, native.lingxi_octomap_project(rays, 1, ends, 1, .1, 10., 0., 0.,
            0., .1, 16, 16, 1, output, 256, stats))
        self.assertEqual([123] * 256, list(output))
        self.assertEqual([123] * 7, list(stats))

    def test_owner_and_binary_and_loaded_core_identity_are_enforced(self):
        with self.assertRaises(ValueError):
            OctomapProjector(self.library, "0" * 64, self.dependencies)
        with self.assertRaisesRegex(ValueError, "actual_loaded"):
            OctomapProjector(self.library, self.sha256, dict.fromkeys(self.dependencies, "0" * 64))
        failures = []
        def other_thread():
            try:
                self.projector.project(batch(), projection())
            except RuntimeError as error:
                failures.append(str(error))
        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(["single_octomap_projector_owner_required"], failures)

    @unittest.skipUnless(os.environ.get("LINGXI_TEST_NAVFN_LIBRARY"), "requires the actual NavFn library")
    def test_observed_volume_feeds_real_navfn_and_unknown_goals_remain_blocked(self):
        spec = projection(width=64, height=48, maximum_z_metres=.2)
        returns = tuple(ray((.05, (y + .5) * .1, z), (6.35, (y + .5) * .1, z))
                        for y in range(40) for z in (.05, .15))
        result = self.projector.project(batch(returns), spec)
        planner = NavfnPlanner(Path(os.environ["LINGXI_TEST_NAVFN_LIBRARY"]), os.environ["LINGXI_TEST_NAVFN_SHA256"])
        planned = planner.plan(result.costmap, (.65, 2.05), (5.45, 2.05))
        self.assertEqual("planned", planned.status)
        self.assertEqual(result.costmap.sha256, planned.costmap_sha256)
        self.assertFalse(planned.navigation_ready)
        self.assertEqual("no_path", planner.plan(result.costmap, (.65, 2.05), (5.45, 4.45)).status)


@unittest.skipUnless(os.environ.get('LINGXI_TEST_OCTOMAP_LIBRARY') and
    os.environ.get('LINGXI_TEST_OCTOMAP_BASELINE_LIBRARY'), 'requires both actual native builds')
class NativeOctomapDifferentialTests(unittest.TestCase):
    def setUp(self):
        dependencies = json.loads(os.environ['LINGXI_TEST_OCTOMAP_DEPENDENCIES'])
        self.before, self.after = [OctomapProjector(Path(os.environ[prefix + '_LIBRARY']),
            os.environ[prefix + '_SHA256'], dependencies)
            for prefix in ('LINGXI_TEST_OCTOMAP_BASELINE', 'LINGXI_TEST_OCTOMAP')]

    def test_exact_grids_statistics_and_hashes_for_moving_origins_clipping_and_revisions(self):
        generator = random.Random(20261103)
        for case in range(48):
            scans = []
            for index in range(1 + case % 5):
                returns = [ray((generator.uniform(-.7, .7), generator.uniform(-.7, .7),
                    generator.choice((.05, .15, .25))),
                    (generator.uniform(-1.5, 1.5), generator.uniform(-1.5, 1.5),
                     generator.choice((.05, .15, .25)))) for _ in range(48)]
                near = ray(endpoint=(.65, .55, .05))
                returns.extend((near, ray(), near, ray()))
                if index % 2:
                    returns.reverse()
                scans.append(tuple(returns))
            observations = replace(batch(*scans), revision=1 + case)
            spec = projection(width=32, height=32, origin_x_metres=-1.6,
                origin_y_metres=-1.6, maximum_z_metres=(.1, .2, .3)[case % 3],
                maximum_range_metres=(.1, .6, 3.)[case % 3])
            with self.subTest(case=case):
                old = self.before.project(observations, spec)
                new = self.after.project(observations, spec)
                self.assertEqual(old, new)
                self.assertEqual(old.sha256, new.sha256)
                self.assertEqual(old.costmap.sha256, new.costmap.sha256)

    def test_native_failure_results_and_unpublished_output_match(self):
        def call(projector, *, work=4000000, end=1, coordinate=.05):
            rays = (ctypes.c_double * 6)(coordinate, .55, .05, 1.25, .55, .05)
            ends = (ctypes.c_uint32 * 1)(end)
            output = (ctypes.c_uint8 * 256)(*([123] * 256))
            stats = (ctypes.c_uint64 * 7)(*([123] * 7))
            native = projector.native
            result = native.lingxi_octomap_project(rays, 1, ends, 1, .1, 10.,
                0., 0., 0., .1, 16, 16, work, output, 256, stats)
            return result, native.lingxi_octomap_error(), bytes(output), tuple(stats)
        for changes in ({'work': 1}, {'end': 0}, {'end': 2}, {'coordinate': float('nan')},
                        {'coordinate': 501.}, {'work': 16000001}):
            with self.subTest(changes=changes):
                before, after = call(self.before, **changes), call(self.after, **changes)
                self.assertEqual(before, after)
                self.assertEqual(-1, after[0])
                self.assertEqual(bytes([123] * 256), after[2])
                self.assertEqual((123,) * 7, after[3])


if __name__ == "__main__":
    unittest.main()
