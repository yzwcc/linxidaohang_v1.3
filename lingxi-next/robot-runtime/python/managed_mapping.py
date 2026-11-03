"""Explicit commissioning launcher; no installation, motion or target discovery."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

from live_points import digest_file
from mapping_owner import ManagedMappingOwner
from mapping_platform import configuration


ROOT = Path(__file__).resolve().parents[2]


def validate_output(output, config, immutable_files, *, existing=False):
    output = Path(output)
    if (not output.is_absolute() or (not output.is_dir() if existing else output.exists()) or output.is_symlink()
            or output.resolve() != output):
        raise ValueError("new_direct_mapping_output_required")
    frozen = [Path(config[name]) for name in (
        "root", "coreRoot", "mappingRoot", "pointAdapter", "reference",
        "work", "adapter", "mappingAdapter", "bundle") if name in config]
    if (any(output == entry or output.is_relative_to(entry) or entry.is_relative_to(output)
            for entry in frozen)
            or any(Path(file).resolve().is_relative_to(output) for file in immutable_files)):
        raise ValueError("mapping_output_must_not_overlap_frozen_inputs")


def production_api_environment():
    api = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    for name in ("LINGXI_TLS_KEY", "LINGXI_TLS_CERT", "LINGXI_PAIRING_CODE_SHA256",
                 "LINGXI_AIMDK_BRIDGE_TOKEN",
                 "LINGXI_HOST", "LINGXI_PORT", "LINGXI_WEB_ROOT"):
        if name in os.environ:
            api[name] = os.environ[name]
    if any(not api.get(name) for name in ("LINGXI_TLS_KEY", "LINGXI_TLS_CERT", "LINGXI_PAIRING_CODE_SHA256")):
        raise ValueError("explicit_production_tls_and_pairing_required")
    return api


def run(args):
    config, _ = configuration(args.config)
    if "pointAdapter" not in config:
        raise ValueError("same_engine_mapping_point_output_required")
    for path, digest in ((args.node, args.node_sha256), (args.api_bundle, args.api_sha256)):
        if not path.is_absolute():
            raise ValueError("absolute_verified_mapping_runtime_required")
        digest_file(path, digest)
    validate_output(args.output, config, (args.config, args.node, args.api_bundle), existing=False)
    maps = args.maps
    if maps is not None:
        validate_output(maps, config, (args.config, args.node, args.api_bundle), existing=True)
    if args.persistent:
        if args.seconds is not None:
            raise ValueError("persistent_mapping_must_not_have_duration")
    elif args.seconds is not None and not 5 <= args.seconds <= 3600:
        raise ValueError("bounded_mapping_commissioning_duration_required")
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    api = production_api_environment()
    stop = threading.Event()
    for value in (signal.SIGINT, signal.SIGTERM):
        signal.signal(value, lambda *_: stop.set())
    owner = ManagedMappingOwner(
        [sys.executable, "-B", str(ROOT / "robot-runtime/python/mapping_service.py"),
         "--config", str(args.config)],
        [str(args.node), str(args.api_bundle)],
        service_environment=environment, api_environment=api, output=args.output, maps=maps)
    owner.continuous = args.persistent
    result = owner.run(stop=stop, seconds=None if args.persistent else args.seconds if args.seconds is not None else 300,
                       on_listening=lambda status: print(json.dumps(status), flush=True))
    return 0 if result["result"] == "stopped" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "output", "node", "api-bundle"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("node-sha256", "api-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--seconds", type=int)
    parser.add_argument("--maps", type=Path,
                        help="existing independent map catalog; kept outside the owner runtime")
    parser.add_argument("--persistent", action="store_true",
                        help="run until SIGTERM; only for the installed independent owner")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
