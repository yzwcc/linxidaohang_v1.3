"""Bounded recorded or live localization with the paired HTTPS console."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import threading

from controlled_localization import (
    FIELDS, ROOT, MappingPlatform, digest, direct, load_map, source_hashes,
    strict_json, verified_configuration, verify_completion)
from live_points import digest_file
from localization_owner import ManagedLocalizationOwner
from managed_mapping import validate_output


def api_configuration():
    api = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    for name in ("LINGXI_TLS_KEY", "LINGXI_TLS_CERT", "LINGXI_PAIRING_CODE_SHA256",
                 "LINGXI_HOST", "LINGXI_PORT", "LINGXI_WEB_ROOT"):
        if name in os.environ:
            api[name] = os.environ[name]
    if (any(not api.get(name) for name in ("LINGXI_TLS_KEY", "LINGXI_TLS_CERT"))
            or re.fullmatch(r"[0-9a-f]{64}", api.get("LINGXI_PAIRING_CODE_SHA256", "")) is None):
        raise ValueError("explicit_production_tls_and_pairing_required")
    files = {}
    for name in ("LINGXI_TLS_KEY", "LINGXI_TLS_CERT"):
        file = Path(api[name])
        info = direct(file)
        if not 1 <= info.st_size <= 1024 * 1024:
            raise ValueError("bounded_localization_tls_file_required")
        if name == "LINGXI_TLS_KEY" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError("private_owned_localization_tls_key_required")
        files[file] = digest(file.read_bytes())
    return api, files


def run(args):
    if (type(args.seconds) not in (int, float) or not math.isfinite(args.seconds)
            or not 5 <= args.seconds <= 120):
        raise ValueError("bounded_offline_localization_duration_required")
    config, environment = verified_configuration(args.config, allow_live=True)
    mode = config.get("inputMode", "recorded")
    # Native/ROS startup precedes API admission; the child must outlive the parent interval.
    if mode == "live" and config["maxSeconds"] < args.seconds + 45.:
        raise ValueError("live_child_duration_must_cover_startup_and_parent_interval")
    platform = MappingPlatform(config)
    for path, expected in ((args.node, args.node_sha256), (args.api_bundle, args.api_sha256)):
        direct(path)
        digest_file(path, expected)
    direct(args.saved_map, directory=True)
    saved_maps = args.saved_map.parent
    api, tls_files = api_configuration()
    validate_output(args.output, config, (
        args.config, args.node, args.api_bundle, args.saved_map / "map-ready.json",
        api["LINGXI_TLS_KEY"], api["LINGXI_TLS_CERT"]))
    if args.output.is_relative_to(saved_maps) or saved_maps.is_relative_to(args.output):
        raise ValueError("separate_saved_map_catalog_and_localization_output_required")
    # Check immutable inputs before any child or listener is admitted.
    config_hash, sources = digest(args.config.read_bytes()), source_hashes()
    composition = {
        file: digest((ROOT / "robot-runtime/python" / file).read_bytes())
        for file in ("managed_localization.py", "localization_owner.py")
    }
    profile = platform.profile
    load_map(args.saved_map, args.map_id, args.receipt_sha256, profile, args.profile_sha256,
             session="0" * 32, input_mode=mode, sensor_provenance=platform.sensor_provenance())

    def native_command(output):
        command = [sys.executable, "-B", str(ROOT / "robot-runtime/python/controlled_localization.py"), "child"]
        for field in FIELDS:
            value = output if field == "output" else getattr(args, field.replace("-", "_"))
            command.extend(("--" + field, str(value)))
        return command

    owner = ManagedLocalizationOwner(
        native_command, [str(args.node), str(args.api_bundle)],
        native_environment=environment, api_environment=api, output=args.output, saved_maps=saved_maps)
    binding, _ = load_map(
        args.saved_map, args.map_id, args.receipt_sha256, profile, args.profile_sha256,
        session=owner.native.session, input_mode=mode, sensor_provenance=platform.sensor_provenance())

    def verify():
        if mode == "live":
            from live_localization import verify_completion as verify_live
            return verify_live(
                strict_json((owner.native_output / "child-report.json").read_bytes()),
                strict_json((owner.native_output / "localization-final.json").read_bytes()),
                frozen=platform.frozen, binding=binding, sources=sources, config_hash=config_hash,
                base_scale=config.get("basePose", {}).get("metresPerMapUnit"))
        verify_completion(
            strict_json((owner.native_output / "child-report.json").read_bytes()),
            strict_json((owner.native_output / "localization-final.json").read_bytes()),
            frozen=platform.frozen, binding=binding, sources=sources,
            config_hash=config_hash, rate=config["rate"])

    def recheck():
        if (platform.verify() != platform.frozen or source_hashes() != sources
                or digest(args.config.read_bytes()) != config_hash
                or any(digest((ROOT / "robot-runtime/python" / file).read_bytes()) != expected
                       for file, expected in composition.items())):
            raise ValueError("unchanged_managed_localization_inputs_required")
        reloaded, _ = load_map(
            args.saved_map, args.map_id, args.receipt_sha256, profile, args.profile_sha256,
            session=binding.session, input_mode=mode, sensor_provenance=platform.sensor_provenance())
        if reloaded != binding:
            raise ValueError("unchanged_managed_localization_binding_required")
        for path, expected in ((args.node, args.node_sha256), (args.api_bundle, args.api_sha256)):
            direct(path)
            digest_file(path, expected)
        for path, expected in tls_files.items():
            direct(path)
            if digest(path.read_bytes()) != expected:
                raise ValueError("unchanged_localization_tls_files_required")

    stop = threading.Event()
    previous = {}
    try:
        for value in (signal.SIGINT, signal.SIGTERM):
            previous[value] = signal.signal(value, lambda *_: stop.set())
        report = owner.run(
            binding, None if mode == "live" else platform.frozen["journal"]["records"],
            seconds=args.seconds, stop=stop,
            on_listening=lambda status: print(json.dumps(status), flush=True),
            verify_complete=verify, verify_inputs=recheck)
    finally:
        for value, handler in previous.items():
            signal.signal(value, handler)
    return {"completed": 0, "interrupted": 2}.get(report["result"], 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in (*FIELDS, "node", "api-bundle", "node-sha256", "api-sha256"):
        parser.add_argument("--" + field, required=True,
            type=Path if field in ("config", "saved-map", "output", "node", "api-bundle") else str)
    parser.add_argument("--seconds", type=float, default=90.)
    previous = os.umask(0o077)
    try:
        return run(parser.parse_args())
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
