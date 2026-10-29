"""Stage and run the local recorded GLIM service plus the paired API probe."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "tools")]
from mapping_service import configuration, private_json

SOURCE_NAMES = (
    "robot-runtime/python/mapping_service.py", "robot-runtime/python/controlled_mapping.py",
    "robot-runtime/python/mapping_points.py", "robot-runtime/python/mapping_platform.py",
    "robot-slam/lingxi_slam/mapping_worker.py", "robot-slam/lingxi_slam/mapping_session.py",
    "robot-slam/lingxi_slam/mapping_input.py", "robot-slam/lingxi_slam/sensor_admission.py",
    "robot-api/src/mapping-backend.ts", "robot-api/src/server.ts", "robot-api/src/domain.ts",
    "robot-api/src/mapping-points.ts", "robot-api/src/slam-points.ts",
    "robot-slam/lingxi_slam/point_data.py", "robot-slam/lingxi_slam/point_pipe.py",
    "robot-slam/lingxi_slam/point_output.py",
    "shared/contracts.ts", "tools/check-mapping-control.ts", "tools/run_mapping_control_trial.py",
)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def run(args):
    config, _ = configuration(args.config)
    if args.point_adapter is not None:
        entry = args.point_adapter
        if not entry.is_absolute() or entry.resolve(strict=True) != entry or not entry.is_dir():
            raise ValueError("direct_frozen_point_adapter_required")
        config = {**config, "pointAdapter": str(entry)}
    points_enabled = "pointAdapter" in config
    cache = (Path.home() / ".cache/lingxi-next").resolve(strict=True)
    if (sys.platform != "linux" or os.geteuid() == 0 or not args.output.is_absolute()
            or args.output.exists() or not args.output.resolve().is_relative_to(cache)
            or args.output.resolve() != args.output
            or any(args.output.is_relative_to(Path(config[key])) for key in
                   ("work", "adapter", "mappingAdapter", "bundle", "reference",
                    *(("pointAdapter",) if points_enabled else ())))):
        raise ValueError("new_private_local_linux_mapping_trial_required")
    args.output.mkdir(mode=0o700)
    runtime = args.output / "runtime-source"
    for name in ("tools", "robot-slam", "robot-runtime/python"):
        shutil.copytree(ROOT / name, runtime / name, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", "*.pyc"))
    (runtime / "docs").mkdir(mode=0o700)
    shutil.copyfile(ROOT / "docs/slam-stream-input-evidence.json",
                    runtime / "docs/slam-stream-input-evidence.json")
    config_file = args.output / "private-config.json"
    private_json(config_file, config)
    probe = args.output / "probe.cjs"
    shutil.copyfile(args.probe, probe)
    maps = args.output / "maps"
    maps.mkdir(mode=0o700)
    socket = args.output / "control.sock"
    if len(os.fsencode(socket)) >= 100:
        raise ValueError("short_local_mapping_socket_path_required")
    report = {"result": "failed", "robotContacted": False, "motionCommandsSent": False,
              "inputMode": "recorded", "probeSha256": digest(probe),
              "sourceSha256": {name: digest(ROOT / name) for name in SOURCE_NAMES}}
    service = None
    with (args.output / "private-service.log").open("xb") as log:
        try:
            service = subprocess.Popen([
                sys.executable, "-B", str(runtime / "robot-runtime/python/mapping_service.py"),
                "--config", str(config_file), "--maps", str(maps), "--socket", str(socket),
            ], stdout=log, stderr=log, start_new_session=True, stdin=subprocess.DEVNULL)
            deadline = time.monotonic() + 15
            while not socket.exists() and time.monotonic() < deadline:
                if service.poll() is not None:
                    raise RuntimeError("mapping_service_exited_before_socket")
                time.sleep(.02)
            if not socket.is_socket():
                raise RuntimeError("mapping_service_socket_deadline")
            with (args.output / "private-probe.log").open("xb") as probe_log:
                result = subprocess.run([
                    str(args.node), str(probe), str(socket), str(maps), str(args.output), "4144",
                    *(["points"] if points_enabled else []),
                ], stdout=probe_log, stderr=probe_log, timeout=300, check=False)
            if result.returncode:
                raise RuntimeError("recorded_mapping_api_probe_failed")
            api = json.loads((args.output / "private-api-report.json").read_text())
            if api["result"] != "recorded_native_service_api_save_discard_verified":
                raise RuntimeError("complete_api_probe_required")
            children = []
            for child_file in sorted(maps.glob("*.private-report.json")):
                child = json.loads(child_file.read_text())
                worker = child["worker"]
                if (child["result"] not in ("saved", "discarded") or child["controlThreadAlive"]
                        or not child["nativeOwnerIsSingleNonMainThread"]
                        or child["nativeCalls"]["create"] != 1 or child["nativeCalls"]["close"] != 1
                        or worker["threadAlive"] or worker["failure"] or worker["state"] != "stopped"
                        or worker["counts"]["completedMessages"] != 4144
                        or worker["counts"]["rejectedMessages"] or worker["counts"]["discardedMessages"]):
                    raise RuntimeError("complete_native_mapping_child_required")
                children.append({"result": child["result"], "session": child["session"],
                                 "reportSha256": digest(child_file), "nativeCalls": child["nativeCalls"]})
                if points_enabled:
                    points = child.get("points", [])
                    if (len(points) != 1 or points[0]["sent"] < 3
                            or points[0]["failed"] or points[0]["threadAlive"]):
                        raise RuntimeError("complete_native_point_writer_required")
                    children[-1]["points"] = points[0]
            if len(children) != 2 or {child["result"] for child in children} != {"saved", "discarded"}:
                raise RuntimeError("exact_save_and_discard_children_required")
            receipts = list(maps.glob("*/map-ready.json"))
            if len(receipts) != 1:
                raise RuntimeError("exact_one_ready_map_required")
            receipt = json.loads(receipts[0].read_text())
            from run_glim_mapping_trial import compare_points, inventory_map
            if inventory_map(receipts[0].parent / "map") != receipt["mapInventory"]:
                raise RuntimeError("unchanged_saved_map_inventory_required")
            comparison = compare_points(receipts[0].parent / "private-saved-points.f64",
                                        receipts[0].parent / "private-reloaded-points.f64",
                                        receipt["counts"]["points"])
            report.update(result=api["result"], sessions=api["sessions"], children=children,
                          roundTrip=comparison, apiReportSha256=digest(args.output / "private-api-report.json"))
            if points_enabled:
                if not api.get("pointStream") or not all(api["pointStream"].values()):
                    raise RuntimeError("complete_paired_point_stream_probe_required")
                report["pointStream"] = api["pointStream"]
            if report["sourceSha256"] != {name: digest(ROOT / name) for name in SOURCE_NAMES}:
                report["result"] = "failed"
                raise RuntimeError("mapping_control_sources_changed_during_trial")
        finally:
            if service is not None:
                if service.poll() is None:
                    service.send_signal(signal.SIGTERM)
                try:
                    report["serviceExitCode"] = service.wait(timeout=110)
                except subprocess.TimeoutExpired:
                    os.killpg(service.pid, signal.SIGKILL)
                    service.wait(timeout=5)
                    report["result"] = "failed"
                report["socketRemoved"] = not socket.exists()
                if report.get("serviceExitCode") != 0 or not report["socketRemoved"]:
                    report["result"] = "failed"
            private_json(args.output / "private-trial-report.json", report)
    if report["result"] == "failed":
        raise RuntimeError("mapping_trial_not_confirmed")
    return report


def public_evidence(report, trial):
    """Export only reviewed aggregate fields, not private reports, IDs, maps or paths."""
    return {
        "schemaVersion": 1, "date": "2026-10-30", "result": report["result"],
        "inputMode": "recorded", "machine": "x86_64", "rate": 1.,
        "privateTrialReportSha256": digest(trial / "private-trial-report.json"),
        "probeSha256": report["probeSha256"], "sourceSha256": report["sourceSha256"],
        "runs": [{key: entry[key] for key in (
            "save", "completedMessages", "processedScans", "estimatedPoses", "counts", "previewPoints",
            "points", "browserReconnected", "unavailableAfterStop")
                  if key in entry} for entry in report["sessions"]],
        "children": [{key: entry[key] for key in ("result", "reportSha256", "nativeCalls", "points") if key in entry}
                     for entry in report["children"]],
        **({"pointStream": report["pointStream"]} if "pointStream" in report else {}),
        "roundTrip": report["roundTrip"], "serviceExitCode": report["serviceExitCode"],
        "socketRemoved": report["socketRemoved"],
        "boundaries": dict.fromkeys((
            "robotContacted", "liveSensorsSubscribed", "motionCommandsSent", "targetArm64Validated",
            "geometricAccuracyValidated", "localizationReady", "navigationReady", "deploymentApproved",
        ), False),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "output", "node", "probe"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--point-adapter", type=Path)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        report = run(args)
        if args.evidence is not None:
            config, _ = configuration(args.config)
            if config["rate"] != 1.:
                raise ValueError("one_times_public_trial_evidence_required")
            private_json(args.evidence, public_evidence(report, args.output))
        print(json.dumps({"result": report["result"], "serviceExitCode": report["serviceExitCode"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
