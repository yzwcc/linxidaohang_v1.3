"""Recheck actual ARM64 synthetic point contracts; export no paths, logs or data."""
import argparse
import json
import os
from pathlib import Path

import glim_point_target_probe as probe
import run_glim_point_target as coordinator
from verify_slam_capture import sha256


def evidence(trial, bundle, manifest_sha256):
    manifest = probe.verify_bundle(bundle, manifest_sha256)
    collection, report = (json.loads((trial / name).read_text(encoding="utf-8"))
                          for name in ("collection-report.json", "report.json"))
    if (type(collection["schemaVersion"]) is not int or collection["schemaVersion"] != 1
            or collection["result"] != coordinator.RESULT
            or collection["coordinatorSha256"] != sha256(coordinator.__file__)
            or collection["bundleManifestSha256"] != manifest_sha256
            or not probe.digest(collection["coreManifestSha256"])
            or any(collection[name] is not True for name in (
                "operatorConfirmedForThisExecution", "hostIdentityVerified", "connectionClosed"))
            or type(collection["exitCode"]) is not int or collection["exitCode"] != 0
            or set(collection["filesSha256"]) != set(coordinator.FILES) | {coordinator.PRIVATE_COMMAND_LOG}
            or any(not probe.digest(digest) or (trial / name).is_symlink()
                   or sha256(trial / name) != digest
                   for name, digest in collection["filesSha256"].items())):
        raise ValueError("verified_closed_identity_checked_point_collection_required")
    expected = {name: {
        "sha256": entry["sha256"], "bytes": entry["bytes"],
        "mode": 0o700 if entry["executable"] else 0o600,
    } for name, entry in manifest["files"].items()}
    expected["point-manifest.json"] = {
        "sha256": manifest_sha256, "bytes": (bundle / "point-manifest.json").stat().st_size, "mode": 0o600}
    preflight = collection["readOnlyPreflight"]
    if (collection["uploadedFiles"] != expected
            or type(preflight["uid"]) is not int or preflight["uid"] <= 0
            or preflight["coreManifestSha256"] != collection["coreManifestSha256"]
            or preflight["machine"] != report["target"]["machine"]
            or preflight["python"] != report["target"]["python"]
            or preflight["libc"] != report["target"]["libc"]
            or any(type(preflight[name]) is not bool for name in ("productNodeExists", "systemNodeExists"))):
        raise ValueError("identical_uploaded_bundle_and_target_preflight_required")
    coordinator.validate_report(report, manifest, manifest_sha256, collection["coreManifestSha256"])
    return {
        "schemaVersion": 1, "date": "2026-10-29", "result": probe.RESULT,
        "inputClassification": "isolated_synthetic_contracts",
        "privateCollectionReportSha256": sha256(trial / "collection-report.json"),
        "privateProbeReportSha256": collection["filesSha256"]["report.json"],
        "privateCommandOutputSha256": collection["filesSha256"][coordinator.PRIVATE_COMMAND_LOG],
        "privateContractLogsSha256": {
            name: collection["filesSha256"]["private-contract-" + name + ".log"] for name in probe.SCENARIOS},
        "coordinatorSha256": collection["coordinatorSha256"],
        "bundleManifestSha256": manifest_sha256,
        "coreManifestSha256": collection["coreManifestSha256"],
        "sourceCommits": manifest["sourceCommits"],
        "host": {name: report["target"][name] for name in ("machine", "python", "libc")},
        "actualLoadedLibrariesSha256": report["actualLoadedLibrariesSha256"],
        "emptyAbi": report["emptyAbi"],
        "nativeContractExecutionsOnTarget": report["nativeContractExecutionsOnTarget"],
        "stages": [{key: stage[key] for key in (
            "scenario", "exitCode", "forcedTermination", "processAlive", "timedOut")}
            for stage in report["stages"]],
        "observations": {
            "identityCheckedRobotContacted": True, "uploadedFilesIndividuallyVerified": len(expected),
            "connectionClosed": True, "inputsReverifiedByTarget": True,
            "productNodePresentAtExpectedPath": preflight["productNodeExists"],
            "systemNodePresentAtExpectedPath": preflight["systemNodeExists"],
            "nodeRuntimeExecutedOrValidated": False,
        },
        "boundaries": {name: False for name in probe.BOUNDARIES},
        "acceptance": dict.fromkeys((
            "recordedArm64PointChannelValidated", "liveArm64PointChannelValidated",
            "webPointRenderingValidated", "androidPointRenderingValidated",
            "pointDisplayLatencyValidated", "calibrationValidated", "mapPersistenceValidated",
            "loopClosureValidated", "relocalizationValidated", "baseAlignmentValidated",
            "independentNavigationValidated", "productAccepted",
        ), False),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("trial", "bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = evidence(args.trial.resolve(strict=True), args.bundle.resolve(strict=True), args.manifest_sha256)
        if args.output.exists():
            raise ValueError("existing_point_evidence_is_preserved")
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"result": result["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
