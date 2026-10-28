"""Verify the completed private ARM64 loading trial and export aggregate evidence."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from build_slam_cpu_trial import PINS
from slam_target_probe import MODULES, sha256, verify_bundle


ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = (
    "systemPackagesInstalled", "rosStarted", "motionCommandsSent", "realDataProcessedOnTarget",
    "robotCalibrationValidated", "relocalizationValidated", "targetRealtimePerformanceValidated",
    "rosWrapperValidated", "navigationValidated", "deploymentApproved", "rawDataPublished",
)


def validate_recorded_probe(probe):
    synthetic = probe["syntheticProgramResult"]
    if (synthetic.get("inputClassification") != "synthetic_fixture"
            or synthetic.get("nativeModulesLoaded") != 3
            or synthetic.get("robotCalibrationValidated") is not False
            or synthetic.get("navigationValidated") is not False
            or any(not math.isclose(synthetic.get(name, float("nan")), value, rel_tol=0, abs_tol=1e-12)
                   for name, value in (
                       ("frameSeconds", 1000), ("firstSeconds", 0.000332), ("lastSeconds", 0.095181),
                   ))):
        raise ValueError("recorded_synthetic_execution_contract_mismatch")


def export(bundle, report_path, cross):
    manifest = verify_bundle(bundle)
    report = json.loads(report_path.read_text())
    probe = report["targetProbe"]
    validate_recorded_probe(probe)
    cross_path = cross / "cross-build-report.json"
    build = json.loads(cross_path.read_text())
    if (report["result"] != "target_cpu_loading_and_synthetic_execution_passed_not_slam_acceptance"
            or report["probeExitCode"] != 0
            or report["uploadedFiles"] != len(manifest["files"]) + 1
            or report["bundleManifestSha256"] != sha256(bundle / "bundle-manifest.json")
            or probe["bundleManifestSha256"] != report["bundleManifestSha256"]
            or manifest["crossBuildReportSha256"] != sha256(cross_path)
            or build["result"] != "arm64_cross_built_and_linked_not_executed"
            or build["sourceCommits"] != PINS or report["sourceCommits"] != PINS
            or manifest["sourceCommits"] != PINS
            or probe["result"] != "arm64_modules_loaded_and_synthetic_program_executed"
            or probe["target"]["machine"] != "aarch64"
            or not probe["checks"] or any(value is not True for value in probe["checks"].values())
            or probe["boundaries"] != {name: False for name in BOUNDARIES}):
        raise ValueError("completed_isolated_target_loading_trial_required")
    expected_output = (json.dumps(probe, indent=2, allow_nan=False) + "\n").encode()
    if hashlib.sha256(expected_output).hexdigest() != report["privateProbeOutputSha256"]:
        raise ValueError("recorded_target_output_digest_mismatch")
    for artifact in build["artifacts"]:
        directory = "link-smoke" if artifact["file"] == "lingxi_glim_time_smoke" else "prefix/lib"
        if sha256(cross / directory / artifact["file"]) != artifact["sha256"]:
            raise ValueError("original_cross_build_artifact_changed")
    observed = probe["mappedBundleLibrariesSha256"]
    if not set(MODULES).issubset(observed) or "libglim.so" not in observed:
        raise ValueError("all_four_cpu_modules_must_be_observed")
    for name, digest in observed.items():
        if digest != manifest["files"].get("lib/" + name, {}).get("sha256"):
            raise ValueError("mapped_library_does_not_match_uploaded_bundle")
    source = ROOT / "tools/slam_target_probe.py"
    if sha256(source) != manifest["files"]["slam_target_probe.py"]["sha256"]:
        raise ValueError("actual_target_probe_source_mismatch")
    return {
        "schemaVersion": 1, "observedDate": "2026-10-28",
        "purpose": "arm64_real_target_module_loading_and_synthetic_execution_not_slam_acceptance",
        "result": report["result"], "sourceCommits": PINS,
        "crossBuildReportSha256": sha256(cross_path), "privateTrialReportSha256": sha256(report_path),
        "bundleManifestSha256": report["bundleManifestSha256"],
        "bundleFilesIncludingManifest": report["uploadedFiles"],
        "bundlePayloadBytes": sum(entry["bytes"] for entry in manifest["files"].values()),
        "actualTargetProbeSourceSha256": sha256(source),
        "toolSourcesAtExport": {
            name: sha256(ROOT / "tools" / name) for name in (
                "prepare_slam_target_bundle.py", "run_slam_target_trial.py",
            )
        },
        "target": {name: probe["target"][name] for name in ("machine", "python", "libc")},
        "mappedBundleLibrariesSha256": {name: observed[name] for name in sorted(observed)},
        "syntheticProgram": {
            name: probe["syntheticProgramResult"][name] for name in (
                "inputClassification", "nativeModulesLoaded", "frameSeconds",
                "firstSeconds", "lastSeconds", "robotCalibrationValidated", "navigationValidated",
            )
        },
        "probeSecondsNotSlamBenchmark": probe["probeSeconds"],
        "checks": {name: probe["checks"][name] for name in (
            "fourCpuModulesLoaded", "bundleIntegrityPassed", "isolatedLibrariesObserved",
            "syntheticExecutablePassed",
        )},
        "boundaries": {name: False for name in BOUNDARIES},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "report", "cross-work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("existing_target_evidence_is_preserved")
    evidence = export(args.bundle, args.report, args.cross_work)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": "verified_target_loading_aggregates_exported",
                      "mappedBundleLibraries": len(evidence["mappedBundleLibrariesSha256"])}))


if __name__ == "__main__":
    main()
