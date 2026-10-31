"""Recorded native full mapping through the product save/receipt lifecycle, no robot."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import numpy as np
import run_glim_async_mapping as asynchronous

ROOT = asynchronous.ROOT
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.async_mapping import AsyncMappingLibrary, ENGINE
from lingxi_slam.mapping_session import MappingSession, SaveDecision

SOURCES = ("robot-slam/lingxi_slam/async_mapping.py", "robot-slam/lingxi_slam/mapping_session.py",
           "tools/run_async_mapping_session_trial.py", "docs/slam-async-mapping-evidence.json")


def provenance(args):
    return {"schemaVersion": 1, "profileSha256": asynchronous.full.sha256(args.profile),
            "timingHypothesis": args.hypothesis,
            "configurationSha256": asynchronous.full.configuration_hashes(args.output / "full-config")}


def reload_map(args):
    library = asynchronous.verified_adapter(args)
    before = asynchronous.full.inventory(args.saved / "map")
    counts = AsyncMappingLibrary(library).reload(args.output / "full-config", args.saved / "map",
                                               args.saved / "private-reloaded-points.f64")
    if counts["submaps"] != before["num_submaps"] or counts["frames"] != before["num_all_frames"]:
        raise ValueError("complete_product_full_map_reload_required")
    comparison = asynchronous.full.compare_points(args.saved / "private-saved-points.f64",
                                                  args.saved / "private-reloaded-points.f64", counts["points"])
    report = {"pid": os.getpid(), "loadedSubmaps": counts["submaps"], "comparison": comparison,
              "sensorProvenance": provenance(args), "mapInventory": before}
    if asynchronous.full.inventory(args.saved / "map") != before:
        raise ValueError("product_full_map_reload_must_be_readonly")
    asynchronous.full.save(args.saved / "reload-report.json", report)
    return report


def run(args):
    asynchronous.full.private_output(args.output, args.data_root)
    library = asynchronous.verified_adapter(args)
    frozen = asynchronous.inputs(args)
    source_hashes = {name: asynchronous.full.sha256(ROOT / name) for name in SOURCES}
    evidence = json.loads((ROOT / "docs/slam-async-mapping-evidence.json").read_text())
    case = args.dataset + ("-start" if args.hypothesis == "header_is_scan_start" else "-end")
    expected = next(item for item in evidence["trials"] if item["case"] == case)
    receipts, diagnostics, maps = {}, {}, {}
    identity = uuid.uuid4().hex
    saved = args.output / "maps" / identity

    class ProductNative:
        def __init__(self, odometry, baseline, module):
            asynchronous.full.full_config(args.upstream, baseline, args.output / "full-config")
            saved.parent.mkdir(mode=0o700)
            self.decision = SaveDecision()
            self.frames = 0
            self.clock_origin = self.clock_started = None
            mapper = None

            def mapping_factory():
                nonlocal mapper
                mapper = AsyncMappingLibrary(library)
                return mapper

            def reload(saved_path):
                command = [sys.executable, "-B", str(Path(__file__).resolve()), "reload"]
                for name in ("work", "upstream", "mapping_adapter", "output", "profile", "hypothesis"):
                    command += ["--" + name.replace("_", "-"), str(getattr(args, name))]
                command += ["--saved", str(saved_path)]
                with (saved_path / "private-reload.log").open("x") as log:
                    result = subprocess.run(command, stdout=log, stderr=log,
                        env=asynchronous.full.replay.library_environment(args.work), timeout=180)
                if result.returncode:
                    raise RuntimeError("product_full_map_fresh_reload_failed")
                return json.loads((saved_path / "reload-report.json").read_text())

            self.session = MappingSession(
                lambda: asynchronous.full.replay.Native(odometry, args.output / "full-config", module),
                mapping_factory, self.decision, saved, name="Recorded full mapping session", session=identity,
                input_mode="recorded", can_save=self.complete, inspect_map=asynchronous.full.inventory,
                reload_map=reload, sensor_provenance=provenance(args),
                verify_sensor_provenance=lambda: provenance(args),
                verify_save=self.verify_inputs)
            self.mapper = mapper
            asynchronous.full.observed_libraries(args, library)

        def complete(self):
            return (self.frames == expected["recordedScans"] - expected["skippedUnbracketedScans"]
                    and self.mapper.imu_samples == expected["imuSamples"]
                    and self.mapper.estimated_frames == expected["savedFrames"])

        def verify_inputs(self):
            if (asynchronous.inputs(args) != frozen
                    or source_hashes != {name: asynchronous.full.sha256(ROOT / name) for name in SOURCES}):
                raise ValueError("unchanged_product_mapper_inputs_required")

        def imu(self, stamp, acceleration, angular):
            if self.clock_origin is None:
                self.clock_origin, self.clock_started = stamp, time.monotonic()
            time.sleep(max(0, self.clock_started + stamp - self.clock_origin - time.monotonic()))
            return self.session.imu(stamp, acceleration, angular)

        def frame(self, *args):
            result = self.session.frame(*args)
            self.frames += 1
            return result

        def close(self):
            if self.complete():
                self.decision.request_save()
            else:
                self.decision.discard()
            remaining = self.session.close()
            if self.session.receipt is None:
                raise RuntimeError("explicit_product_receipt_required")
            receipts.update(self.session.receipt)
            diagnostics.update(self.mapper.cached_health())
            maps.update(asynchronous.full.inventory(saved / "map"))
            return remaining

    try:
        report = asynchronous.full.replay.run(args, native_factory=ProductNative)
        if (receipts.get("schemaVersion") != 3 or receipts.get("mappingEngine") != ENGINE
                or receipts["counts"]["savedFrames"] != report["estimatedPoses"]
                or diagnostics["completedImu"] != report["imuSamplesInserted"]
                or receipts["mapInventory"] != maps):
            raise ValueError("complete_product_full_mapping_receipt_required")
        reload = json.loads((saved / "reload-report.json").read_text())
        for path in (args.output / "private-native.log", saved / "private-reload.log"):
            asynchronous.full.check_reload_log(path)
        result = {"result": "recorded_full_mapper_product_receipt_saved_reloaded_not_live_acceptance",
                  "sourceSha256": source_hashes, "dataset": args.dataset, "timingHypothesis": args.hypothesis,
                  "schemaVersion": 3, "engine": ENGINE, "counts": receipts["counts"], "worker": diagnostics,
                  "mapInventory": maps, "roundTrip": reload["comparison"],
                  "worstCombinedFrameMilliseconds": report["combinedMillisecondsMaximum"],
                  "odometryAndCallbackP95Milliseconds": report["odometryMillisecondsP50P95P99"][1],
                  "receiptSha256": asynchronous.full.sha256(saved / "map-ready.json"),
                  "nativeBuildReportSha256": asynchronous.full.sha256(args.mapping_adapter / "build-report.json"),
                  "reportSha256": asynchronous.full.sha256(args.output / "report.json"),
                  "boundaries": dict.fromkeys(asynchronous.full.BOUNDARIES, False)}
        asynchronous.full.save(args.output / "product-mapping-report.json", result)
        return result
    except BaseException as error:
        if args.output.is_dir():
            asynchronous.full.save(args.output / "product-mapping-report.json", {
                "result": "failed_or_interrupted", "errorType": type(error).__name__,
                "boundaries": dict.fromkeys(asynchronous.full.BOUNDARIES, False)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "reload"):
        command = commands.add_parser(name)
        for field in ("work", "upstream", "mapping-adapter", "output", "profile"):
            command.add_argument("--" + field, type=Path, required=True)
        command.add_argument("--hypothesis", choices=asynchronous.full.replay.HYPOTHESES, required=True)
        if name == "run":
            for field in ("adapter", "manifest", "data-root", "diagnostic"):
                command.add_argument("--" + field, type=Path, required=True)
            command.add_argument("--dataset", choices=("static", "dynamic"), required=True)
        else:
            command.add_argument("--saved", type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.absolute())
    environment = asynchronous.full.replay.library_environment(args.work)
    if os.environ.get("LD_LIBRARY_PATH") != environment["LD_LIBRARY_PATH"]:
        os.execve(sys.executable, [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:]], environment)
    previous = os.umask(0o077)
    try:
        result = {"run": run, "reload": reload_map}[args.command](args)
        print(json.dumps({"result": result.get("result", "readonly_product_reload_checked")}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
