"""Probe the isolated Python runtime through passive live mapping and localization."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "robot-runtime/python")]
from check_arm64_python_runtime import check as check_python

RESULT = "isolated_arm64_live_mapping_reload_and_unseeded_localization_checked"
TERMINAL = ("state", "exitCode", "forcedTermination", "cleanupComplete", "processAlive", "monitorAlive")


def clean_terminal(state):
    if (state.get("state") != "stopped" or type(state.get("exitCode")) is not int
            or state["exitCode"] != 0 or state.get("cleanupComplete") is not True
            or any(state.get(key) is not False for key in
                   ("forcedTermination", "processAlive", "monitorAlive"))):
        raise RuntimeError("clean_live_mapping_stop_required")
    return {key: state[key] for key in TERMINAL}


def saved_directory(manager, saved):
    current = manager.current
    if current is None or current["owner"].session != saved["session"]:
        raise RuntimeError("same_mapping_owner_for_saved_directory_required")
    return current["output"]


class PointConsumer:
    """Consume the required private visual channel without retaining sensor data."""

    def __init__(self, directory):
        self.directory, self.stop = directory, threading.Event()
        self.thread = None
        self.frames = self.last_frame = self.trailing_bytes = 0
        self.error = None

    def start(self, state):
        self.thread = threading.Thread(target=self.run, args=(state,), daemon=True)
        self.thread.start()

    def run(self, state):
        from lingxi_slam.point_data import HEADER, MAXIMUM_POINTS, decode
        endpoint = self.directory / state["pointStream"]["socket"]
        pending, deadline = bytearray(), time.monotonic() + 20.
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                while not self.stop.is_set():
                    try:
                        connection.connect(str(endpoint))
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        if time.monotonic() >= deadline:
                            raise TimeoutError("mapping_point_socket_required")
                        self.stop.wait(.01)
                else:
                    return
                connection.settimeout(.2)
                while not self.stop.is_set():
                    try:
                        block = connection.recv(65536)
                    except socket.timeout:
                        continue
                    if not block:
                        self.trailing_bytes = len(pending)
                        return
                    pending.extend(block)
                    while len(pending) >= HEADER.size:
                        count = HEADER.unpack_from(pending)[4]
                        if not 1 <= count <= MAXIMUM_POINTS:
                            raise ValueError("bounded_visual_point_frame_required")
                        length = 192 + count * 24
                        if len(pending) < length:
                            break
                        record = decode(bytes(pending[:length]), state["session"], "live")
                        del pending[:length]
                        frame = record.packet.native_frame_id
                        if frame <= self.last_frame:
                            raise ValueError("increasing_visual_point_frames_required")
                        self.last_frame, self.frames = frame, self.frames + 1
        except BaseException as error:
            self.error = type(error).__name__

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(1.)
            if self.thread.is_alive():
                raise RuntimeError("mapping_point_consumer_cleanup_required")


def capture(manager, *, seconds, consumer=None):
    """Bound the acquisition; never save on input failure or insufficient progress."""
    started = manager.start()
    if consumer is not None:
        consumer.start(started)
    session, beginning = started["session"], time.monotonic()
    while time.monotonic() - beginning < seconds:
        state = manager.state()
        if state["session"] != session or state["phase"] == "failed":
            raise RuntimeError("same_healthy_live_mapping_session_required")
        if consumer is not None and consumer.error is not None:
            raise RuntimeError("healthy_live_point_consumer_required")
        if (state["phase"] == "running" and state["processedScans"] >= 120
                and state["estimatedPoses"] >= 60 and time.monotonic() - beginning >= 10.):
            saved = manager.finish(session, save=True, name="Isolated runtime live check")
            if (saved.get("session") != session or saved.get("confirmed") is not True
                    or saved.get("saved") is not True or saved.get("mapId") != "slam-" + session):
                raise RuntimeError("confirmed_fresh_live_map_required")
            terminal = clean_terminal(manager.current["owner"].snapshot())
            return saved, state, terminal
        time.sleep(.1)
    raise TimeoutError("bounded_live_mapping_progress_required")


def check(args):
    # Perform the numerical probe before importing code which loads native libraries.
    numerical = check_python()
    from check_managed_live_localization import check as check_localization
    from controlled_localization import verified_configuration
    from localization_control import digest, direct, load_map, strict_json
    from mapping_platform import MappingPlatform
    from mapping_service import create_manager, private_json

    config, _ = verified_configuration(args.config, allow_live=True)
    if (config["platform"] != "arm64-humble" or config["inputMode"] != "live"
            or not 15 <= args.mapping_seconds <= 40 or not 10 <= args.seconds <= 30):
        raise ValueError("bounded_arm64_live_runtime_check_required")
    direct(args.output.parent, directory=True)
    if (not args.output.is_absolute() or args.output.resolve() != args.output
            or args.output.exists() or args.output.is_symlink()
            or len(os.fsencode(args.output / "loc/run/native/localization.sock")) >= 100
            or len(os.fsencode(args.output / "maps" / ("0" * 32 + ".points.sock"))) >= 100):
        raise ValueError("new_private_short_live_runtime_output_required")
    args.output.mkdir(mode=0o700)
    report = {"schemaVersion": 1, "result": "failed", "numerical": numerical,
              "motionCommandsSent": False, "vendorPackagesChanged": False,
              "formalServicesChanged": False, "localizationReady": False,
              "navigationReady": False, "physicalCalibrationAccepted": False,
              "productAccepted": False}
    manager = consumer = None
    try:
        platform = MappingPlatform(config)
        config_hash, frozen = digest(args.config.read_bytes()), platform.verify()
        maps = args.output / "maps"
        maps.mkdir(mode=0o700)
        manager = create_manager(args.config, maps)
        consumer = PointConsumer(maps)
        saved, observed, terminal = capture(manager, seconds=args.mapping_seconds, consumer=consumer)
        manager.close()
        consumer.close()
        if consumer.error is not None or consumer.frames < 5:
            raise RuntimeError("validated_live_visual_point_frames_required")
        saved_map = saved_directory(manager, saved)
        receipt_file = saved_map / "map-ready.json"
        receipt_sha, profile_sha = digest(receipt_file.read_bytes()), digest(platform.profile.read_bytes())
        receipt = strict_json(receipt_file.read_bytes())
        if (receipt.get("schemaVersion") != 2 or receipt.get("inputMode") != "live"
                or receipt.get("freshProcessReloadPassed") is not True):
            raise ValueError("provenance_bound_fresh_live_map_required")
        load_map(saved_map, saved["mapId"], receipt_sha, platform.profile, profile_sha,
                 session="0" * 32, input_mode="live", sensor_provenance=platform.sensor_provenance())
        report["mapping"] = {"observed": observed, "process": terminal, "counts": receipt["counts"],
                             "freshProcessReloadPassed": True, "receiptSha256": receipt_sha,
                             "profileSha256": profile_sha, "schemaVersion": 2,
                             "visualPointFrames": consumer.frames, "lastVisualFrame": consumer.last_frame}
        localization_args = argparse.Namespace(**vars(args))
        localization_args.output = args.output / "loc"
        localization_args.saved_map, localization_args.map_id = saved_map, saved["mapId"]
        localization_args.receipt_sha256, localization_args.profile_sha256 = receipt_sha, profile_sha
        report["localization"] = check_localization(localization_args)
        if platform.verify() != frozen or digest(args.config.read_bytes()) != config_hash:
            raise ValueError("unchanged_live_runtime_inputs_required")
        report.update(result=RESULT, configSha256=config_hash, frozenInputs=frozen,
                      checkerSha256=digest(Path(__file__).read_bytes()))
    except BaseException as error:
        report["errorType"] = type(error).__name__
        if manager is not None and manager.current is not None:
            report["mappingFailureState"] = manager.current["owner"].snapshot()
        if consumer is not None:
            report["pointConsumerFailure"] = {"frames": consumer.frames, "errorType": consumer.error}
        raise
    finally:
        try:
            if manager is not None:
                terminal = manager.close()
                report["mappingCleanup"] = (None if terminal is None else
                                            {key: terminal.get(key) for key in TERMINAL})
                if report["result"] == RESULT:
                    clean_terminal(terminal)
            if consumer is not None:
                consumer.close()
        except BaseException as error:
            report.update(result="failed", cleanupErrorType=type(error).__name__)
            raise
        finally:
            if consumer is not None:
                consumer.close()
            private_json(args.output / "live-runtime-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("config", "output", "node", "api-bundle"):
        parser.add_argument("--" + field, type=Path, required=True)
    for field in ("node-sha256", "api-sha256"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--mapping-seconds", type=float, default=35.)
    parser.add_argument("--seconds", type=float, default=15.)
    previous = os.umask(0o077)
    try:
        report = check(parser.parse_args())
        print(json.dumps({"result": report["result"], "mappingCounts": report["mapping"]["counts"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
