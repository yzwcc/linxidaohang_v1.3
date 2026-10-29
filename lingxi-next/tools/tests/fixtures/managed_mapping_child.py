"""Synthetic mapping children with actual service/owner/API process boundaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import struct
import sys
import threading
import time


ROOT = Path(os.environ.get("LINGXI_MAPPING_TEST_SOURCE_ROOT",
                           Path(__file__).resolve().parents[3])).resolve(strict=True)
SOURCE_ENV = {"LINGXI_MAPPING_TEST_SOURCE_ROOT": str(ROOT)}
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
from lingxi_slam.process_protocol import HealthEmitter
from mapping_owner import ManagedMappingOwner, MappingOwnerLimits
from mapping_service import MappingManager, serve


def synthetic_save(output, session, name):
    files = {key: "synthetic" for key in ("graph.bin", "values.bin", "000000/cloud.bin")}
    files["graph.txt"] = "num_submaps: 1\nnum_all_frames: 2\nnum_matching_cost_factors: 0\n"
    for key in ("odom_lidar.txt", "traj_lidar.txt", "odom_imu.txt", "traj_imu.txt"):
        files[key] = "0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 1\n"
    entries = []
    for key, value in sorted(files.items()):
        file = output / "map" / key
        file.parent.mkdir(parents=True, exist_ok=True)
        data = value.encode()
        file.write_bytes(data)
        entries.append([key, len(data), hashlib.sha256(data).hexdigest()])
    points = struct.pack("<9d", 1, 2, 3, 4, 5, 6, 7, 8, 9)
    (output / "private-saved-points.f64").write_bytes(points)
    receipt = {
        "schemaVersion": 1, "session": session, "name": name, "inputMode": "recorded",
        "counts": {"finalizedFrames": 2, "savedFrames": 2, "submaps": 1, "points": 3},
        "mapInventory": {"num_submaps": 1, "num_all_frames": 2, "num_matching_cost_factors": 0,
                         "fileCount": len(entries), "totalBytes": sum(item[1] for item in entries),
                         "artifactInventorySha256": hashlib.sha256(
                             json.dumps(entries, separators=(",", ":")).encode()).hexdigest()},
        "savedPointsSha256": hashlib.sha256(points).hexdigest(), "freshProcessReloadPassed": True,
        "navigationReady": False, "localizationReady": False, "geometricAccuracyValidated": False,
    }
    (output / "map-ready.json").write_text(json.dumps(receipt))


def native(args):
    args.output.mkdir()
    (args.output.parent / "synthetic-native.pid").write_text(str(os.getpid()))
    sink = os.fdopen(os.dup(1), "wb", buffering=0)
    emitter = HealthEmitter(sink, os.environ["LINGXI_SLAM_SESSION"])
    endpoint = args.output.parent / (args.output.name + ".points.sock")
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(endpoint))
    os.chmod(endpoint, 0o600)
    listener.listen(1)
    listener.setblocking(False)
    connection = None
    packet = bytearray.fromhex(json.loads(
        (ROOT / "shared/fixtures/slam-point-v1.synthetic.json").read_text())["hex"])
    packet[10] = 2  # Recorded-mode fixture, with synthetic geometry only.
    packet[48:64] = bytes.fromhex(emitter.session)
    count, save = 0, False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(0, selectors.EVENT_READ)
            while True:
                count += 1
                try:
                    emitter.emit({"state": "running", "failure": None, "counts": {"completedMessages": count},
                                  "stream": {"counts": {"processedScans": count, "estimatedPoses": count}}})
                except BrokenPipeError:
                    if args.scenario != "stubborn-native":
                        raise
                if connection is None:
                    try:
                        connection, _ = listener.accept()
                        connection.settimeout(.2)
                    except BlockingIOError:
                        pass
                if connection is not None:
                    struct.pack_into("<Qdd", packet, 24, count, 1000. + count * .05, time.monotonic())
                    try:
                        connection.sendall(packet)
                    except OSError:
                        connection.close()
                        connection = None
                if selector.select(.05):
                    command = os.read(0, 6)
                    if args.scenario == "stubborn-native" and not command:
                        selector.unregister(0)
                        continue
                    intent = args.output.parent / (args.output.name + ".save-request.json")
                    save = command == b"stop\n" and intent.is_file()
                    if save:
                        value = json.loads(intent.read_text())
                        time.sleep(.25)  # Exercise API point draining while saving.
                        synthetic_save(args.output, emitter.session, value["name"])
                    break
        emitter.emit({"state": "stopped", "failure": None, "counts": {"completedMessages": count},
                      "stream": {"counts": {"processedScans": count, "estimatedPoses": count}}})
        return 0
    finally:
        if connection is not None:
            connection.close()
        listener.close()
        endpoint.unlink(missing_ok=True)
        sink.close()


def service(args):
    if args.scenario == "service-startup":
        return 1
    manager = MappingManager(args.maps, lambda output: [
        sys.executable, "-B", str(Path(__file__).resolve()), "native",
        "--output", str(output), "--scenario", args.scenario,
    ], {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **SOURCE_ENV}, points_enabled=True)
    serve(args.socket, manager, parent_input=sys.stdin.buffer,
          status=lambda value: print(json.dumps(value), flush=True))
    return 0


def owner(args):
    stop = threading.Event()
    for value in (signal.SIGTERM, signal.SIGINT):
        signal.signal(value, lambda *_: stop.set())
    runtime = ManagedMappingOwner(
        [sys.executable, "-B", str(Path(__file__).resolve()), "service", "--scenario", args.scenario],
        [args.node, args.bundle],
        service_environment={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **SOURCE_ENV},
        api_environment={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                         **({"LINGXI_PAIRING_CODE_SHA256": hashlib.sha256(
                             b"isolated-managed-mapping-test").hexdigest(), "LINGXI_HOST": "127.0.0.1",
                             **({"LINGXI_TLS_KEY": args.tls_key, "LINGXI_TLS_CERT": args.tls_cert}
                                if args.scenario == "production-tls" else {})}
                            if args.scenario.startswith("production-") else {"LINGXI_DEMO_MODE": "1"}),
                         "LINGXI_PORT": "-1" if args.scenario == "api-startup" else "0",
                         "LINGXI_WEB_ROOT": os.environ.get("LINGXI_MAPPING_TEST_WEB_ROOT",
                                                          str(args.output / "absent")),
                         "LINGXI_DEMO_PAIRING_CODE": "isolated-managed-mapping-test"},
        output=args.output, limits=MappingOwnerLimits(service_stop_seconds=2., api_stop_seconds=2.))
    result = runtime.run(stop=stop, seconds=.25 if args.scenario == "duration" else 30.,
                         on_listening=lambda value: print(json.dumps({
                             **value, "apiPid": runtime.api.pid, "servicePid": runtime.service.pid,
                             "sourceRoot": str(ROOT),
                         }), flush=True))
    return 0 if result["result"] == "stopped" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("owner", "service", "native"))
    for key in ("output", "maps", "socket"):
        parser.add_argument("--" + key, type=Path)
    for key in ("node", "bundle", "scenario", "tls-key", "tls-cert"):
        parser.add_argument("--" + key, default="healthy" if key == "scenario" else None)
    parser.add_argument("--parent-stdin", action="store_true")
    args = parser.parse_args()
    raise SystemExit({"owner": owner, "service": service, "native": native}[args.command](args))
