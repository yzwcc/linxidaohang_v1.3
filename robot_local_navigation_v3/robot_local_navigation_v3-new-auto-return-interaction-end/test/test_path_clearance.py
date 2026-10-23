from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from robot_local_navigation_v3.models import PathSafetyConfig
from robot_local_navigation_v3.path_clearance import (
    GridMapInfo,
    PathClearanceError,
    decode_png_occupancy,
    inflate_obstacles,
    line_is_clear,
    plan_reference_line,
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
        self.height = 50
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
        start_x, start_y = self._pixel_world(6, 25)
        goal_x, goal_y = self._pixel_world(54, 25)
        return plan_reference_line(
            map_dir=self.map_dir,
            start_x=start_x,
            start_y=start_y,
            goal_x=goal_x,
            goal_y=goal_y,
            config=PathSafetyConfig(robot_radius_m=0.2, extra_clearance_m=0.1),
        )

    def test_reference_line_detours_around_inflated_static_obstacle(self) -> None:
        for v in range(13, 37):
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
        self.assertTrue(any(v != 25 for _, v in pixels))
        for start, end in zip(pixels, pixels[1:]):
            self.assertTrue(line_is_clear(start, end, blocked, width, height))

    def test_full_width_wall_rejects_route(self) -> None:
        for v in range(self.height):
            self.pixels[v * self.width + 30] = 0
        self._write_metadata()

        with self.assertRaisesRegex(PathClearanceError, "no route remains"):
            self._plan()

    def test_two_obstacles_keep_the_global_route_in_the_clear_middle_lane(self) -> None:
        for v in range(8, 16):
            for u in range(self.width):
                self.pixels[v * self.width + u] = 0
        for v in range(35, 43):
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
        self.assertEqual(pixels[0], (6, 25))
        self.assertEqual(pixels[-1], (54, 25))
        self.assertTrue(all(21 <= v <= 29 for _, v in pixels))
        for start, end in zip(pixels, pixels[1:]):
            self.assertTrue(line_is_clear(start, end, blocked, width, height))

    def test_narrow_middle_lane_is_rejected_after_footprint_inflation(self) -> None:
        for v in range(8, 21):
            for u in range(self.width):
                self.pixels[v * self.width + u] = 0
        for v in range(30, 43):
            for u in range(self.width):
                self.pixels[v * self.width + u] = 0
        self._write_metadata()

        with self.assertRaisesRegex(PathClearanceError, "inflated obstacle clearance"):
            self._plan()

    def test_occupied_start_is_rejected(self) -> None:
        self.pixels[25 * self.width + 6] = 0
        self._write_metadata()

        with self.assertRaisesRegex(PathClearanceError, "start pose"):
            self._plan()

    def test_unmeasured_robot_radius_is_rejected(self) -> None:
        with self.assertRaisesRegex(PathClearanceError, "not configured"):
            plan_reference_line(
                map_dir=self.map_dir,
                start_x=0.6,
                start_y=2.4,
                goal_x=5.4,
                goal_y=2.4,
                config=PathSafetyConfig(robot_radius_m=None),
            )

    def test_start_and_goal_in_same_grid_cell_keep_both_world_positions(self) -> None:
        route = plan_reference_line(
            map_dir=self.map_dir,
            start_x=2.51,
            start_y=2.45,
            goal_x=2.54,
            goal_y=2.46,
            config=PathSafetyConfig(robot_radius_m=0.2, extra_clearance_m=0.1),
        )

        self.assertEqual(route, [(2.51, 2.45), (2.54, 2.46)])
