"""Audit preserved callback timing/initialization; never run SLAM, ROS or a robot."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import json5
import numpy as np

from audit_slam_calibration import matrix
from diagnose_slam_sensors import stamp_ns, vector
from export_glim_matched_evidence import export as verify_matched
from run_kiss_offline_trial import cloud_arrays, valid_pose
from slam_callback_journal import records
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
REVIEWED_SOURCES = {
    "src/glim/odometry/robust_initial_state_estimation.cpp":
        "6cda405ccf62dce5a46f0a703a0b47ed11d4a3b8ce24e40a5cd22f01af021f35",
    "src/glim/odometry/robust_initial_state_estimation_estimate.cpp":
        "6f1d574f3da0d445b057301b4f7c8816203184c4841277d86dc1a40621f35304",
    "src/glim/common/imu_integration.cpp":
        "e99413ff27e18eb074be3d44d835ce762e182a216a18f8b3bdf58127a57799b8",
    "src/glim/odometry/odometry_estimation_imu.cpp":
        "d025eac2dddb5d67a00272435ee4504dd7dfe02b9704aa65173ed457ff4100c5",
}
BOUNDARIES = (
    "robotContacted", "nativeEngineExecuted", "rosNodeStarted", "motionCommandsSent",
    "configurationChanged", "biasCorrectionApplied", "timingOffsetFitted",
    "physicalStationarityConfirmed", "initializationRootCauseEstablished",
    "sensorSynchronizationValidated", "robotCalibrationValidated",
    "geometryValidated", "navigationValidated", "deploymentApproved", "rawDataPublished",
)


def ordered(values):
    values = np.asarray(values)
    if (values.dtype != np.dtype("int64") or values.ndim != 1 or len(values) < 2
            or np.any(values < 0) or np.any(values[1:] <= values[:-1])):
        raise ValueError("strictly_ordered_integer_nanoseconds_required")
    return values


def quantiles(values):
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("nonempty_finite_scalar_samples_required")
    return np.percentile(values, [0, 50, 95, 100]).tolist()


def clock_summary(headers, arrivals):
    headers, arrivals = ordered(headers), ordered(arrivals)
    if len(headers) != len(arrivals):
        raise ValueError("one_arrival_per_sensor_header_required")
    # Subtract integer origins before float conversion; absolute clock offset is unknown.
    residual = ((arrivals - arrivals[0]) - (headers - headers[0])).astype(np.float64) / 1e6
    return {
        "samples": len(headers),
        "headerGapMillisecondsMinP50P95Max": quantiles(np.diff(headers) / 1e6),
        "relativeArrivalMinusHeaderMillisecondsMinP50P95Max": quantiles(residual),
        "endRelativeArrivalMinusHeaderMilliseconds": float(residual[-1]),
        "absoluteTransportLatencyNotMeasured": True,
        "noClockOffsetOrDriftCorrectionApplied": True,
    }


def coverage(headers, offset_bounds_ns, imu_headers):
    headers, imu_headers = ordered(headers), ordered(imu_headers)
    bounds = np.asarray(offset_bounds_ns)
    if (bounds.dtype != np.dtype("int64") or bounds.shape != (len(headers), 2)
            or np.any(bounds[:, 0] < 0) or np.any(bounds[:, 1] <= bounds[:, 0])
            or np.any(bounds[:, 1] > 200_000_000)):
        raise ValueError("complete_integer_scan_offset_bounds_required")
    result = {}
    for hypothesis in ("header_is_scan_start", "header_is_scan_end"):
        start = headers if hypothesis.endswith("start") else headers - bounds[:, 1]
        first, last = start + bounds[:, 0], start + bounds[:, 1]
        result[hypothesis] = {
            "scans": len(headers),
            "bracketed": int(np.count_nonzero((first >= imu_headers[0]) & (last <= imu_headers[-1]))),
            "beginningBeforeImu": int(np.count_nonzero(first < imu_headers[0])),
            "endingAfterImu": int(np.count_nonzero(last > imu_headers[-1])),
        }
    result["pointOffsetSpanMillisecondsMinP50P95Max"] = quantiles((bounds[:, 1] - bounds[:, 0]) / 1e6)
    result["neitherHypothesisSelectedOrApproved"] = True
    return result


def initial_state(text):
    result = {}
    for name, prefix, count in (("T_world_imu", "se3", 7), ("v_world_imu", "vec", 3), ("imu_bias", "vec", 6)):
        matches = re.findall(r"\[odom\] \[info\] " + name + "=" + prefix + r"\(([^)\r\n]+)\)", text)
        if len(matches) != 1:
            raise ValueError("exactly_one_logged_initial_state_required")
        try:
            values = np.asarray([float(value) for value in matches[0].split(",")])
        except ValueError as error:
            raise ValueError("finite_complete_logged_initial_state_required") from error
        if values.shape != (count,) or not np.isfinite(values).all():
            raise ValueError("finite_complete_logged_initial_state_required")
        result[name] = values
    return result


def vectors_summary(acceleration, angular):
    acceleration, angular = (np.asarray(value, dtype=np.float64) for value in (acceleration, angular))
    if any(value.ndim != 2 or value.shape[1] != 3 or len(value) < 2 or not np.isfinite(value).all()
           for value in (acceleration, angular)) or acceleration.shape != angular.shape:
        raise ValueError("paired_finite_three_axis_imu_samples_required")
    return {
        "samples": len(angular),
        "accelerationNormRawUnitsMinP50P95Max": quantiles(np.linalg.norm(acceleration, axis=1)),
        "angularNormRawUnitsMinP50P95Max": quantiles(np.linalg.norm(angular, axis=1)),
        "accelerationCenteredVectorRmsRawUnits": float(np.sqrt(np.mean(np.sum(
            (acceleration - acceleration.mean(axis=0)) ** 2, axis=1)))),
        "angularCenteredVectorRmsRawUnits": float(np.sqrt(np.mean(np.sum(
            (angular - angular.mean(axis=0)) ** 2, axis=1)))),
        "angularMeanNormRawUnits": float(np.linalg.norm(angular.mean(axis=0))),
        "lowVariationDoesNotProvePhysicalStationarity": True,
    }


def initialization(arrays, sensors, config, state, lidar_imu):
    frames, pose_headers = ordered(arrays["frame_headers_ns"]), ordered(arrays["pose_headers_ns"])
    imu_headers = ordered(sensors["imu_headers"])
    clouds = ordered(sensors["cloud_headers"])
    first = int(np.searchsorted(frames, pose_headers[0]))
    if (first >= len(frames) or not np.array_equal(frames[first:], pose_headers)
            or len(arrays["poses"]) != len(pose_headers)):
        raise ValueError("initialization_prefix_then_contiguous_recorded_poses_required")
    first_cloud = int(np.searchsorted(clouds, pose_headers[0]))
    if first_cloud >= len(clouds) or clouds[first_cloud] != pose_headers[0]:
        raise ValueError("first_pose_requires_exact_recorded_cloud")
    algorithm = np.asarray(arrays["frame_algorithm_seconds"])
    expected = (frames - imu_headers[0]).astype(np.float64) / 1e9 + 1000.
    if (algorithm.shape != expected.shape or not np.isfinite(algorithm).all()
            or not np.allclose(algorithm, expected, atol=1e-12, rtol=0)):
        raise ValueError("unchanged_scan_start_algorithm_time_required")
    window = config.get("initialization_window_size")
    if (config.get("initialization_mode") != "ROBUST" or type(window) not in (int, float)
            or not np.isfinite(window) or window <= 0):
        raise ValueError("explicit_robust_initialization_window_required")
    elapsed = (frames - frames[0]).astype(np.float64) / 1e9
    crossing = np.flatnonzero(algorithm - algorithm[0] >= window)
    if not len(crossing):
        raise ValueError("recording_must_span_declared_initialization_window")
    scan_end = int(pose_headers[0]) + int(sensors["offsets_ns"][first_cloud, 1])
    end = int(np.searchsorted(imu_headers, scan_end, side="left")) + 1
    if end > len(imu_headers):
        raise ValueError("initial_scan_requires_actual_bracketing_imu")
    acc, gyro = sensors["acceleration"][:end], sensors["angular"][:end]
    transform = valid_pose(lidar_imu)
    actual_imu = valid_pose(arrays["poses"][0]) @ transform
    logged = matrix(state["T_world_imu"][:3], state["T_world_imu"][3:])
    difference = float(np.max(np.abs(actual_imu - logged)))
    if difference > 2e-6:
        raise ValueError("first_pose_must_match_six_decimal_logged_initial_state")
    return {
        "mode": "ROBUST", "configuredWindowSeconds": window,
        "noPoseScansBeforeFirstPose": first,
        "firstPoseSpanFromFirstProcessedScanSeconds": float(elapsed[first]),
        "previousScanSpanSeconds": float(elapsed[first - 1]) if first else None,
        "firstPoseAtFirstFrameReachingConfiguredWindow": int(crossing[0]) == first,
        "algorithmTimesMatchIntegerOriginScanStartFormula": True,
        "imuInsertedThroughFirstPoseScanIncludingBracketingSample": end,
        "initialImuHeaderSpanSeconds": int(imu_headers[end - 1] - imu_headers[0]) / 1e9,
        "initialImuPrefixReconstructedFromFrozenDrainRuleNotInstrumentedCount": True,
        "windowCrossingUsesActualAlgorithmDoubleSubtraction": True,
        "initialInput": vectors_summary(acc, gyro),
        "completeInput": vectors_summary(sensors["acceleration"], sensors["angular"]),
        "loggedInitialAngularBiasNormRawUnits": float(np.linalg.norm(state["imu_bias"][3:])),
        "initialMeanAngularMinusLoggedBiasNormRawUnits": float(np.linalg.norm(
            gyro.mean(axis=0) - state["imu_bias"][3:])),
        "loggedInitialAccelerationBiasNormRawUnits": float(np.linalg.norm(state["imu_bias"][:3])),
        "loggedInitialVelocityNormRawUnits": float(np.linalg.norm(state["v_world_imu"])),
        "firstPoseVersusRoundedLoggedImuMatrixMaximumDifference": difference,
        "loggedEstimatesNotIndependentCalibrationOrTruth": True,
        "stateVectorsAndAbsoluteTimesNotPublished": True,
    }


def read_sensors(journal, arrays):
    from rosbags.typesys import Stores, get_typestore
    store = get_typestore(Stores.ROS2_HUMBLE)
    columns = {key: [] for key in ("imu_headers", "imu_arrivals", "cloud_headers", "cloud_arrivals",
                                   "acceleration", "angular", "offsets_ns")}
    for entry in records(journal):
        kind = entry.kind
        message = store.deserialize_cdr(entry.payload, "sensor_msgs/msg/" + ("Imu" if kind == "imu" else "PointCloud2"))
        stamp = stamp_ns(message)
        index = len(columns[kind + "_headers"])
        expected = arrays[kind + "_headers_ns"]
        if index >= len(expected) or stamp != int(expected[index]):
            raise ValueError("every_decoded_callback_header_must_match_verified_observations")
        columns[kind + "_headers"].append(stamp)
        columns[kind + "_arrivals"].append(entry.elapsed_ns)
        if kind == "imu":
            columns["acceleration"].append(vector(message.linear_acceleration))
            columns["angular"].append(vector(message.angular_velocity))
        else:
            _, offsets, _ = cloud_arrays(message)
            bounds = np.array([offsets.min(), offsets.max()]) * 1e9
            rounded = np.rint(bounds)
            if not np.allclose(bounds, rounded, atol=1e-4, rtol=0):
                raise ValueError("integer_nanosecond_point_offsets_required")
            columns["offsets_ns"].append(rounded.astype(np.int64))
    for kind in ("cloud", "imu"):
        if len(columns[kind + "_headers"]) != len(arrays[kind + "_headers_ns"]):
            raise ValueError("all_verified_callbacks_must_be_decoded")
    return {key: np.asarray(value, dtype=np.float64 if key in ("acceleration", "angular") else np.int64)
            for key, value in columns.items()}


def read_configuration(bundle):
    directory = bundle / "config"
    config = json5.loads((directory / "config_odometry_cpu.json").read_text())["odometry_estimation"]
    extrinsic = json5.loads((directory / "config_sensors.json").read_text())["sensors"]["T_lidar_imu"]
    return config, matrix(extrinsic[:3], extrinsic[3:])


def audit(args):
    parent = verify_matched(args)
    for name, expected in REVIEWED_SOURCES.items():
        payload = (args.upstream / name).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError("reviewed_pinned_initialization_sources_required")
    directory = args.results / "live"
    with np.load(directory / "private-results.npz", allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files}
    sensors = read_sensors(directory / "private-callbacks.cdrjournal", arrays)
    if sha256(directory / "private-callbacks.cdrjournal") != parent["callbackJournalSha256"]:
        raise ValueError("callback_journal_must_remain_unchanged_during_audit")
    state = initial_state((directory / "private-native.log").read_text(encoding="utf-8"))
    config, extrinsic = read_configuration(args.bundle)
    return {
        "schemaVersion": 1, "purpose": "preserved_callback_initialization_and_time_audit",
        "result": "offline_inputs_audited_not_calibration_or_product_acceptance",
        "auditorSha256": sha256(__file__),
        "matchedEvidenceCanonicalSha256": hashlib.sha256(
            (json.dumps(parent, indent=2, allow_nan=False) + "\n").encode()).hexdigest(),
        "callbackJournalSha256": parent["callbackJournalSha256"],
        "sourceCommits": parent["sourceCommits"], "reviewedUpstreamSourceSha256": REVIEWED_SOURCES,
        "callbackClock": {kind: clock_summary(sensors[kind + "_headers"], sensors[kind + "_arrivals"])
                          for kind in ("cloud", "imu")},
        "scanCoverage": coverage(sensors["cloud_headers"], sensors["offsets_ns"], sensors["imu_headers"]),
        "initialization": initialization(arrays, sensors, config, state, extrinsic),
        "nativeWarnings": parent["nativeWarnings"]["live"],
        "noNewSensorCaptureOrNativeReplay": True,
        "boundaries": dict.fromkeys(BOUNDARIES, False),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "parent-bundle", "core-bundle", "results", "upstream", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to((ROOT / ".local").resolve()):
        parser.error("new_ignored_aggregate_output_required")
    result = audit(args)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"result": result["result"], "sha256": sha256(args.output)}))


if __name__ == "__main__":
    main()
