"""Record/reload real acquisition sources through the explicit product map lifecycle."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import build_glim_observations as build
import run_async_mapping_session_trial as product

async_mapping = build.asynchronous
ROOT = build.ROOT
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.mapping_session import MappingSession, SaveDecision
from lingxi_slam.observed_mapping import ObservedMappingLibrary
from lingxi_slam.observation_archive import inspect_observations, sha256
from lingxi_slam.observation_binding import bind_observations, verify_observations


def implementation(args):
    paths = [Path(__file__).resolve(), ROOT / "tools/build_glim_observations.py",
        ROOT / "docs/slam-async-mapping-evidence.json", *[
        ROOT / "robot-slam/lingxi_slam" / (name + ".py") for name in
        ("observed_mapping", "observation_archive", "observation_binding")]]
    value = {"build": sha256(args.mapping_adapter / "build-report.json"),
             "sources": {path.relative_to(ROOT).as_posix(): sha256(path) for path in paths}}
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reload(args):
    library = build.verified_adapter(args)
    before = async_mapping.full.inventory(args.saved / "map")
    counts = ObservedMappingLibrary(library).reload(args.output / "full-config", args.saved / "map",
                                                   args.saved / "private-reloaded-points.f64")
    if counts["submaps"] != before["num_submaps"] or counts["frames"] != before["num_all_frames"]:
        raise ValueError("complete_observed_map_fresh_reload_required")
    comparison = async_mapping.full.compare_points(args.saved / "private-saved-points.f64",
        args.saved / "private-reloaded-points.f64", counts["points"])
    observations = inspect_observations((args.saved / "private-observations.bin").absolute(),
                                       sha256(args.saved / "private-observations.bin"))
    report = {"pid": os.getpid(), "loadedSubmaps": counts["submaps"], "comparison": comparison,
              "sensorProvenance": product.provenance(args), "mapInventory": before, "observations": observations}
    if async_mapping.full.inventory(args.saved / "map") != before:
        raise ValueError("observed_map_reload_must_be_readonly")
    async_mapping.full.save(args.saved / "reload-report.json", report)
    return report


def run(args):
    library, frozen = build.verified_adapter(args), implementation(args)
    saved = args.output / "maps" / uuid.uuid4().hex
    result = {}
    case = args.dataset + ("-start" if args.hypothesis == "header_is_scan_start" else "-end")
    evidence = json.loads((ROOT / "docs/slam-async-mapping-evidence.json").read_text())
    expected = next(item for item in evidence["trials"] if item["case"] == case)

    class Native:
        def __init__(self, odometry, baseline, module):
            async_mapping.full.full_config(args.upstream, baseline, args.output / "full-config")
            config = args.output / "full-config/config_odometry_cpu.json"
            value = async_mapping.full.json5.loads(config.read_text())
            value["odometry_estimation"]["save_imu_rate_trajectory"] = True
            config.write_text(json.dumps(value, indent=2) + "\n")
            saved.parent.mkdir(mode=0o700)
            self.mapper, self.decision = ObservedMappingLibrary(library), SaveDecision()
            self.frames, self.origin, self.started = 0, None, None

            def reload_saved(path):
                command = [sys.executable, "-B", str(Path(__file__).resolve()), "reload"]
                for name in ("work", "upstream", "mapping_adapter", "output", "profile", "hypothesis"):
                    command += ["--" + name.replace("_", "-"), str(getattr(args, name))]
                command += ["--saved", str(path)]
                with (path / "private-reload.log").open("x") as log:
                    child = subprocess.run(command, stdout=log, stderr=log,
                        env=async_mapping.full.replay.library_environment(args.work), timeout=180)
                if child.returncode:
                    raise RuntimeError("observed_map_fresh_reload_failed_see_private_log")
                return json.loads((path / "reload-report.json").read_text())

            def verify_inputs():
                build.verified_adapter(args)
                if implementation(args) != frozen:
                    raise ValueError("frozen_observation_implementation_required")

            self.session = MappingSession(
                lambda: async_mapping.full.replay.Native(odometry, args.output / "full-config", module),
                lambda: self.mapper, self.decision, saved, name="Recorded observed mapping", session=saved.name,
                input_mode="recorded", can_save=self.complete, inspect_map=async_mapping.full.inventory,
                reload_map=reload_saved, sensor_provenance=product.provenance(args),
                verify_sensor_provenance=lambda: product.provenance(args), verify_save=verify_inputs)
            async_mapping.full.observed_libraries(args, library)

        def imu(self, *values):
            if self.origin is None:
                self.origin, self.started = values[0], time.monotonic()
            time.sleep(max(0, self.started + values[0] - self.origin - time.monotonic()))
            return self.session.imu(*values)

        def frame(self, *values):
            value = self.session.frame(*values)
            self.frames += 1
            return value

        def complete(self):
            return (self.frames == expected["recordedScans"] - expected["skippedUnbracketedScans"]
                    and self.mapper.imu_samples == expected["imuSamples"]
                    and self.mapper.estimated_frames == expected["savedFrames"])

        def close(self):
            if self.session.closed:
                return self.session.remaining
            if self.complete():
                self.decision.request_save()
            else:
                self.decision.discard()
            remaining = self.session.close()
            if self.session.receipt is None:
                raise ValueError("reloaded_observed_map_receipt_required")
            reload_report = json.loads((saved / "reload-report.json").read_text())
            if reload_report["observations"] != self.mapper.observation_report:
                raise ValueError("independent_source_geometry_reload_must_match")
            inventory = async_mapping.full.inventory(saved / "map")
            binding = bind_observations(saved, inventory, product.provenance(args), frozen)
            if verify_observations(saved, inventory, product.provenance(args), frozen) != binding:
                raise ValueError("final_observation_binding_must_match")
            result.update(result="recorded_observed_map_saved_reloaded_not_geometry_acceptance",
                observations=binding["observations"], mapInventory=inventory, receiptSha256=binding["mapReceiptSha256"],
                implementationSha256=frozen, worker=self.mapper.cached_health(), roundTrip=reload_report["comparison"],
                independentObservationReloadPassed=True, bindingSha256=sha256(saved / "observations-ready.json"),
                boundaries=dict.fromkeys(async_mapping.full.BOUNDARIES, False))
            return remaining

    report = async_mapping.full.replay.run(args, native_factory=Native)
    if result["observations"]["scans"] != report["estimatedPoses"]:
        raise ValueError("all_recorded_estimated_frames_must_be_archived")
    for path in (args.output / "private-native.log", saved / "private-reload.log"):
        async_mapping.full.check_reload_log(path)
    result.update(peakResidentMiB=report["peakResidentMiB"], dataset=args.dataset, timingHypothesis=args.hypothesis,
                  nativeBuildReportSha256=sha256(args.mapping_adapter / "build-report.json"))
    async_mapping.full.save(args.output / "observed-mapping-report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "reload"):
        command = commands.add_parser(name)
        for field in ("work", "upstream", "mapping-adapter", "output", "profile"):
            command.add_argument("--" + field, type=Path, required=True)
        command.add_argument("--hypothesis", choices=async_mapping.full.replay.HYPOTHESES, required=True)
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
    environment = async_mapping.full.replay.library_environment(args.work)
    if os.environ.get("LD_LIBRARY_PATH") != environment["LD_LIBRARY_PATH"]:
        os.execve(sys.executable, [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:]], environment)
    previous = os.umask(0o077)
    try:
        report = {"run": run, "reload": reload}[args.command](args)
        print(json.dumps({"result": report.get("result", "independent_observation_reload_checked")}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
