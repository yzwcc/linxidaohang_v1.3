"""Read-only comparison with the current X2 ROS graph, not factory acceptance."""

from pathlib import Path


LIDAR_FRAME = "lidar_chest_front"
IMU_FRAME = "lidar_imu_chest_front"
# Match the pinned saved-map export gate (imu_from_lidar @ lidar_from_imu).
MAX_RESIDUAL = 0.0001


def valid_rigid(matrix):
    import numpy as np

    return (matrix.shape == (4, 4) and np.isfinite(matrix).all()
            and np.max(np.abs(matrix[3] - (0, 0, 0, 1))) <= 1e-7
            and np.max(np.abs(matrix[:3, :3].T @ matrix[:3, :3] - np.eye(3))) <= MAX_RESIDUAL
            and abs(np.linalg.det(matrix[:3, :3]) - 1) <= MAX_RESIDUAL)


def check_target_mounting(profile, lookup):
    import numpy as np
    import yaml

    try:
        path = Path(profile)
        if not path.is_absolute():
            return "invalid_profile"
        with path.open("rb") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            return "invalid_profile"
        document = yaml.safe_load(raw)
        # Use the reference FAST-LIO wildcard stanza, with no per-node overrides.
        if not isinstance(document, dict) or set(document) != {"/**"}:
            return "invalid_profile"
        mapping = document["/**"]["ros__parameters"]["mapping"]
        if mapping["extrinsic_est_en"] is not False:
            return "invalid_profile"
        rotation, translation = mapping["extrinsic_R"], mapping["extrinsic_T"]
        if (not isinstance(rotation, list) or len(rotation) != 9 or not isinstance(translation, list)
                or len(translation) != 3 or any(type(value) not in (int, float) for value in rotation + translation)):
            return "invalid_profile"
        configured = np.eye(4)
        configured[:3, :3] = np.asarray(rotation, dtype=float).reshape(3, 3)
        configured[:3, 3] = translation
        if not valid_rigid(configured):
            return "invalid_profile"
    except (OSError, AttributeError, KeyError, TypeError, ValueError, OverflowError, yaml.YAMLError):
        return "invalid_profile"

    try:
        # Time zero requests the latest TF; this comparison uses only the fixed LiDAR–IMU edge.
        transform = lookup(LIDAR_FRAME, IMU_FRAME, 0)
        if transform is None:
            return "missing_live_tf"
        measured = np.asarray(transform, dtype=float)
        if not valid_rigid(measured):
            return "invalid_live_tf"
        residual = np.max(np.abs(configured @ measured - np.eye(4)))
        return "matched" if residual <= MAX_RESIDUAL else "mismatch"
    except (TypeError, ValueError, OverflowError, RuntimeError):
        return "invalid_live_tf"
