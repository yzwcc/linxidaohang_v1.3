"""Run the shared SLAM workspace against an explicit independent saved-map catalog."""
import argparse
import json
import os
import signal
import socket
import sys
import threading
from pathlib import Path

from controlled_localization import verified_configuration
from live_points import digest_file
from managed_mapping import production_api_environment, validate_output
from slam_owner import ManagedSlamOwner

ROOT = Path(__file__).resolve().parents[2]


def validate(args):
    config, _ = verified_configuration(args.config, allow_live=True)
    immutable = (args.config, args.node, args.api_bundle)
    validate_output(args.output, config, immutable)
    # Apply the same frozen-input separation rules to the existing catalog.
    validate_output(args.maps, config, immutable, existing=True)
    if args.seconds is not None and not 5 <= args.seconds <= 3600:
        raise ValueError("explicit_bounded_or_continuous_slam_runtime_required")
    for path, expected in ((args.node, args.node_sha256), (args.api_bundle, args.api_sha256)):
        if not path.is_absolute():
            raise ValueError("absolute_verified_slam_runtime_required")
        digest_file(path, expected)
    return production_api_environment()


def notify_ready(value):
    endpoint = os.environ.get("NOTIFY_SOCKET")
    if endpoint:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
            connection.sendto(b"READY=1", "\0" + endpoint[1:] if endpoint.startswith("@") else endpoint)
    print(json.dumps(value), flush=True)


def run(args):
    api = validate(args)
    _, native_environment = verified_configuration(args.config, allow_live=True)
    stop = threading.Event()
    previous = {value: signal.signal(value, lambda *_: stop.set()) for value in (signal.SIGINT, signal.SIGTERM)}
    try:
        owner = ManagedSlamOwner(
            [sys.executable, "-B", str(ROOT / "robot-runtime/python/slam_service.py"), "--config", str(args.config)],
            [str(args.node), str(args.api_bundle)], maps=args.maps, output=args.output,
            service_environment={**native_environment, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                 "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}, api_environment=api)
        result = owner.run(stop=stop, seconds=args.seconds, on_listening=notify_ready)
        return 0 if result["result"] == "stopped" else 1
    finally:
        for value, handler in previous.items():
            signal.signal(value, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "maps", "output", "node", "api-bundle"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("node-sha256", "api-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--seconds", type=int, help="Omit for a persistent service; native sessions remain bounded.")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
