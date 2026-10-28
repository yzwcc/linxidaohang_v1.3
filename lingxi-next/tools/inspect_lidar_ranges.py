"""Read every recorded cloud; export distance aggregates, never a map or calibration."""
import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import platform

import numpy as np

from diagnose_slam_sensors import stamp_ns
from run_kiss_offline_trial import cloud_arrays
from slam_bag_audit import CLOUD
from verify_slam_capture import sha256, verify


EDGES = (0, 1, 2, 5, 10, 20, 30, 50, 100)
BOUNDARIES = (
    "robotContacted", "containsRosNode", "containsMotionAdapter",
    "coordinatesRescaled", "pointsDownsampled", "rangeFilterApplied",
    "physicalUnitsCalibrated", "ratedSensorRangeEstablished",
    "mapGeometryValidated", "navigationValidated", "deploymentApproved",
    "rawCoordinatesPublished",
)


class Distances:
    def __init__(self):
        self.bins = np.zeros(len(EDGES), dtype=np.int64)
        self.frames = []
        self.zero_count = 0

    def add(self, values):
        values = np.asarray(values, dtype=np.float64)
        if (values.ndim != 1 or not len(values) or not np.isfinite(values).all()
                or np.any(values < 0)):
            raise ValueError("nonempty_finite_nonnegative_distances_required")
        self.bins += np.histogram(values, bins=[*EDGES, np.inf])[0]
        self.zero_count += int(np.count_nonzero(values == 0))
        self.frames.append(np.percentile(values, [0, 50, 95, 100]))

    def summary(self):
        if not self.frames:
            raise ValueError("recorded_distances_required")
        frames = np.asarray(self.frames)
        return {
            "finitePointCount": int(self.bins.sum()),
            "zeroDistancePointCount": self.zero_count,
            "distanceUnits": "raw_point_coordinate_units_not_validated_metres",
            "histogramLowerInclusiveUpperExclusive": [
                {"lower": lower, "upper": EDGES[index + 1] if index + 1 < len(EDGES) else None,
                 "count": int(count)}
                for index, (lower, count) in enumerate(zip(EDGES, self.bins))
            ],
            "minimum": float(frames[:, 0].min()),
            "maximum": float(frames[:, 3].max()),
            "perScanStatisticsMinP50P95Max": {
                name: np.percentile(frames[:, index], [0, 50, 95, 100]).tolist()
                for index, name in enumerate(("minimum", "median", "p95", "maximum"))
            },
            "perScanPercentilesAreNotPooledPointPercentiles": True,
        }


def summarize(messages, expected_count):
    radial, horizontal = Distances(), Distances()
    count = source_count = dropped_count = 0
    first = previous = None
    for message in messages:
        stamp = stamp_ns(message)
        if (message.header.frame_id != "lidar_chest_front"
                or (previous is not None and stamp <= previous)):
            raise ValueError("ordered_expected_recorded_lidar_frame_required")
        if first is None:
            first = stamp
        previous = stamp
        points, _, dropped = cloud_arrays(message)
        source_count += int(message.width * message.height)
        dropped_count += dropped
        radial.add(np.linalg.norm(points, axis=1))
        horizontal.add(np.hypot(points[:, 0], points[:, 1]))
        count += 1
    if (count != expected_count or count < 2
            or int(radial.bins.sum()) + dropped_count != source_count):
        raise ValueError("all_declared_clouds_and_points_required")
    return {
        "recordedScans": count, "sourcePointCount": source_count,
        "nonfiniteCoordinatePointCount": dropped_count,
        "recordedHeaderSpanSeconds": (previous - first) / 1e9,
        "radial3d": radial.summary(), "horizontalXY": horizontal.summary(),
        "noImuOrScanCoverageGateApplied": True,
        "noRangeOrZeroReturnFilterApplied": True,
    }


def recorded_clouds(bag):
    from rosbags.highlevel import AnyReader
    from rosbags.typesys import Stores, get_typestore

    with AnyReader([bag], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as reader:
        connections = [item for item in reader.connections if item.topic == CLOUD]
        if len(connections) != 1 or connections[0].msgtype != "sensor_msgs/msg/PointCloud2":
            raise ValueError("one_expected_recorded_cloud_connection_required")
        for connection, _, payload in reader.messages(connections=connections):
            yield reader.deserialize(payload, connection.msgtype)


def run(manifest_path, data_root, output):
    if (platform.system() != "Linux" or os.geteuid() == 0
            or os.sys.prefix == os.sys.base_prefix):
        raise ValueError("ordinary_user_isolated_linux_environment_required")
    if metadata.version("rosbags") != "0.11.9":
        raise ValueError("reviewed_rosbags_version_required")
    data_root, output = Path(data_root).resolve(strict=True), Path(output).resolve()
    if output.exists():
        raise ValueError("existing_range_evidence_is_preserved")
    if output == data_root or data_root in output.parents:
        raise ValueError("output_must_not_be_inside_recordings")
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verify(manifest, data_root)
    report = {
        "schemaVersion": 1,
        "purpose": "all_recorded_cloud_distance_aggregates_not_sensor_range_or_map_acceptance",
        "runnerSourceSha256": sha256(__file__),
        "decoderSourceSha256": sha256(Path(__file__).with_name("run_kiss_offline_trial.py")),
        "captureManifestSha256": sha256(manifest_path),
        "rosbagsVersion": metadata.version("rosbags"),
        "boundaries": {name: False for name in BOUNDARIES},
        "datasets": {
            name: summarize(recorded_clouds(data_root / expected["directoryName"]),
                            expected["topicCounts"]["cloud"])
            for name, expected in manifest["datasets"].items()
        },
    }
    verify(manifest, data_root)
    report["allOriginalCapturesUnchangedBeforeAndAfter"] = True
    previous_umask = os.umask(0o077)
    try:
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    finally:
        os.umask(previous_umask)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "data-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    report = run(args.manifest, args.data_root, args.output)
    print(json.dumps({
        "result": "all_recorded_cloud_distance_aggregates_completed_not_calibration",
        "reportSha256": sha256(args.output),
        "datasets": {
            name: {"recordedScans": value["recordedScans"],
                   "sourcePointCount": value["sourcePointCount"],
                   "radial3d": value["radial3d"], "horizontalXY": value["horizontalXY"]}
            for name, value in report["datasets"].items()
        },
    }, allow_nan=False))


if __name__ == "__main__":
    main()
