from __future__ import annotations

import math
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from robot_local_navigation_v3.models import PathSafetyConfig
from robot_local_navigation_v3.path_clearance import (
    GridMapInfo,
    PathClearanceError,
    decode_png_occupancy,
    distance_to_blocked,
    inflate_obstacles,
    line_is_clear,
    line_clearance,
    plan_reference_line,
    world_line_is_clear,
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _write_gray_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    rows = b"".join(
        b"\x00" + bytes(pixels[y * width:(y + 1) * width])
        for y in range(height)
    )
    data = b"\x89PNG\r\n\x1a\n"
    data += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
    data += _png_chunk(b"IDAT", zlib.compress(rows))
    data += _png_chunk(b"IEND", b"")
    path.write_bytes(data)


class PathClearanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.map_dir = Path(self.temp_dir.name) / "7"
        self.map_dir.mkdir()
        self.width = 60
        self.height = 80
        self.pixels = bytearray([255]) * (self.width * self.height)
        self._write_metadata()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_metadata(self) -> None:
        (self.map_dir / "grid_map_info.txt").write_text(
            f"10\n0 {self.height - 1}\n{self.width} {self.height}\n",
            encoding="utf-8",
        )
        _write_gray_png(
            self.map_dir / "occupancy_map.png",
            self.width,
            self.height,
            self.pixels,
        )

    def _pixel_world(self, u: int, v: int) -> tuple[float, float]:
        return u / 10.0, (self.height - 1 - v) / 10.0

    def _plan(self) -> list[tuple[float, float]]:
        center = self.height // 2
        start_x, start_y = self._pixel_world(12, center)
        goal_x, goal_y = self._pixel_world(48, center)
        return plan_reference_line(
            map_dir=self.map_dir,
            start_x=start_x,
            start_y=start_y,
            goal_x=goal_x,
            goal_y=goal_y,
            config=PathSafetyConfig(robot_radius_m=0.2, extra_clearance_m=0.1),
        )

    def test_reference_line_detours_around_inflated_static_obstacle(self) -> None:
        center = self.height // 2
        for v in range(center - 12, center + 12):
            for u in range(28, 33):
                self.pixels[v * self.width + u] = 0
        self._write_metadata()

        route = self._plan()

        self.assertGreaterEqual(len(route), 3)
        info = GridMapInfo.load(self.map_dir / "grid_map_info.txt")
        width, height, occupied = decode_png_occupancy(
            self.map_dir / "occupancy_map.png"
        )
        blocked = inflate_obstacles(
            occupied,
            width,
            height,
            (0.2 + 0.1) * info.resolution_pixels_per_meter,
        )
        pixels = [info.world_to_pixel(x, y) for x, y in route]
        self.assertTrue(any(v != center for _, v in pixels))
        for start, end in zip(pixels, pixels[1:]):
            self.assertTrue(line_is_clear(start, end, blocked, width, height))

    def test_full_width_wall_rejects_route(self) -> None:
        for v in range(self.height):
            self.pixels[v * self.width + 30] = 0
        self._write_metadata()

        with self.assertRaisesRegex(PathClearanceError, "minimum global path clearance|no route remains"):
            self._plan()

    def test_two_obstacles_keep_the_global_route_in_the_clear_middle_lane(self) -> None:
        center = self.height // 2
        for v in range(center - 30, center - 18):
            for u in range(self.width):
                self.pixels[v * self.width + u] = 0
        for v in range(center + 18, center + 30):
            for u in range(self.width):
                self.pixels[v * self.width + u] = 0
        self._write_metadata()

        route = self._plan()

        info = GridMapInfo.load(self.map_dir / "grid_map_info.txt")
        width, height, occupied = decode_png_occupancy(
            self.map_dir / "occupancy_map.png"
        )
        blocked = inflate_obstacles(
            occupied,
            width,
            height,
            (0.2 + 0.1) * info.resolution_pixels_per_meter,
        )
        pixels = [info.world_to_pixel(x, y) for x, y in route]
        self.assertEqual(pixels[0], (12, center))
        self.assertEqual(pixels[-1], (48, center))
        self.assertTrue(all(center - 4 <= v <= center + 4 for _, v in pixels))
        for start, end in zip(pixels, pixels[1:]):
            self.assertTrue(line_is_clear(start, end, blocked, width, height))

    def test_reference_line_prefers_extra_clearance_from_local_avoidance_boundary(self) -> None:
        center = self.height // 2
        for v in range(center - 7, center + 8):
            for u in range(28, 33):
                self.pixels[v * self.width + u] = 0
        self._write_metadata()

        route = self._plan()

        info = GridMapInfo.load(self.map_dir / "grid_map_info.txt")
        width, height, occupied = decode_png_occupancy(
            self.map_dir / "occupancy_map.png"
        )
        blocked = inflate_obstacles(
            occupied,
            width,
            height,
            (0.2 + 0.1) * info.resolution_pixels_per_meter,
        )
        clearance = distance_to_blocked(blocked, width, height)
        pixels = [info.world_to_pixel(x, y) for x, y in route]
        self.assertGreaterEqual(len(pixels), 3)
        conservative_clearance = [
            max(0.0, value * 0.92 - 2.0) for value in clearance
        ]
        for start, end in zip(pixels, pixels[1:]):
            segment_clearance = line_clearance(
                start, end, blocked, width, height, conservative_clearance
            )
            self.assertIsNotNone(segment_clearance)
            self.assertGreaterEqual(segment_clearance, 3.5)

    def test_configured_radius_and_extra_clearance_move_route_farther_from_obstacle(self) -> None:
        center = self.height // 2
        self.width = 100
        self.pixels = bytearray([255]) * (self.width * self.height)
        for v in range(center - 7, center + 8):
            for u in range(48, 53):
                self.pixels[v * self.width + u] = 0
        self._write_metadata()

        info = GridMapInfo.load(self.map_dir / "grid_map_info.txt")
        width, height, occupied = decode_png_occupancy(
            self.map_dir / "occupancy_map.png"
        )
        obstacle_distance = distance_to_blocked(occupied, width, height)
        start = self._pixel_world(20, center)
        goal = self._pixel_world(80, center)

        def minimum_obstacle_distance(radius_m: float, extra_clearance_m: float) -> float:
            route = plan_reference_line(
                map_dir=self.map_dir,
                start_x=start[0],
                start_y=start[1],
                goal_x=goal[0],
                goal_y=goal[1],
                config=PathSafetyConfig(
                    robot_radius_m=radius_m,
                    extra_clearance_m=extra_clearance_m,
                ),
            )
            minimum = math.inf
            for segment_start, segment_end in zip(route, route[1:]):
                start_u = segment_start[0] * info.resolution_pixels_per_meter + info.origin_u
                start_v = info.origin_v - segment_start[1] * info.resolution_pixels_per_meter
                end_u = segment_end[0] * info.resolution_pixels_per_meter + info.origin_u
                end_v = info.origin_v - segment_end[1] * info.resolution_pixels_per_meter
                length = math.hypot(end_u - start_u, end_v - start_v)
                sample_count = max(1, math.ceil(length * 2.0))
                for sample in range(sample_count + 1):
                    ratio = sample / sample_count
                    x = int(round(start_u + (end_u - start_u) * ratio))
                    y = int(round(start_v + (end_v - start_v) * ratio))
                    minimum = min(minimum, obstacle_distance[y * width + x])
            return minimum

        baseline_distance = minimum_obstacle_distance(0.1, 0.1)
        increased_clearance_distance = minimum_obstacle_distance(0.3, 0.4)
        self.assertGreater(increased_clearance_distance, baseline_distance + 1.0)

    def test_clearance_requirement_rejects_a_route_without_a_wide_enough_lane(self) -> None:
        center = self.height // 2
        for v in range(center - 35, center + 35):
            self.pixels[v * self.width + 30] = 0
        self._write_metadata()

        with self.assertRaisesRegex(PathClearanceError, "no route remains"):
            self._plan()

    def test_narrow_middle_lane_is_rejected_after_footprint_inflation(self) -> None:
        center = self.height // 2
        for v in range(center - 24, center - 6):
            for u in range(self.width):
                self.pixels[v * self.width + u] = 0
        for v in range(center + 6, center + 24):
            for u in range(self.width):
                self.pixels[v * self.width + u] = 0
        self._write_metadata()

        with self.assertRaisesRegex(PathClearanceError, "inflated obstacle clearance|minimum global path clearance"):
            self._plan()

    def test_occupied_start_is_rejected(self) -> None:
        self.pixels[(self.height // 2) * self.width + 12] = 0
        self._write_metadata()

        with self.assertRaisesRegex(PathClearanceError, "start pose"):
            self._plan()

    def test_unmeasured_robot_radius_is_rejected(self) -> None:
        with self.assertRaisesRegex(PathClearanceError, "not configured"):
            plan_reference_line(
                map_dir=self.map_dir,
                start_x=1.2,
                start_y=(self.height - 1 - self.height // 2) / 10.0,
                goal_x=4.8,
                goal_y=(self.height - 1 - self.height // 2) / 10.0,
                config=PathSafetyConfig(robot_radius_m=None),
            )

    def test_start_and_goal_in_same_grid_cell_keep_both_world_positions(self) -> None:
        route = plan_reference_line(
            map_dir=self.map_dir,
            start_x=2.51,
            start_y=(self.height - 1 - self.height // 2) / 10.0,
            goal_x=2.54,
            goal_y=(self.height - 1 - self.height // 2) / 10.0 + 0.01,
            config=PathSafetyConfig(robot_radius_m=0.2, extra_clearance_m=0.1),
        )

        self.assertEqual(route, [
            (2.51, (self.height - 1 - self.height // 2) / 10.0),
            (2.54, (self.height - 1 - self.height // 2) / 10.0 + 0.01),
        ])

    def test_exact_start_and_goal_positions_are_rechecked_after_pixel_planning(self) -> None:
        center = self.height // 2
        start = (self._pixel_world(12, center)[0], self._pixel_world(12, center)[1])
        goal = (self._pixel_world(48, center)[0], self._pixel_world(48, center)[1])
        with patch("robot_local_navigation_v3.path_clearance.world_line_is_clear", return_value=False):
            with self.assertRaisesRegex(PathClearanceError, "exact reference line"):
                plan_reference_line(
                    map_dir=self.map_dir,
                    start_x=start[0],
                    start_y=start[1],
                    goal_x=goal[0],
                    goal_y=goal[1],
                    config=PathSafetyConfig(robot_radius_m=0.2, extra_clearance_m=0.1),
                )

    def test_world_reference_segment_checks_clearance_along_exact_coordinates(self) -> None:
        info = GridMapInfo(
            resolution_pixels_per_meter=1.0,
            origin_u=0.0,
            origin_v=0.0,
            width=12,
            height=12,
        )
        blocked = [False] * (info.width * info.height)
        clearance = [10.0] * (info.width * info.height)
        clearance[5 * info.width + 5] = 2.0

        self.assertFalse(
            world_line_is_clear(
                (2.0, -5.0),
                (8.0, -5.0),
                info,
                blocked,
                info.width,
                info.height,
                clearance,
                3.5,
            )
        )
        self.assertTrue(
            world_line_is_clear(
                (2.0, -7.0),
                (8.0, -7.0),
                info,
                blocked,
                info.width,
                info.height,
                clearance,
                3.5,
            )
        )
