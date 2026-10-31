"""Pinned upstream global planning over explicit Nav2 uint8 costmap snapshots."""
import ctypes
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import threading


UPSTREAM_COMMIT = "3c3db59d6969d8ecee8e68468693d006397f4a0c"
MAXIMUM_CELLS = 1048576


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class CostmapSnapshot:
    map_id: str
    receipt_sha256: str
    revision: int
    width: int
    height: int
    resolution_metres: float
    origin_x_metres: float
    origin_y_metres: float
    costs: bytes
    frame: str = "lingxi_saved_map"
    encoding: str = "nav2_costmap_uint8"

    def __post_init__(self):
        if (type(self.map_id) is not str or re.fullmatch(r"slam-[0-9a-f]{32}", self.map_id) is None
                or type(self.receipt_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", self.receipt_sha256) is None
                or type(self.revision) is not int or not 0 <= self.revision < 2**53
                or any(type(value) is not int or not 8 <= value <= 2048 for value in (self.width, self.height))
                or self.width * self.height > MAXIMUM_CELLS or type(self.costs) is not bytes
                or len(self.costs) != self.width * self.height or self.frame != "lingxi_saved_map"
                or self.encoding != "nav2_costmap_uint8" or not finite(self.resolution_metres)
                or not .005 <= self.resolution_metres <= 1.
                or any(not finite(value) or abs(value) > 1e6 for value in (self.origin_x_metres, self.origin_y_metres))):
            raise ValueError("bounded_explicit_nav2_costmap_snapshot_required")

    @property
    def sha256(self):
        fields = {key: getattr(self, key) for key in self.__dataclass_fields__ if key != "costs"}
        metadata = json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(metadata + b"\n" + self.costs).hexdigest()

    def cell(self, position):
        if type(position) not in (tuple, list) or len(position) != 2 or not all(map(finite, position)):
            raise ValueError("finite_world_xy_required")
        x = math.floor((position[0] - self.origin_x_metres) / self.resolution_metres)
        y = math.floor((position[1] - self.origin_y_metres) / self.resolution_metres)
        if not 1 <= x < self.width - 1 or not 1 <= y < self.height - 1:
            raise ValueError("interior_costmap_cell_required")
        return x, y


@dataclass(frozen=True)
class GlobalPlan:
    status: str
    map_id: str
    receipt_sha256: str
    costmap_sha256: str
    revision: int
    points_metres: tuple
    frame: str = "lingxi_saved_map"
    navigation_ready: bool = False


class NavfnPlanner:
    """Native work must be supervised in a child before integration with live tasks.

    This core accepts an already constructed costmap, not an occupancy image or
    a point cloud. It does not validate physical scale, footprint inflation,
    current localization, traversable ground, or dynamic obstacle clearance.
    """

    def __init__(self, library, sha256):
        path = Path(library)
        if (not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file()
                or path.stat().st_size > 16 * 1024**2 or type(sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
                or hashlib.sha256(path.read_bytes()).hexdigest() != sha256):
            raise ValueError("direct_hash_verified_navfn_library_required")
        native = ctypes.CDLL(str(path))
        native.lingxi_navfn_abi_version.argtypes = []
        native.lingxi_navfn_abi_version.restype = ctypes.c_int
        native.lingxi_navfn_commit.argtypes = []
        native.lingxi_navfn_commit.restype = ctypes.c_char_p
        if native.lingxi_navfn_abi_version() != 1 or native.lingxi_navfn_commit() != UPSTREAM_COMMIT.encode():
            raise ValueError("pinned_navfn_engine_and_abi_required")
        native.lingxi_navfn_plan.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
            *([ctypes.c_int] * 6), ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        native.lingxi_navfn_plan.restype = ctypes.c_int
        self.native, self.owner = native, threading.get_ident()

    def plan(self, snapshot, start, goal, *, maximum_path_points=16384):
        if threading.get_ident() != self.owner:
            raise RuntimeError("single_navfn_planner_owner_required")
        if (not isinstance(snapshot, CostmapSnapshot) or type(maximum_path_points) is not int
                or not 2 <= maximum_path_points <= 65536):
            raise ValueError("costmap_and_bounded_path_budget_required")
        sx, sy = snapshot.cell(start)
        gx, gy = snapshot.cell(goal)
        costs = (ctypes.c_uint8 * len(snapshot.costs)).from_buffer_copy(snapshot.costs)
        output = (ctypes.c_float * (2 * maximum_path_points))()
        count = self.native.lingxi_navfn_plan(costs, len(costs), snapshot.width, snapshot.height,
                                            sx, sy, gx, gy, output, maximum_path_points)
        if count < 0 or count > maximum_path_points:
            raise RuntimeError("navfn_did_not_return_a_valid_complete_plan")
        points = tuple((snapshot.origin_x_metres + (float(output[i * 2]) + .5) * snapshot.resolution_metres,
                        snapshot.origin_y_metres + (float(output[i * 2 + 1]) + .5) * snapshot.resolution_metres)
                       for i in range(count))
        if any(not all(map(finite, point)) for point in points):
            raise RuntimeError("finite_complete_navfn_plan_required")
        return GlobalPlan("planned" if points else "no_path", snapshot.map_id, snapshot.receipt_sha256,
                          snapshot.sha256, snapshot.revision, points)
