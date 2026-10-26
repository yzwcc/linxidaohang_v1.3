from __future__ import annotations

import heapq
import math
import struct
import zlib
from array import array
from dataclasses import dataclass
from pathlib import Path

from robot_local_navigation_v3.models import PathSafetyConfig


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_MAP_PIXELS = 20_000_000
ROUTE_CLEARANCE_PREFERENCE_M = 0.35
ROUTE_CLEARANCE_COST_WEIGHT = 12.0
CHAMFER_DISTANCE_LOWER_BOUND_SCALE = 0.92
ROUTE_CELL_GEOMETRY_MARGIN = 2.0


class PathClearanceError(ValueError):
    """Raised when a globally planned path cannot be proven clear."""


@dataclass(frozen=True)
class GridMapInfo:
    resolution_pixels_per_meter: float
    origin_u: float
    origin_v: float
    width: int
    height: int

    @classmethod
    def load(cls, path: Path) -> "GridMapInfo":
        lines = [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) < 3 or any(len(line) < count for line, count in zip(lines[:3], (1, 2, 2))):
            raise PathClearanceError(f"invalid grid_map_info.txt: {path}")
        try:
            info = cls(
                resolution_pixels_per_meter=float(lines[0][0]),
                origin_u=float(lines[1][0]),
                origin_v=float(lines[1][1]),
                width=int(float(lines[2][0])),
                height=int(float(lines[2][1])),
            )
        except (ValueError, OverflowError) as exc:
            raise PathClearanceError(f"invalid grid map values: {path}") from exc
        if (
            not math.isfinite(info.resolution_pixels_per_meter)
            or info.resolution_pixels_per_meter <= 0
            or not math.isfinite(info.origin_u)
            or not math.isfinite(info.origin_v)
            or info.width <= 0
            or info.height <= 0
            or info.width * info.height > MAX_MAP_PIXELS
        ):
            raise PathClearanceError(f"unsafe grid map dimensions or resolution: {path}")
        return info

    def world_to_pixel(self, x: float, y: float) -> tuple[int, int]:
        u = (x * self.resolution_pixels_per_meter) + self.origin_u
        v = self.origin_v - (y * self.resolution_pixels_per_meter)
        return int(round(u)), int(round(v))

    def pixel_to_world(self, u: int, v: int) -> tuple[float, float]:
        return (
            (u - self.origin_u) / self.resolution_pixels_per_meter,
            -(v - self.origin_v) / self.resolution_pixels_per_meter,
        )


def decode_png_occupancy(path: Path, occupied_threshold: int = 205) -> tuple[int, int, list[bool]]:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise PathClearanceError(f"not a PNG occupancy map: {path}")
    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    palette: bytes | None = None
    transparency: bytes | None = None
    compressed = bytearray()
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        start = offset + 8
        end = start + length
        if end + 4 > len(data):
            raise PathClearanceError("truncated PNG chunk")
        chunk = data[start:end]
        if kind == b"IHDR":
            if length != 13:
                raise PathClearanceError("invalid PNG IHDR")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunk)
            if compression != 0 or filtering != 0 or interlace != 0:
                raise PathClearanceError("unsupported PNG compression, filter, or interlace mode")
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"tRNS":
            transparency = chunk
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
        offset = end + 4

    if width is None or height is None or bit_depth != 8:
        raise PathClearanceError("only 8-bit PNG occupancy maps are supported")
    if width <= 0 or height <= 0 or width * height > MAX_MAP_PIXELS:
        raise PathClearanceError("PNG map dimensions are invalid or exceed the safety limit")
    channels_by_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channels_by_type.get(color_type)
    if channels is None or (color_type == 3 and not palette):
        raise PathClearanceError(f"unsupported PNG color type: {color_type}")
    stride = width * channels
    try:
        raw = zlib.decompress(compressed)
    except zlib.error as exc:
        raise PathClearanceError("invalid PNG compressed image data") from exc
    if len(raw) != height * (stride + 1):
        raise PathClearanceError("PNG scanline data length does not match image dimensions")

    rows: list[bytearray] = []
    cursor = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scan = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        if filter_type > 4:
            raise PathClearanceError(f"unsupported PNG filter: {filter_type}")
        for index in range(stride):
            left = scan[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                scan[index] = (scan[index] + left) & 255
            elif filter_type == 2:
                scan[index] = (scan[index] + above) & 255
            elif filter_type == 3:
                scan[index] = (scan[index] + ((left + above) // 2)) & 255
            elif filter_type == 4:
                p = left + above - upper_left
                pa, pb, pc = abs(p - left), abs(p - above), abs(p - upper_left)
                predictor = left if pa <= pb and pa <= pc else above if pb <= pc else upper_left
                scan[index] = (scan[index] + predictor) & 255
        rows.append(scan)
        previous = scan

    occupancy: list[bool] = []
    for row in rows:
        for x in range(width):
            base = x * channels
            if color_type == 0:
                luminance, alpha = row[base], 255
            elif color_type == 2:
                luminance = (299 * row[base] + 587 * row[base + 1] + 114 * row[base + 2]) // 1000
                alpha = 255
            elif color_type == 3:
                palette_index = row[base]
                palette_offset = palette_index * 3
                if palette_offset + 2 >= len(palette or b""):
                    raise PathClearanceError("PNG palette index is out of range")
                colors = palette or b""
                luminance = (299 * colors[palette_offset] + 587 * colors[palette_offset + 1] + 114 * colors[palette_offset + 2]) // 1000
                alpha = transparency[palette_index] if transparency and palette_index < len(transparency) else 255
            elif color_type == 4:
                luminance, alpha = row[base], row[base + 1]
            else:
                luminance = (299 * row[base] + 587 * row[base + 1] + 114 * row[base + 2]) // 1000
                alpha = row[base + 3]
            occupancy.append(alpha == 0 or luminance <= occupied_threshold)
    return width, height, occupancy


def plan_reference_line(
    *,
    map_dir: Path,
    start_x: float,
    start_y: float,
    goal_x: float,
    goal_y: float,
    config: PathSafetyConfig,
) -> list[tuple[float, float]]:
    if config.robot_radius_m is None:
        raise PathClearanceError("robot footprint radius is not configured from a measurement")
    info = GridMapInfo.load(map_dir / "grid_map_info.txt")
    width, height, occupied = decode_png_occupancy(map_dir / "occupancy_map.png", config.occupied_threshold)
    if (width, height) != (info.width, info.height):
        raise PathClearanceError(
            f"map metadata/image size mismatch: metadata={info.width}x{info.height}, image={width}x{height}"
        )

    radius_cells = (config.robot_radius_m + config.extra_clearance_m) * info.resolution_pixels_per_meter
    blocked = inflate_obstacles(occupied, width, height, radius_cells)
    start = info.world_to_pixel(start_x, start_y)
    goal = info.world_to_pixel(goal_x, goal_y)
    for label, (x, y) in (("start", start), ("goal", goal)):
        if not (0 <= x < width and 0 <= y < height):
            raise PathClearanceError(f"{label} pose is outside the occupancy map")
        if blocked[y * width + x]:
            raise PathClearanceError(f"{label} pose is inside the inflated obstacle clearance")

    clearance = distance_to_blocked(blocked, width, height)
    for index, distance in enumerate(clearance):
        clearance[index] = max(
            0.0,
            distance * CHAMFER_DISTANCE_LOWER_BOUND_SCALE
            - ROUTE_CELL_GEOMETRY_MARGIN,
        )
    preferred_clearance_cells = ROUTE_CLEARANCE_PREFERENCE_M * info.resolution_pixels_per_meter
    for label, (x, y) in (("start", start), ("goal", goal)):
        if clearance[y * width + x] < preferred_clearance_cells:
            raise PathClearanceError(
                f"{label} pose cannot maintain the minimum global path clearance"
            )

    route_blocked = bytearray(
        1 if is_blocked or clearance[index] < preferred_clearance_cells else 0
        for index, is_blocked in enumerate(blocked)
    )
    pixels = astar(
        route_blocked,
        width,
        height,
        start,
        goal,
        clearance_cells=clearance,
        preferred_clearance_cells=preferred_clearance_cells,
    )
    pixels = compress_path(
        pixels,
        route_blocked,
        width,
        height,
        clearance_cells=clearance,
        preferred_clearance_cells=preferred_clearance_cells,
    )
    if len(pixels) > config.max_reference_points:
        raise PathClearanceError(
            f"safe reference line requires {len(pixels)} points; configured limit is {config.max_reference_points}"
        )
    world = [info.pixel_to_world(x, y) for x, y in pixels]
    if len(world) == 1:
        world = [(start_x, start_y), (goal_x, goal_y)]
    else:
        world[0] = (start_x, start_y)
        world[-1] = (goal_x, goal_y)
    for start_world, goal_world in zip(world, world[1:]):
        if not world_line_is_clear(
            start_world,
            goal_world,
            info,
            route_blocked,
            width,
            height,
            clearance,
            preferred_clearance_cells,
        ):
            raise PathClearanceError(
                "exact reference line cannot maintain the minimum global path clearance"
            )
    return world


def inflate_obstacles(occupied: list[bool], width: int, height: int, radius_cells: float) -> list[bool]:
    if len(occupied) != width * height or radius_cells < 0 or not math.isfinite(radius_cells):
        raise PathClearanceError("invalid occupancy grid or inflation radius")
    # An 8-connected chamfer distance can overestimate Euclidean distance by
    # about 8%; inflate by 10% plus one pixel to remain conservative.
    limit = int(math.ceil(radius_cells * 1.10 + 1.0))
    inf = limit + max(width, height) + 1
    distance = [0 if cell else inf for cell in occupied]
    diagonal = 1.4142135623730951
    for y in range(height):
        row = y * width
        for x in range(width):
            i = row + x
            value = distance[i]
            if x:
                value = min(value, distance[i - 1] + 1.0)
            if y:
                value = min(value, distance[i - width] + 1.0)
                if x:
                    value = min(value, distance[i - width - 1] + diagonal)
                if x + 1 < width:
                    value = min(value, distance[i - width + 1] + diagonal)
            distance[i] = value
    for y in range(height - 1, -1, -1):
        row = y * width
        for x in range(width - 1, -1, -1):
            i = row + x
            value = distance[i]
            if x + 1 < width:
                value = min(value, distance[i + 1] + 1.0)
            if y + 1 < height:
                value = min(value, distance[i + width] + 1.0)
                if x:
                    value = min(value, distance[i + width - 1] + diagonal)
                if x + 1 < width:
                    value = min(value, distance[i + width + 1] + diagonal)
            distance[i] = value

    inflated = [value <= limit for value in distance]
    boundary_cells = int(math.ceil(radius_cells + 1.0))
    if boundary_cells:
        for y in range(height):
            if y < boundary_cells or y >= height - boundary_cells:
                start = y * width
                inflated[start:start + width] = [True] * width
            else:
                start = y * width
                inflated[start:start + boundary_cells] = [True] * boundary_cells
                inflated[start + width - boundary_cells:start + width] = [True] * boundary_cells
    return inflated


def astar(
    blocked: list[bool],
    width: int,
    height: int,
    start: tuple[int, int],
    goal: tuple[int, int],
    *,
    clearance_cells: array | None = None,
    preferred_clearance_cells: float = 0.0,
) -> list[tuple[int, int]]:
    start_index = start[1] * width + start[0]
    goal_index = goal[1] * width + goal[0]
    costs = {start_index: 0.0}
    parents: dict[int, int] = {}
    queue: list[tuple[float, float, int]] = [(0.0, 0.0, start_index)]
    directions = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                  (1, 1, 1.4142135623730951), (1, -1, 1.4142135623730951),
                  (-1, 1, 1.4142135623730951), (-1, -1, 1.4142135623730951))
    while queue:
        _, cost, index = heapq.heappop(queue)
        if cost != costs.get(index):
            continue
        if index == goal_index:
            path = [goal]
            while index != start_index:
                index = parents[index]
                path.append((index % width, index // width))
            path.reverse()
            return path
        x, y = index % width, index // width
        for dx, dy, step_cost in directions:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            next_index = ny * width + nx
            if blocked[next_index]:
                continue
            if dx and dy and (blocked[y * width + nx] or blocked[ny * width + x]):
                continue
            clearance_penalty = 0.0
            if clearance_cells is not None and preferred_clearance_cells > 0:
                ratio = preferred_clearance_cells / max(
                    preferred_clearance_cells,
                    clearance_cells[next_index],
                )
                clearance_penalty = ROUTE_CLEARANCE_COST_WEIGHT * ratio * ratio
            next_cost = cost + step_cost * (1.0 + clearance_penalty)
            if next_cost >= costs.get(next_index, math.inf):
                continue
            costs[next_index] = next_cost
            parents[next_index] = index
            heuristic = math.hypot(goal[0] - nx, goal[1] - ny)
            heapq.heappush(queue, (next_cost + heuristic, next_cost, next_index))
    raise PathClearanceError("no route remains after robot-footprint obstacle inflation")


def compress_path(
    path: list[tuple[int, int]],
    blocked: list[bool],
    width: int,
    height: int,
    *,
    clearance_cells: array | None = None,
    preferred_clearance_cells: float = 0.0,
) -> list[tuple[int, int]]:
    if len(path) <= 2:
        return path
    result = [path[0]]
    anchor = 0
    while anchor < len(path) - 1:
        candidate = len(path) - 1
        preferred_candidate = None
        while candidate > anchor:
            segment_clearance = line_clearance(
                path[anchor], path[candidate], blocked, width, height, clearance_cells
            )
            if segment_clearance is None:
                candidate -= 1
                continue
            if segment_clearance >= preferred_clearance_cells:
                preferred_candidate = candidate
                break
            candidate -= 1
        if preferred_candidate is None:
            raise PathClearanceError("safe route compression could not preserve a clear segment")
        result.append(path[preferred_candidate])
        anchor = preferred_candidate
    return result


def line_is_clear(
    start: tuple[int, int],
    end: tuple[int, int],
    blocked: list[bool],
    width: int,
    height: int,
    *,
    clearance_cells: array | None = None,
    preferred_clearance_cells: float = 0.0,
) -> bool:
    minimum_clearance = line_clearance(
        start, end, blocked, width, height, clearance_cells
    )
    if minimum_clearance is None:
        return False
    return preferred_clearance_cells <= 0 or minimum_clearance >= preferred_clearance_cells


def line_clearance(
    start: tuple[int, int],
    end: tuple[int, int],
    blocked: list[bool],
    width: int,
    height: int,
    clearance_cells: array | None = None,
) -> float | None:
    x0, y0 = start
    x1, y1 = end
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx - dy
    minimum_clearance = math.inf
    while True:
        if not (0 <= x0 < width and 0 <= y0 < height):
            return None
        index = y0 * width + x0
        if blocked[index]:
            return None
        if clearance_cells is not None:
            minimum_clearance = min(minimum_clearance, clearance_cells[index])
        if x0 == x1 and y0 == y1:
            return minimum_clearance
        doubled = error * 2
        old_x, old_y = x0, y0
        if doubled > -dy:
            error -= dy
            x0 += sx
        if doubled < dx:
            error += dx
            y0 += sy
        if x0 != old_x and y0 != old_y:
            if blocked[old_y * width + x0] or blocked[y0 * width + old_x]:
                return None
            if clearance_cells is not None:
                minimum_clearance = min(
                    minimum_clearance,
                    clearance_cells[old_y * width + x0],
                    clearance_cells[y0 * width + old_x],
                )


def world_line_is_clear(
    start: tuple[float, float],
    end: tuple[float, float],
    info: GridMapInfo,
    blocked: list[bool],
    width: int,
    height: int,
    clearance_cells: array,
    preferred_clearance_cells: float,
) -> bool:
    scale = info.resolution_pixels_per_meter
    start_u = start[0] * scale + info.origin_u
    start_v = info.origin_v - start[1] * scale
    end_u = end[0] * scale + info.origin_u
    end_v = info.origin_v - end[1] * scale
    pixel_length = math.hypot(end_u - start_u, end_v - start_v)
    sample_count = max(1, math.ceil(pixel_length * 2.0))
    for sample in range(sample_count + 1):
        ratio = sample / sample_count
        u = start_u + (end_u - start_u) * ratio
        v = start_v + (end_v - start_v) * ratio
        if not (0.0 <= u <= width - 1 and 0.0 <= v <= height - 1):
            return False
        x, y = int(round(u)), int(round(v))
        index = y * width + x
        if blocked[index] or clearance_cells[index] < preferred_clearance_cells:
            return False
    return True


def distance_to_blocked(blocked: list[bool], width: int, height: int) -> array:
    if len(blocked) != width * height or width <= 0 or height <= 0:
        raise PathClearanceError("invalid blocked grid for clearance calculation")
    inf = float(max(width, height) + 1)
    distance = array("f", [inf]) * (width * height)
    for index, is_blocked in enumerate(blocked):
        if is_blocked:
            distance[index] = 0.0

    diagonal = 1.4142135623730951
    for y in range(height):
        row = y * width
        for x in range(width):
            index = row + x
            value = distance[index]
            if x:
                value = min(value, distance[index - 1] + 1.0)
            if y:
                value = min(value, distance[index - width] + 1.0)
                if x:
                    value = min(value, distance[index - width - 1] + diagonal)
                if x + 1 < width:
                    value = min(value, distance[index - width + 1] + diagonal)
            distance[index] = value

    for y in range(height - 1, -1, -1):
        row = y * width
        for x in range(width - 1, -1, -1):
            index = row + x
            value = distance[index]
            if x + 1 < width:
                value = min(value, distance[index + 1] + 1.0)
            if y + 1 < height:
                value = min(value, distance[index + width] + 1.0)
                if x:
                    value = min(value, distance[index + width - 1] + diagonal)
                if x + 1 < width:
                    value = min(value, distance[index + width + 1] + diagonal)
            distance[index] = value
    return distance
