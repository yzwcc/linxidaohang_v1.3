"""Synthetic native protocol with real Linux owner, Unix backend and compiled API."""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import sys
import threading
import time

ROOT = Path(os.environ.get("LINGXI_LOCALIZATION_TEST_SOURCE_ROOT", Path(__file__).resolve().parents[3]))
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
from lingxi_slam.localization_session import LocalizationBinding, LocalizationSession
from lingxi_slam.process_protocol import HealthEmitter
from lingxi_slam.process_supervisor import ProcessLimits
from localization_control import LocalizationControlServer
from localization_owner import ManagedLocalizationOwner
from mapping_service import private_json


def binding(session, scenario):
    return LocalizationBinding(session, "live" if scenario.startswith("live") else "recorded", "slam-" + "b" * 32,
                               "c" * 64, "d" * 64, "e" * 64)


class UnusedRegistrar:
    def register(self, *_):
        raise AssertionError("synthetic_owner_fixture_must_not_register_points")


def native(args):
    sink = os.fdopen(os.dup(1), "wb", buffering=0)
    emitter = HealthEmitter(sink, os.environ["LINGXI_SLAM_SESSION"])
    (args.output / "child.pid").write_text(str(os.getpid()))
    identity = binding(emitter.session, args.scenario)
    if args.scenario == "wrong-binding":
        identity = replace(identity, points_sha256="f" * 64)
    session = LocalizationSession(identity, UnusedRegistrar)
    session.open_worker()
    count, stop_received = 0, False
    try:
        with LocalizationControlServer(args.output / "localization.sock", session) as server:
            if args.scenario == "startup":
                with selectors.DefaultSelector() as selector:
                    selector.register(0, selectors.EVENT_READ)
                    selector.select(5)
                return 1
            time.sleep(.15)  # The owner must wait for worker health as well as the socket.
            server.start()
            with selectors.DefaultSelector() as selector:
                selector.register(0, selectors.EVENT_READ)
                while True:
                    if args.scenario != "duration":
                        count = count + 1 if args.scenario.startswith("live") else min(20, count + 1)
                    emitter.emit({
                        "state": "running", "failure": None, "counts": {"completedMessages": count},
                        "stream": {"counts": {"processedScans": count, "estimatedPoses": count}},
                    })
                    if selector.select(.05):
                        stop_received = os.read(0, 6) == b"stop\n"
                        break
        complete = stop_received and count == 20
        # The existing real replay rejects a partial journal, including operator stop.
        partial_failure = args.scenario == "partial-failure" and not complete
        session.close_worker()
        if args.scenario != "live-missing-report":
            private_json(args.output / "fixture-report.json", {
                "complete": complete, "stopReceived": stop_received,
                "workerClosed": session.state()["workerClosed"],
            })
        emitter.emit({
            "state": "failed" if partial_failure else "stopped",
            "failure": "native_replay_composition_failed" if partial_failure else None,
            "counts": {"completedMessages": count},
            "stream": {"counts": {"processedScans": count, "estimatedPoses": count}},
        })
        return 1 if partial_failure else 0
    finally:
        sink.close()


def owner(args):
    stop = threading.Event()
    for value in (signal.SIGTERM, signal.SIGINT):
        signal.signal(value, lambda *_: stop.set())
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    api = {**environment, "LINGXI_HOST": "127.0.0.1", "LINGXI_PORT": "0",
           "LINGXI_PAIRING_CODE_SHA256": hashlib.sha256(b"owned-localization-test").hexdigest(),
           "LINGXI_TLS_KEY": args.tls_key, "LINGXI_TLS_CERT": args.tls_cert}
    if args.scenario == "api-startup":
        api["LINGXI_PORT"] = "-1"
    if args.scenario == "missing-tls":
        del api["LINGXI_TLS_KEY"]
    maps = args.output.parent / "maps"
    maps.mkdir(mode=0o700)
    runtime = ManagedLocalizationOwner(
        lambda output: [sys.executable, "-B", str(Path(__file__).resolve()), "native",
                        "--output", str(output), "--scenario", args.scenario],
        [args.node, args.bundle], native_environment=environment, api_environment=api,
        output=args.output, saved_maps=maps,
        process_limits=ProcessLimits(startup_seconds=5., progress_seconds=2., shutdown_seconds=2.),
        api_seconds=3.)

    def verify():
        final = json.loads((runtime.native_output / "fixture-report.json").read_bytes())
        live = args.scenario.startswith("live")
        if (args.scenario in ("bad-completion", "live-bad-report")
                or not live and not final["complete"]
                or final["stopReceived"] is not True or final["workerClosed"] is not True):
            raise ValueError("synthetic_complete_report_required")

    def recheck():
        if args.scenario == "changed-input":
            raise ValueError("synthetic_unchanged_inputs_required")

    live = args.scenario.startswith("live")
    result = runtime.run(
        binding(runtime.native.session, args.scenario), None if live else 20,
        seconds=1.5 if live else .25 if args.scenario == "duration" else 10.,
        stop=stop, verify_complete=verify, verify_inputs=recheck,
        on_listening=lambda value: print(json.dumps({
            **value, "apiPid": runtime.api.pid, "nativePid": runtime.native.process.pid,
        }), flush=True))
    return {"completed": 0, "interrupted": 2}.get(result["result"], 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("owner", "native"))
    parser.add_argument("--output", type=Path, required=True)
    for name in ("scenario", "node", "bundle", "tls-key", "tls-cert"):
        parser.add_argument("--" + name)
    args = parser.parse_args()
    os.umask(0o077)
    raise SystemExit({"owner": owner, "native": native}[args.command](args))
