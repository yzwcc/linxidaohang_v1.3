"""Synthetic geometry and workers with the actual shared service, owner and API."""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(os.environ.get("LINGXI_SLAM_TEST_SOURCE", Path(__file__).resolve().parents[3]))
os.environ["LINGXI_MAPPING_TEST_SOURCE_ROOT"] = str(ROOT)
os.environ["LINGXI_LOCALIZATION_TEST_SOURCE_ROOT"] = str(ROOT)
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
from lingxi_slam.activity_lease import ActivityGate
from localization_service import LocalizationBinding, LocalizationManager, SavedMapResolver
from mapping_service import MappingManager
from slam_service import serve
from slam_owner import ManagedSlamOwner
from mapping_owner import MappingOwnerLimits
import managed_mapping_child as mapping_fixture
import managed_localization_child as localization_fixture

PROVENANCE = {"schemaVersion": 1, "profileSha256": "e" * 64,
              "timingHypothesis": "header_is_scan_start", "configurationSha256": {"lidar.json": "f" * 64}}


def native_mapping(args):
    args.scenario = "healthy"
    result = mapping_fixture.native(args)
    file = args.output / "map-ready.json"
    if file.exists():
        receipt = json.loads(file.read_bytes())
        file.write_text(json.dumps({**receipt, "schemaVersion": 2, "sensorProvenance": PROVENANCE}))
    return result


def native_localization(args):
    chosen = LocalizationBinding(**json.loads(args.binding))
    localization_fixture.binding = lambda session, _: replace(chosen, session=session)
    args.scenario = "live-normal"  # Continuous synthetic health, not live sensor data.
    return localization_fixture.native(args)


def service(args):
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                   "LINGXI_SLAM_TEST_SOURCE": str(ROOT)}
    gate = ActivityGate(args.maps / ".slam-activity.lock")
    mapping = MappingManager(args.maps, lambda output: [sys.executable, "-B", __file__,
        "mapping", "--output", str(output)], environment, points_enabled=True, activity_gate=gate)
    resolver = SavedMapResolver(args.maps, "e" * 64, "recorded", PROVENANCE)
    localization = LocalizationManager(args.runs, resolver, lambda chosen, output: [
        sys.executable, "-B", __file__, "localization", "--output", str(output),
        "--binding", json.dumps(asdict(chosen.binding))], environment, input_mode="recorded", activity_gate=gate)
    serve(args.socket, args.localization_socket, mapping, localization, parent_input=sys.stdin.buffer,
          status=lambda value: print(json.dumps(value), flush=True))
    return 0


def owner(args):
    stop = threading.Event()
    for value in (signal.SIGTERM, signal.SIGINT):
        signal.signal(value, lambda *_: stop.set())
    runtime = ManagedSlamOwner([sys.executable, "-B", __file__, "service"], [str(args.node), str(args.bundle)],
        maps=args.maps, output=args.output,
        service_environment={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                             "LINGXI_SLAM_TEST_SOURCE": str(ROOT)},
        api_environment={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LINGXI_HOST": "127.0.0.1", "LINGXI_PORT": "0",
                         "LINGXI_TLS_KEY": str(args.tls_key), "LINGXI_TLS_CERT": str(args.tls_cert),
                         "LINGXI_WEB_ROOT": os.environ.get("LINGXI_SLAM_TEST_WEB", str(ROOT / "dist")),
                         "LINGXI_PAIRING_CODE_SHA256": hashlib.sha256(b"synthetic-slam-workspace").hexdigest()},
        limits=MappingOwnerLimits(startup_seconds=15., service_stop_seconds=10., api_stop_seconds=5.))
    report = runtime.run(stop=stop, seconds=None, on_listening=lambda value: print(json.dumps({
        **value, "apiPid": runtime.api.pid, "servicePid": runtime.service.pid,
    }), flush=True))
    return 0 if report["result"] == "stopped" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("owner", "service", "mapping", "localization"))
    for name in ("output", "maps", "runs", "socket", "localization-socket", "node", "bundle", "tls-key", "tls-cert"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--binding")
    parser.add_argument("--parent-stdin", action="store_true")
    args = parser.parse_args()
    return {"owner": owner, "service": service, "mapping": native_mapping,
            "localization": native_localization}[args.mode](args)


if __name__ == "__main__":
    raise SystemExit(main())
