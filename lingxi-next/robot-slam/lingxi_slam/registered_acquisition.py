"""Same-scan registered rays in native map units, not metric admission."""
from dataclasses import dataclass, field
import re

import numpy as np

from .acquisition_output import AcquisitionPacket
from .observation_archive import rigid
from .point_data import arrays


@dataclass(frozen=True)
class RegisteredAcquisition:
    binding: object
    generation: int
    native_frame_id: int
    algorithm_stamp: float
    received_at: float
    source_sha256: str
    origins: np.ndarray
    endpoints: np.ndarray
    map_lidar_pose: np.ndarray
    coordinate_frame: str = field(default="lingxi_saved_map", init=False)
    units: str = field(default="native-raw", init=False)
    navigation_ready: bool = field(default=False, init=False)


def register_acquisition(packet, points, *, binding, generation, received_at, map_from_lidar):
    """Caller must fence freshness and generation before and after this copy.

    Both measured ray ends use the SAME frame's map-from-odometry transform.
    No scan origin, timestamp, metric scale, ground alignment or free cell is
    invented here. Historical submaps are not valid inputs.
    """
    from .localization_session import LocalizationBinding
    _, point_pose = arrays(points)
    if (type(packet) is not AcquisitionPacket or type(binding) is not LocalizationBinding
            or type(generation) is not int or generation < 0
            or type(received_at) not in (int, float) or not np.isfinite(received_at) or received_at < 0
            or packet.native_frame_id != points.native_frame_id
            or packet.algorithm_stamp != points.algorithm_stamp
            or packet.coordinate_frame != "lingxi_glim_odom"
            or packet.kind != "native_per_return_original_lio_acquisition"
            or type(packet.source_sha256) is not str or re.fullmatch("[0-9a-f]{64}", packet.source_sha256) is None):
        raise ValueError("matching_current_acquisition_and_localization_required")
    origins, endpoints = packet.origins, packet.endpoints
    if (any(type(value) is not np.ndarray or value.dtype != np.float64 or value.ndim != 2
            or value.shape[1] != 3 or value.flags.writeable or not np.isfinite(value).all()
            for value in (origins, endpoints))
            or not 1 <= len(origins) <= 65536 or endpoints.shape != origins.shape):
        raise ValueError("owned_complete_per_return_acquisition_geometry_required")
    odom_from_lidar = rigid(np.asarray(packet.world_lidar_pose))
    if not np.allclose(odom_from_lidar, point_pose, rtol=0, atol=1e-8):
        raise ValueError("same_frame_corrected_lidar_pose_required")
    map_pose = rigid(np.asarray(map_from_lidar)).copy()
    map_from_odom = rigid(map_pose @ np.linalg.inv(odom_from_lidar))
    registered = [value @ map_from_odom[:3, :3].T + map_from_odom[:3, 3]
                  for value in (origins, endpoints)]
    if any(not np.isfinite(value).all() for value in registered):
        raise ValueError("finite_registered_acquisition_geometry_required")
    for value in (*registered, map_pose):
        value.flags.writeable = False
    return RegisteredAcquisition(binding, generation, packet.native_frame_id, packet.algorithm_stamp,
                                 received_at, packet.source_sha256, *registered, map_pose)
