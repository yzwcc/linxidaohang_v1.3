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
from product_configuration import selection as product_selection

ROOT = Path(__file__).resolve().parents[2]


def acquisition_selection(args, config):
    path = getattr(args, 'acquisition_settings', None)
    expected = getattr(args, 'acquisition_settings_sha256', None)
    if path is None:
        if expected is not None:
            raise ValueError('paired_shared_acquisition_settings_required')
        return None
    from live_acquisition import AcquisitionSettings
    from mapping_platform import MappingPlatform
    selected = AcquisitionSettings(path, MappingPlatform(config))
    if expected is not None and selected.frozen['settingsSha256'] != expected:
        raise ValueError('unchanged_deployed_acquisition_settings_required')
    directories = [selected.value['acquisition']['root']]
    if selected.terrain is not None:
        directories.extend(str(Path(selected.value['terrain'][name]['library']).parent) for name in ('ground', 'octomap'))
    for directory in directories:
        for output, existing in ((args.output, False), (args.maps, True)):
            validate_output(output, {'bundle': directory}, (path,), existing=existing)
    return selected


def validate(args):
    config, _ = verified_configuration(args.config, allow_live=True)
    acquisition_selection(args, config)
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
    return {**production_api_environment(), **product_selection(args, config)}


def notify_ready(value):
    endpoint = os.environ.get("NOTIFY_SOCKET")
    if endpoint:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
            connection.sendto(b"READY=1", "\0" + endpoint[1:] if endpoint.startswith("@") else endpoint)
    print(json.dumps(value), flush=True)


def run(args):
    api = validate(args)
    config, native_environment = verified_configuration(args.config, allow_live=True)
    acquisition = acquisition_selection(args, config)
    service = [sys.executable, "-B", str(ROOT / "robot-runtime/python/slam_service.py"), "--config", str(args.config)]
    if acquisition is not None:
        service.extend(acquisition.child_arguments())
    stop = threading.Event()
    previous = {value: signal.signal(value, lambda *_: stop.set()) for value in (signal.SIGINT, signal.SIGTERM)}
    try:
        owner = ManagedSlamOwner(
            service,
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
    parser.add_argument('--acquisition-settings', type=Path)
    parser.add_argument('--acquisition-settings-sha256')
    parser.add_argument('--product-config', type=Path)
    parser.add_argument('--product-config-sha256')
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
