"""Actual isolated ARM64 wheel/ROS compatibility with synthetic geometry, no ROS node."""
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import platform
import sys
import warnings

from arm64_python_runtime import PINNED, isolated_venv, load_lock, sha256, LOCK

ROOT = Path(__file__).resolve().parents[1]
ROS_PATHS = ("/opt/ros/humble/local/lib/python3.10/dist-packages",
             "/opt/ros/humble/lib/python3.10/site-packages")


def check():
    if (sys.platform != "linux" or platform.machine() != "aarch64" or sys.version_info[:2] != (3, 10)
            or os.geteuid() == 0 or sys.prefix == sys.base_prefix):
        raise ValueError("ordinary_arm64_isolated_python310_probe_required")
    load_lock()
    isolated_venv(Path(sys.prefix))
    modules, versions = {}, {}
    # Fail on numerical compatibility warnings rather than suppressing them.
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        for name, module in (("numpy", "numpy"), ("scipy", "scipy"), ("small-gicp", "small_gicp"),
                             ("PyYAML", "yaml"), ("json5", "json5")):
            loaded = import_module(module)
            if not Path(loaded.__file__).resolve().is_relative_to(Path(sys.prefix)):
                raise ValueError("numerical_modules_must_come_from_isolated_environment")
            modules[name], versions[name] = True, metadata.version(name)
        from scipy.spatial import cKDTree
        from scipy.spatial.transform import Rotation, Slerp
        if recorded:
            raise ValueError("warning_free_numerical_imports_required")
    if versions != PINNED:
        raise ValueError("same_locked_runtime_versions_required")
    for path in sys.path:
        if ("site-packages" in path or "dist-packages" in path) and not (
                Path(path).is_relative_to(Path(sys.prefix)) or path in ROS_PATHS):
            raise ValueError("only_venv_and_explicit_humble_packages_allowed")
    sys.path[:0] = [str(ROOT / "robot-slam")]
    import numpy as np
    from lingxi_slam.seeded_localization import SeededMapRegistration, sensor_scan_from_world

    rng = np.random.default_rng(48)
    surfaces = []
    for axis, offset in ((0, -4.), (0, 5.), (1, -3.), (1, 6.), (2, -2.), (2, 3.)):
        points = rng.uniform([-4, -3, -2], [5, 6, 3], (900, 3))
        points[:, axis] = offset
        surfaces.append(points)
    points, truth = np.concatenate(surfaces), np.eye(4)
    truth[:3, :3] = Rotation.from_euler("z", .24).as_matrix()
    truth[:3, 3] = [1.2, -.8, .3]
    seed = truth.copy()
    seed[:3, 3] += [.18, -.12, .08]
    registration = SeededMapRegistration(points)
    scan = sensor_scan_from_world(points, truth)
    result = registration.register(scan, seed)
    error = None if not result.accepted else float(np.max(np.abs(result.map_from_sensor - truth)))
    if error is None or error > .004:
        raise ValueError("synthetic_seeded_gicp_known_transform_required")
    wrong = truth.copy()
    wrong[:3, 3] += 100
    rejected = registration.register(scan, wrong)
    if rejected.accepted or rejected.map_from_sensor is not None:
        raise ValueError("wrong_seed_must_not_produce_pose")
    distances, _ = cKDTree(points).query(points, workers=1)
    if np.max(distances) != 0 or not np.isfinite(Slerp([0, 1], Rotation.from_euler("z", [0, .2]))(.5).as_matrix()).all():
        raise ValueError("synthetic_scipy_tree_and_rotation_required")
    import check_base_pose_tf2
    tf = check_base_pose_tf2.check()
    return {"schemaVersion": 1, "result": "isolated_arm64_numerical_ros_compatibility_passed",
            "machine": platform.machine(), "python": platform.python_version(),
            "lockSha256": sha256(LOCK), "versions": versions, "modulesIsolated": modules,
            "numericalImportWarnings": 0, "systemSitePackages": False,
            "syntheticGicpMaximumMatrixError": error,
            "syntheticTfMaximumMatrixError": tf["maximumMatrixElementError"],
            "checks": {"knownSeedGicp": True, "wrongSeedRejected": True,
                       "scipyTreeRotation": True, "exactTimeHumbleTf": True},
            "boundaries": dict.fromkeys(("sensorsSubscribed", "rosNodeCreated", "motionCommandsSent",
                "vendorPackagesChanged", "physicalCalibrationAccepted", "navigationReady", "productAccepted"), False)}


if __name__ == "__main__":
    print(json.dumps(check(), allow_nan=False))
