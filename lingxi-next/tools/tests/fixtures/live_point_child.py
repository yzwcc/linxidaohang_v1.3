"""Isolated synthetic process fixtures, never actual sensors or robot results."""
import argparse
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


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
from lingxi_slam.process_protocol import HealthEmitter
from lingxi_slam.process_supervisor import ProcessLimits
from point_owner import OwnerLimits, PointRuntimeOwner


def sensor(args):
    if args.scenario == "before-connect":
        return 1
    fixture = json.loads((ROOT / "shared/fixtures/slam-point-v1.synthetic.json").read_text(encoding="utf-8"))
    sink = os.fdopen(os.dup(1), "wb", buffering=0)
    emitter = HealthEmitter(sink, os.environ["LINGXI_SLAM_SESSION"])
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    count, stop = 0, False
    try:
        if args.scenario != "no-connect":
            connection.connect(str(args.point_socket))
        with selectors.DefaultSelector() as selector:
            selector.register(0, selectors.EVENT_READ)
            deadline = time.monotonic() + 20.
            while time.monotonic() < deadline:
                count += 1
                emitter.emit({"state": "running", "failure": None,
                              "counts": {"completedMessages": count},
                              "stream": {"counts": {"processedScans": count, "estimatedPoses": count}}})
                packet = bytearray.fromhex(fixture["hex"])
                packet[10] = 1  # Live-mode protocol in this explicitly isolated synthetic test only.
                packet[48:64] = bytes.fromhex(emitter.session)
                struct.pack_into("<Qdd", packet, 24, count, 1000. + count * .1, time.monotonic())
                if args.scenario != "no-connect":
                    connection.sendall(packet)
                if selector.select(.05):
                    if os.read(0, 6) != b"stop\n":
                        return 1
                    stop = True
                    break
        emitter.emit({"state": "stopped", "failure": None, "counts": {"completedMessages": count},
                      "stream": {"counts": {"processedScans": count, "estimatedPoses": count}}})
        return 0 if stop else 1
    except (BrokenPipeError, ConnectionResetError):
        return 1
    finally:
        connection.close()
        sink.close()


def owner(args):
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    runtime = PointRuntimeOwner(
        [sys.executable, "-B", str(Path(__file__).resolve()), "sensor", "--scenario", args.scenario],
        [args.node, args.bundle],
        native_environment={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
        api_environment={
            "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LINGXI_DEMO_MODE": "1",
            "LINGXI_PORT": "-1" if args.scenario == "api-startup" else "0",
            "LINGXI_DATA_DIR": str(args.output / "data"), "LINGXI_WEB_ROOT": str(args.output / "no-static"),
            "LINGXI_DEMO_PAIRING_CODE": "isolated-point-owner-fixture",
        }, output=args.output, limits=OwnerLimits(point_connect_seconds=4., api_start_seconds=15.),
        process_limits=ProcessLimits(startup_seconds=4., shutdown_seconds=3.))

    def ready(status):
        print(json.dumps({**status, "sensorPid": runtime.native.process.pid, "apiPid": runtime.api.pid}), flush=True)

    report = runtime.run(stop=stop, seconds=.25 if args.scenario == "duration" else 15., on_listening=ready)
    return 0 if report["result"] == "stopped" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sensor", "owner"))
    parser.add_argument("--scenario", default="healthy",
                        choices=("healthy", "before-connect", "no-connect", "api-startup", "duration"))
    parser.add_argument("--point-socket", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--node")
    parser.add_argument("--bundle")
    arguments = parser.parse_args()
    raise SystemExit(sensor(arguments) if arguments.command == "sensor" else owner(arguments))
