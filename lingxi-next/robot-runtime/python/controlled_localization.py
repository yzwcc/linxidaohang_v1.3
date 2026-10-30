"""Supervised recorded-input localization runtime, without robot connections."""
import argparse
from dataclasses import asdict
import os
from pathlib import Path
import platform as host_platform
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "tools")]
from lingxi_slam.process_protocol import session_id
from lingxi_slam.process_supervisor import ProcessLimits, ProcessSupervisor
from localization_control import (
    LocalizationControlServer, LocalizedNative, digest, direct, load_map, make_session, strict_json)
from mapping_platform import MappingPlatform, configuration, verify_loaded_libraries
from mapping_service import private_json
import run_glim_supervised_replay as replay


FIELDS = ("config", "saved-map", "map-id", "receipt-sha256", "profile-sha256", "output")
SOURCES = (
    "robot-runtime/python/controlled_localization.py",
    "robot-runtime/python/localization_control.py",
    "robot-runtime/python/mapping_platform.py",
    "robot-slam/lingxi_slam/localization_session.py",
    "robot-slam/lingxi_slam/localization_tracking.py",
    "robot-slam/lingxi_slam/seeded_localization.py",
)


def source_hashes():
    return {name: digest((ROOT / name).read_bytes()) for name in SOURCES}


def verified_configuration(file):
    config, environment = configuration(file)
    if (sys.platform != "linux" or host_platform.machine() != "x86_64" or os.geteuid() == 0
            or config.get("platform") == "arm64-humble"
            or config["inputMode"] != "recorded" or "pointAdapter" not in config):
        raise ValueError("ordinary_user_x64_recorded_localization_with_corrected_points_required")
    return config, environment


def verify_completion(child_report, final, *, frozen, binding, sources, config_hash, rate):
    """Accept only the complete, bound worker result and revoked final state."""
    replay.complete_child(child_report, frozen["journal"])
    identity, state = asdict(binding), final["state"]
    integrity = child_report["inputIntegrity"]
    if (child_report["result"] != replay.CHILD_RESULT or child_report["session"] != binding.session
            or any(integrity.get(key) != value for key, value in frozen.items())
            or integrity["localizationBinding"] != identity
            or integrity["localizationSourceSha256"] != sources
            or integrity["localizationConfigSha256"] != config_hash
            or child_report["replayRate"] != rate
            or state["binding"] != identity or state["state"] != "stopped"
            or state["mapFromSensor"] is not None or state["referenceFrame"] is not None
            or state["cleanupComplete"] is not True or state["workerClosed"] is not True
            or state["workerReady"] is not False or state["requestPending"] is not False
            or any(state[name] is not False for name in ("localizationReady", "navigationReady"))
            or any(final[name] is not False for name in (
                "robotContacted", "motionCommandsSent", "mapProfileAssociationVerified"))):
        raise ValueError("complete_same_session_localization_cleanup_required")


def child(args, sink):
    from importlib.metadata import version
    import small_gicp
    from lingxi_slam.point_output import NativePointOutput, PointOutputNative

    config, environment = verified_configuration(args.config)
    if any(os.environ.get(key) != value for key, value in environment.items()):
        raise ValueError("unchanged_reviewed_localization_environment_required")
    platform = MappingPlatform(config)
    profile = platform.args.bundle / "private-profile.json"
    binding, points = load_map(
        args.saved_map, args.map_id, args.receipt_sha256, profile, args.profile_sha256,
        session=session_id(os.environ.get("LINGXI_SLAM_SESSION")), input_mode="recorded")
    session = make_session(binding, points)
    decode = platform.decoder()
    config_hash = digest(args.config.read_bytes())
    sources = source_hashes()
    gicp_library = Path(small_gicp.__file__).resolve(strict=True)
    gicp_hash = digest(gicp_library.read_bytes())
    frozen = {**platform.frozen, "localizationBinding": asdict(binding),
              "localizationConfigSha256": config_hash, "localizationSourceSha256": sources,
              "smallGicpExtensionSha256": gicp_hash}
    endpoint = args.output / "localization.sock"

    def verify_inputs():
        current = platform.verify()
        if (digest(args.config.read_bytes()) != config_hash
                or source_hashes() != sources or digest(gicp_library.read_bytes()) != gicp_hash):
            raise ValueError("localization_configuration_changed")
        reloaded, _ = load_map(
            args.saved_map, args.map_id, args.receipt_sha256, profile, args.profile_sha256,
            session=binding.session, input_mode="recorded")
        if reloaded != binding:
            raise ValueError("localization_binding_changed")
        return {**current, "localizationBinding": asdict(binding),
                "localizationConfigSha256": config_hash, "localizationSourceSha256": sources,
                "smallGicpExtensionSha256": gicp_hash}

    def native_factory():
        def point_factory(accept):
            output = NativePointOutput(platform.point_library)
            # The shared replay owner records native calls around this whole composition.
            return PointOutputNative(lambda: platform.native(lambda _: None), output, accept)
        return LocalizedNative(session, point_factory)

    def verify_loaded():
        platform.loaded(points=True)
        verify_loaded_libraries({gicp_library: gicp_hash}, Path("/proc/self/maps").read_text())
        return {**platform.frozen["nativeLibrariesSha256"],
                platform.point_library.name: platform.frozen["pointExtensionLibrarySha256"],
                gicp_library.name: gicp_hash}

    options = argparse.Namespace(
        rate=config["rate"], hypothesis=platform.hypothesis,
        reference=platform.args.reference, output=args.output)
    try:
        with LocalizationControlServer(endpoint, session) as server:
            server.start()
            return replay.recorded_child(
                options, sink, integrity=frozen, native_factory=native_factory,
                verify_loaded=verify_loaded, decode=decode, verify_inputs=verify_inputs,
                versions={name: version(name) for name in ("numpy", "rosbags", "small-gicp")})
    finally:
        private_json(args.output / "localization-final.json", {
            "state": session.state(), "robotContacted": False, "motionCommandsSent": False,
            "mapProfileAssociationVerified": False,
        })


def run(args):
    config, environment = verified_configuration(args.config)
    platform = MappingPlatform(config)
    config_hash, sources = digest(args.config.read_bytes()), source_hashes()
    direct(args.output.parent, directory=True)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("new_private_localization_output_required")
    if any(path == args.output or path in args.output.parents or args.output in path.parents
           for path in (args.config, args.saved_map)):
        raise ValueError("separate_localization_output_required")
    args.output.mkdir(mode=0o700)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "child"]
    for field in FIELDS:
        command.extend(("--" + field, str(getattr(args, field.replace("-", "_")))))
    owner = ProcessSupervisor(
        command, cwd=args.output, environment=environment,
        stderr_path=args.output / "private-child.log",
        limits=ProcessLimits(startup_seconds=30., progress_seconds=2., shutdown_seconds=5.))
    result = {"result": "failed", "robotContacted": False, "motionCommandsSent": False,
              "localizationReady": False, "navigationReady": False,
              "mapProfileAssociationVerified": False, "localizationSourceSha256": sources,
              "localizationConfigSha256": config_hash}
    try:
        binding, _ = load_map(
            args.saved_map, args.map_id, args.receipt_sha256,
            platform.args.bundle / "private-profile.json", args.profile_sha256,
            session=owner.session, input_mode="recorded")
        expected = platform.frozen["journal"]
        terminal = replay.coordinate(owner, expected["records"], args.seconds)
        child_report = strict_json((args.output / "child-report.json").read_bytes())
        final = strict_json((args.output / "localization-final.json").read_bytes())
        verify_completion(child_report, final, frozen=platform.frozen, binding=binding,
                          sources=sources, config_hash=config_hash, rate=config["rate"])
        if (platform.verify() != platform.frozen or source_hashes() != sources
                or digest(args.config.read_bytes()) != config_hash):
            raise ValueError("unchanged_localization_runtime_inputs_required")
        result.update(result="recorded_localization_runtime_closed", process=terminal,
                      filesSha256={name: digest((args.output / name).read_bytes())
                                   for name in ("child-report.json", "localization-final.json")})
    except Exception as error:
        result["errorType"] = type(error).__name__
    finally:
        result["process"] = owner.stop()
        terminal = result["process"]
        if (terminal["state"] != "stopped" or terminal["exitCode"] != 0 or terminal["forcedTermination"]
                or not terminal["cleanupComplete"] or terminal["processAlive"] or terminal["monitorAlive"]):
            result["result"] = "failed"
        private_json(args.output / "localization-runtime-report.json", result)
    return 0 if result["result"] != "failed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "child"):
        command = commands.add_parser(name)
        for field in FIELDS:
            command.add_argument("--" + field, required=True,
                                 type=Path if field in ("config", "saved-map", "output") else str)
        if name == "run":
            command.add_argument("--seconds", required=True, type=float)
    args = parser.parse_args()
    if args.command == "run" and not 5 <= args.seconds <= 120:
        raise ValueError("bounded_offline_localization_duration_required")
    previous = os.umask(0o077)
    try:
        if args.command == "child":
            with os.fdopen(os.dup(1), "wb", buffering=0) as sink:
                os.dup2(2, 1)
                return child(args, sink)
        return run(args)
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
