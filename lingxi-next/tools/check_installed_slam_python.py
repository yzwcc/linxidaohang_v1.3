"""Preflight the installed service interpreter without nodes, sensors or motion."""
import argparse
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import platform
import site
import sys
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arm64_python_runtime import PINNED, LOCK, isolated_venv, load_lock, sha256

MODULES = {"numpy": "numpy", "scipy": "scipy", "small-gicp": "small_gicp",
           "PyYAML": "yaml", "json5": "json5"}
ROS_ROOT = Path("/opt/ros/humble")


def selection(executable):
    selected = Path(executable)
    prefix = Path(sys.prefix)
    if (not selected.is_absolute() or str(selected) != executable
            or selected.parent != prefix / "bin" or selected.parent.resolve(strict=True) != selected.parent
            or not selected.is_file() or not os.access(selected, os.X_OK)
            or Path(sys.executable) != selected or sys.prefix == sys.base_prefix
            or prefix.resolve(strict=True) != prefix
            or any(prefix.is_relative_to(Path(path)) for path in ("/home", "/root"))):
        raise ValueError("explicit_direct_service_accessible_venv_interpreter_required")
    isolated_venv(prefix)
    if not sys.flags.no_user_site or site.ENABLE_USER_SITE is not False:
        raise ValueError("disabled_python_user_site_required")
    return prefix


def check(executable):
    if (sys.platform != "linux" or platform.machine() != "aarch64"
            or sys.version_info[:2] != (3, 10) or os.geteuid() == 0):
        raise ValueError("nonroot_arm64_python310_service_required")
    prefix = selection(executable)
    load_lock()
    versions = {}
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        for name, module in MODULES.items():
            loaded = import_module(module)
            distribution = metadata.distribution(name)
            if (not Path(loaded.__file__).resolve(strict=True).is_relative_to(prefix)
                    or not Path(distribution.locate_file("")).resolve(strict=True).is_relative_to(prefix)):
                raise ValueError("locked_modules_and_metadata_must_come_from_selected_venv")
            versions[name] = distribution.version
        if versions != PINNED:
            raise ValueError("same_locked_runtime_versions_required")
        # Exercise compiled SciPy imports; import ROS only, never create a node.
        import_module("scipy.spatial")
        import_module("scipy.spatial.transform")
        for module in ("rclpy", "tf2_ros"):
            loaded = import_module(module)
            if not Path(loaded.__file__).resolve(strict=True).is_relative_to(ROS_ROOT):
                raise ValueError("existing_humble_ros_modules_required")
        if recorded:
            raise ValueError("warning_free_runtime_imports_required")
    return {"schemaVersion": 1, "result": "installed_slam_python_preflight_passed",
            "executable": executable, "lockSha256": sha256(LOCK), "versions": versions,
            "systemSitePackages": False, "userSiteEnabled": False,
            "rosNodesCreated": False, "sensorsSubscribed": False, "motionCommandsSent": False,
            "runtimeCompatibilityAccepted": False, "navigationReady": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.python), allow_nan=False))
        return 0
    except Exception:
        print("Selected Python must be an isolated ARM64 3.10 venv with locked numerical packages "
              "and existing Humble imports; see docs/INSTALLED_SLAM_PYTHON.md.", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
