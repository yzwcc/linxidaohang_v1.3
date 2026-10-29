"""Explicit bounded live point runtime; no installer or robot movement commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import sys
import threading

from point_owner import PointRuntimeOwner


ROOT = Path(__file__).resolve().parents[2]


def digest_file(path, expected):
    import re
    if not re.fullmatch("[a-f0-9]{64}", expected) or path.is_symlink() or not path.is_file():
        raise ValueError("explicit_matching_runtime_artifact_required")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError("explicit_matching_runtime_artifact_required")


def environment(root, core):
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1",
        "ROS_DISTRO": "humble", "ROS_DOMAIN_ID": "0", "RMW_IMPLEMENTATION": "rmw_fastrtps_cpp",
        "FASTRTPS_DEFAULT_PROFILES_FILE": "/agibot/software/entry/cfg/ros_dds_configuration.xml",
        "AMENT_PREFIX_PATH": "/opt/ros/humble", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2",
        "PYTHONPATH": ("/opt/ros/humble/local/lib/python3.10/dist-packages:"
                       "/opt/ros/humble/lib/python3.10/site-packages"),
        "LD_LIBRARY_PATH": ":".join(map(str, (
            root / "lib", core / "lib", Path("/opt/ros/humble/lib/aarch64-linux-gnu"),
            Path("/opt/ros/humble/lib")))),
    }


def run(args):
    if args.operator_confirmed is not True:
        raise ValueError("fresh_stationary_supervised_estop_confirmation_required")
    if (sys.platform != "linux" or platform.machine() != "aarch64"
            or os.geteuid() == 0 or sys.version_info[:2] != (3, 10)):
        raise ValueError("ordinary_arm64_humble_python310_required")
    for name in ("root", "core_root", "point_bundle", "node", "api_bundle"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.output = args.output.absolute()
    if (any(args.output == path or path in args.output.parents or args.output in path.parents
            for path in (args.root, args.core_root, args.point_bundle, args.node.parent, args.api_bundle.parent))
            or not 5 <= args.seconds <= 240):
        raise ValueError("bounded_live_session_outside_frozen_inputs_required")
    digest_file(args.node, args.node_sha256)
    digest_file(args.api_bundle, args.api_sha256)
    api_environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    for name in (
        "LINGXI_TLS_KEY", "LINGXI_TLS_CERT", "LINGXI_PAIRING_CODE_SHA256", "LINGXI_HOST", "LINGXI_PORT",
        "LINGXI_DATA_DIR", "LINGXI_WEB_ROOT", "AIMDK_BRIDGE_URL", "LINGXI_AGENT_URL", "LINGXI_AGENT_INTERNAL_TOKEN",
    ):
        if name in os.environ:
            api_environment[name] = os.environ[name]
    for name in ("LINGXI_TLS_KEY", "LINGXI_TLS_CERT", "LINGXI_PAIRING_CODE_SHA256"):
        if not api_environment.get(name):
            raise ValueError("explicit_production_tls_and_pairing_required")
    child = [sys.executable, "-B", str(Path(__file__).resolve()), "child",
             "--root", str(args.root), "--core-root", str(args.core_root),
             "--point-bundle", str(args.point_bundle), "--point-manifest-sha256", args.point_manifest_sha256,
             "--hypothesis", args.hypothesis]
    owner = PointRuntimeOwner(
        child, [str(args.node), str(args.api_bundle)], native_environment=environment(args.root, args.core_root),
        api_environment=api_environment, output=args.output)
    stop = threading.Event()
    old = {signum: signal.signal(signum, lambda *_: stop.set()) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        return owner.run(stop=stop, seconds=args.seconds,
                         on_listening=lambda status: print(json.dumps(status), flush=True))
    finally:
        for signum, handler in old.items():
            signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "child"))
    for name in ("root", "core-root", "point-bundle"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--point-manifest-sha256", required=True)
    parser.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
    parser.add_argument("--point-socket", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--api-bundle", type=Path)
    parser.add_argument("--node-sha256")
    parser.add_argument("--api-sha256")
    parser.add_argument("--seconds", type=float, default=60.)
    parser.add_argument("--operator-confirmed", action="store_true")
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        if args.command == "child":
            if args.point_socket is None:
                raise ValueError("explicit_parent_point_socket_required")
            from live_child import run as child
            with os.fdopen(os.dup(1), "wb", buffering=0) as sink:
                os.dup2(2, 1)
                return child(args, sink)
        if any(getattr(args, name) is None for name in (
                "output", "node", "api_bundle", "node_sha256", "api_sha256")):
            raise ValueError("explicit_live_runtime_artifacts_required")
        return 0 if run(args)["result"] == "stopped" else 1
    finally:
        os.umask(previous)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Lingxi Next live-point runtime failed; inspect private reports.", file=sys.stderr)
        raise SystemExit(1)
