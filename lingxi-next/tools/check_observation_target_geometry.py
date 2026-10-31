"""Independently reconstruct synthetic observations collected from the actual ARM64 fixture."""
import argparse
import json
from pathlib import Path
import re
import sys

import numpy as np

import run_observation_target as target

sys.path.insert(0, str(target.ROOT / "robot-slam"))
from lingxi_slam.observation_archive import ObservationArchive, sha256


def check(source, output, manifest_sha256):
    manifest = target.verify_source(source, manifest_sha256)
    report = json.loads((output / "report.json").read_bytes())
    collection = json.loads((output / "collection.json").read_bytes())
    expected = {**{name: digest for name, digest in manifest["expectedCoreLibrariesSha256"].items()
                   if name != "libodometry_estimation_cpu.so"},
                **{Path(name).name: entry["sha256"] for name, entry in manifest["files"].items() if name.startswith("lib/")}}
    loaded = report["loadedLibrariesSha256"]
    if (report["result"] != target.RESULT or report["machine"] != "aarch64"
            or report["manifestSha256"] != manifest_sha256
            or report["boundaries"] != dict.fromkeys(target.BOUNDARIES, False)
            or len(loaded) != len(expected) or {Path(name).name: value for name, value in loaded.items()} != expected
            or collection["result"] != target.RESULT or collection["manifestSha256"] != manifest_sha256
            or collection["reportSha256"] != sha256(output / "report.json")
            or collection["hostIdentityVerified"] is not True or collection["syntheticOnly"] is not True
            or [row["scenario"] for row in report["scenarios"]] != list(target.SCENARIOS)):
        raise ValueError("actual_source_bound_arm64_observation_collection_required")
    scans = {}
    for row in report["scenarios"]:
        mode = row["scenario"]
        invalid = mode in ("unbracketed", "missing-trajectory")
        if (type(row["exitCode"]) is not int or row["exitCode"] != 0
                or row["loadedLibrariesMatched"] is not True or row["archiveCreated"] is not (not invalid)):
            raise ValueError("all_native_scenarios_must_complete_without_fabrication")
        path = output / (mode + ".bin")
        if invalid:
            if path.exists():
                raise ValueError("unbracketed_sources_must_not_produce_geometry")
        else:
            if path.stat().st_size != row["archiveBytes"]:
                raise ValueError("complete_collected_synthetic_archive_required")
            archive = ObservationArchive(path.absolute(), row["archiveSha256"])
            scan, = archive
            if (archive.submaps, archive.scans, archive.points, archive.revision) != (1, 1, 3, 2):
                raise ValueError("exact_native_fixture_counts_required")
            scans[mode] = scan
    origins = np.array([[2, 0, 0], [3, 0, 0], [4, 0, 0]])
    directions = np.array([[1, 0, 0], [2**-.5, 2**-.5, 0], [0, 1, 0]])
    for mode in ("base", "fallback", "reference-shift"):
        np.testing.assert_allclose(scans[mode].origins_raw, origins, rtol=0, atol=1e-12)
        np.testing.assert_allclose(scans[mode].endpoints_raw, origins + directions, rtol=0, atol=1e-12)
    if scans["fallback"].trajectory_kind != "original_lio_imu":
        raise ValueError("whole_original_lio_trajectory_fallback_required")
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    for original, corrected in ((scans["base"].origins_raw, scans["corrected"].origins_raw),
                                (scans["base"].endpoints_raw, scans["corrected"].endpoints_raw)):
        np.testing.assert_allclose(corrected, (original - [10, 0, 0]) @ rotation.T + [20, 0, 0], rtol=0, atol=1e-12)
    if scans["base"].source_sha256 != scans["corrected"].source_sha256:
        raise ValueError("global_corrections_must_preserve_acquisition_identity")
    lever_arm_origins = np.array([[1, -3, 2], [3 + 2**.5, -2 * 2**.5, 2], [7, -1, 2]])
    np.testing.assert_allclose(scans["extrinsic"].origins_raw, lever_arm_origins, rtol=0, atol=1e-12)
    np.testing.assert_allclose(scans["extrinsic"].endpoints_raw, lever_arm_origins + directions, rtol=0, atol=1e-12)
    return {"result": "actual_arm64_synthetic_acquisition_geometry_verified_not_live_mapping",
            "manifestSha256": manifest_sha256, "nativeReportSha256": sha256(output / "report.json"),
            "nativeScenarios": len(target.SCENARIOS), "geometryArchivesReconstructed": len(scans),
            "maximumAllowedCoordinateErrorRaw": 1e-12,
            "verifierSourcesSha256": {name: sha256(target.ROOT / name) for name in (
                "tools/check_observation_target_geometry.py", "robot-slam/lingxi_slam/observation_archive.py")},
            "boundaries": dict.fromkeys(target.BOUNDARIES, False)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--report-name", default="geometry-report.json")
    args = parser.parse_args()
    if re.fullmatch(r"[a-z][a-z0-9-]{0,63}\.json", args.report_name) is None:
        raise ValueError("new_geometry_report_filename_required")
    report = check(args.source.absolute(), args.output.absolute(), args.manifest_sha256)
    with (args.output / args.report_name).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
