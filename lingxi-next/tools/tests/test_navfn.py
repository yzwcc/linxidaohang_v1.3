"""Contract checks and actual upstream NavFn tests on isolated synthetic costmaps."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-navigation"))
from lingxi_navigation.navfn import CostmapSnapshot, NavfnPlanner, UPSTREAM_COMMIT


def snapshot(costs=None, **changes):
    return CostmapSnapshot("slam-" + "a" * 32, "b" * 64, 1, 64, 48, .1, -3., -2.,
                           bytes(64 * 48) if costs is None else costs, **changes)


def world(grid, x, y):
    return grid.origin_x_metres + (x + .5) * grid.resolution_metres, grid.origin_y_metres + (y + .5) * grid.resolution_metres


class CostmapContractTests(unittest.TestCase):
    def test_explicit_engine_pin_matches_lock(self):
        lock = json.loads((ROOT / "robot-navigation/navfn.lock.json").read_bytes())
        self.assertEqual(UPSTREAM_COMMIT, lock["commit"])

    def test_dimensions_cost_bytes_and_identity_are_bounded(self):
        original = snapshot()
        for changes in ({"width": True}, {"height": 7}, {"width": 2049},
                        {"revision": True}, {"revision": -1}, {"revision": 2**53},
                        {"costs": bytearray(original.costs)}, {"costs": b""},
                        {"map_id": "arbitrary"}, {"receipt_sha256": "invalid"}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                replace(original, **changes)

    def test_occupancy_grid_encoding_and_unbound_coordinates_are_not_accepted(self):
        for changes in ({"encoding": "occupancy_grid_int8"}, {"frame": "lidar_chest_front"},
                        {"resolution_metres": float("nan")}, {"resolution_metres": 0},
                        {"resolution_metres": True}, {"origin_x_metres": float("inf")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(snapshot(), **changes)

    def test_snapshot_digest_binds_map_revision_geometry_and_all_cost_bytes(self):
        original = snapshot()
        self.assertEqual(original.sha256, snapshot().sha256)
        for changes in ({"revision": 2}, {"receipt_sha256": "c" * 64},
                        {"map_id": "slam-" + "c" * 32}, {"origin_y_metres": 0.},
                        {"resolution_metres": .2}, {"costs": b"\xff" + original.costs[1:]}):
            with self.subTest(changes=list(changes)):
                self.assertNotEqual(original.sha256, replace(original, **changes).sha256)

    def test_world_cell_conversion_preserves_ros_lower_left_origin(self):
        grid = snapshot()
        self.assertEqual((6, 20), grid.cell(world(grid, 6, 20)))
        self.assertEqual((54, 40), grid.cell(world(grid, 54, 40)))
        for position in ((-3.00001, 0.), (float("nan"), 0.), (True, 1.), (1.,),
                         world(grid, 0, 20), world(grid, 63, 20)):
            with self.subTest(position=position), self.assertRaises(ValueError):
                grid.cell(position)


@unittest.skipUnless(os.environ.get("LINGXI_TEST_NAVFN_LIBRARY"), "requires the actual hash-verified native NavFn build")
class NativeNavfnTests(unittest.TestCase):
    def setUp(self):
        self.library = Path(os.environ["LINGXI_TEST_NAVFN_LIBRARY"])
        self.sha256 = os.environ["LINGXI_TEST_NAVFN_SHA256"]
        self.planner = NavfnPlanner(self.library, self.sha256)

    def plan(self, grid, **kwargs):
        return self.planner.plan(grid, world(grid, 6, 20), world(grid, 54, 20), **kwargs)

    def test_actual_core_open_route_has_expected_direction_endpoints_and_binding(self):
        grid = snapshot()
        before = hashlib.sha256(grid.costs).hexdigest()
        result = self.plan(grid)
        self.assertEqual("planned", result.status)
        self.assertGreater(len(result.points_metres), 40)
        self.assertEqual(world(grid, 6, 20), result.points_metres[0])
        self.assertEqual(world(grid, 54, 20), result.points_metres[-1])
        self.assertEqual((grid.map_id, grid.receipt_sha256, grid.revision, grid.sha256),
                         (result.map_id, result.receipt_sha256, result.revision, result.costmap_sha256))
        self.assertFalse(result.navigation_ready)
        self.assertEqual(before, hashlib.sha256(grid.costs).hexdigest())

    def test_wall_detour_uses_observed_gap_without_crossing_blocked_cells(self):
        costs = bytearray(64 * 48)
        for y in range(36):
            for x in range(29, 32):
                costs[y * 64 + x] = 254
        grid = snapshot(bytes(costs))
        result = self.plan(grid)
        self.assertEqual("planned", result.status)
        cells = [grid.cell(point) for point in result.points_metres]
        self.assertGreaterEqual(max(y for _, y in cells), 36)
        self.assertTrue(all(grid.costs[y * grid.width + x] < 253 for x, y in cells))

    def test_solid_unknown_inscribed_and_lethal_walls_have_no_plan(self):
        for value in (253, 254, 255):
            costs = bytearray(64 * 48)
            for y in range(48):
                costs[y * 64 + 30] = value
            with self.subTest(value=value):
                result = self.plan(snapshot(bytes(costs)))
                self.assertEqual("no_path", result.status)
                self.assertEqual((), result.points_metres)

    def test_high_cost_band_prefers_lower_cost_detour(self):
        costs = bytearray(64 * 48)
        for y in range(16, 25):
            for x in range(20, 44):
                costs[y * 64 + x] = 240
        grid = snapshot(bytes(costs))
        result = self.plan(grid)
        self.assertEqual("planned", result.status)
        self.assertTrue(any(abs(grid.cell(point)[1] - 20) >= 5 for point in result.points_metres))

    def test_blocked_goal_does_not_return_an_old_successful_plan(self):
        self.assertEqual("planned", self.plan(snapshot()).status)
        costs = bytearray(64 * 48)
        costs[20 * 64 + 54] = 255
        result = self.plan(replace(snapshot(bytes(costs)), revision=2))
        self.assertEqual("no_path", result.status)
        self.assertEqual((), result.points_metres)
        self.assertEqual(2, result.revision)

    def test_exhausted_path_budget_does_not_publish_a_partial_plan(self):
        result = self.plan(snapshot(), maximum_path_points=2)
        self.assertEqual("no_path", result.status)
        self.assertEqual((), result.points_metres)

    def test_same_cell_goal_returns_one_cell_centre(self):
        grid = snapshot()
        start = world(grid, 6, 20)
        result = self.planner.plan(grid, start, start)
        self.assertEqual((start,), result.points_metres)

    def test_owner_and_binary_identity_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "hash_verified"):
            NavfnPlanner(self.library, "0" * 64)
        failures = []
        def other_thread():
            try:
                self.plan(snapshot())
            except RuntimeError as error:
                failures.append(str(error))
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(["single_navfn_planner_owner_required"], failures)


if __name__ == "__main__":
    unittest.main()
