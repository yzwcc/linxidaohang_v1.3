"""Synthetic standard-message fixtures; no ROS runtime or robot is needed."""
import ast
import importlib.util
import math
from pathlib import Path
import struct
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).parents[1] / "slam_preflight.py"
spec = importlib.util.spec_from_file_location("slam_preflight", SOURCE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def header(stamp=100, frame="lidar"):
    return NS(stamp=NS(sec=int(stamp), nanosec=int((stamp - int(stamp)) * 1e9)), frame_id=frame)


def cloud(big_endian=False, height=1, padding=0):
    endian = ">" if big_endian else "<"
    data = b""
    for row in range(height):
        for column in range(2):
            data += struct.pack(endian + "ffff", 1, 2, 3, (row * 2 + column) / 100)
        data += b"\xff" * padding
    return NS(
        width=2, height=height, point_step=16, row_step=32 + padding,
        is_bigendian=big_endian, data=data, header=header(),
        fields=[NS(name=name, offset=index * 4, datatype=7, count=1)
                for index, name in enumerate(("x", "y", "z", "time"))],
    )


class CloudSamples(unittest.TestCase):
    def test_little_endian_times_are_not_assigned_guessed_units(self):
        sample = probe.sample_cloud(cloud())
        self.assertEqual(2, sample["finiteXyz"])
        self.assertAlmostEqual(0.01, sample["pointTimes"]["time"]["spanRawUnits"])
        self.assertEqual("unverified", sample["pointTimes"]["time"]["units"])

    def test_big_endian(self):
        sample = probe.sample_cloud(cloud(big_endian=True))
        self.assertEqual(2, sample["finiteXyz"])
        self.assertAlmostEqual(0.01, sample["pointTimes"]["time"]["spanRawUnits"])

    def test_organized_rows_with_padding(self):
        sample = probe.sample_cloud(cloud(height=2, padding=8))
        self.assertEqual(4, sample["sampled"])
        self.assertAlmostEqual(0.03, sample["pointTimes"]["time"]["spanRawUnits"])

    def test_timestamp_field_float64(self):
        message = cloud()
        message.point_step = 24
        message.row_step = 48
        message.fields[-1] = NS(name="timestamp", offset=16, datatype=8, count=1)
        message.data = b"".join(struct.pack("<ffffd", 1, 2, 3, 0, t) for t in (10, 10.1))
        self.assertAlmostEqual(0.1, probe.sample_cloud(message)["pointTimes"]["timestamp"]["spanRawUnits"])

    def test_integer_nanosecond_candidate_remains_unverified(self):
        message = cloud()
        message.fields[-1] = NS(name="t", offset=12, datatype=6, count=1)
        message.data = b"".join(struct.pack("<fffI", 1, 2, 3, t) for t in (0, 10000000))
        sample = probe.sample_cloud(message)
        self.assertEqual(10000000, sample["pointTimes"]["t"]["spanRawUnits"])
        self.assertEqual("unverified", sample["pointTimes"]["t"]["units"])

    def test_nonfinite_coordinates_and_times_are_excluded(self):
        message = cloud()
        message.data = struct.pack("<ffffffff", math.nan, 2, 3, math.inf, 1, 2, 3, 0)
        sample = probe.sample_cloud(message)
        self.assertEqual(1, sample["finiteXyz"])
        self.assertEqual(1, sample["pointTimes"]["time"]["finiteSamples"])

    def test_missing_time_is_reported_without_fabricating_it(self):
        message = cloud()
        message.fields = message.fields[:3]
        self.assertEqual({}, probe.sample_cloud(message)["pointTimes"])

    def test_single_point(self):
        message = cloud()
        message.width = 1
        message.row_step = 16
        message.data = message.data[:16]
        self.assertEqual(1, probe.sample_cloud(message)["sampled"])

    def test_invalid_clouds(self):
        for mutate, category in (
            (lambda m: setattr(m, "width", 0), "invalid_pointcloud_dimensions"),
            (lambda m: setattr(m, "data", b""), "truncated_pointcloud"),
            (lambda m: setattr(m.fields[0], "offset", 16), "pointcloud_field_out_of_bounds"),
            (lambda m: setattr(m.fields[1], "name", "x"), "duplicate_pointcloud_field"),
            (lambda m: setattr(m.fields[0], "datatype", 100), "unsupported_required_field"),
            (lambda m: setattr(m.fields[0], "count", 3), "unsupported_required_field"),
            (lambda m: setattr(m.fields[0], "name", "other"), "missing_xyz"),
        ):
            with self.subTest(category=category):
                message = cloud()
                mutate(message)
                with self.assertRaisesRegex(ValueError, category):
                    probe.sample_cloud(message)

    def test_bounded_sampling(self):
        sample = probe.sample_cloud(cloud(height=300))
        self.assertEqual(256, sample["sampled"])
        with self.assertRaises(ValueError):
            probe.sample_cloud(cloud(), budget=100000)


class Metadata(unittest.TestCase):
    def test_empty_evidence_never_reports_navigation_ready(self):
        report = probe.Audit().report()
        self.assertEqual(0, report["streams"]["cloud"]["messages"])
        for key in ("mappingValidated", "relocalizationValidated", "navigationValidated", "slamExecuted"):
            self.assertFalse(report["validation"][key])

    def test_sensor_evidence_still_does_not_validate_mapping(self):
        audit = probe.Audit()
        audit.cloud(cloud())
        report = audit.report()
        self.assertEqual(1, report["cloud"]["framesWithVaryingSampledPointTime"]["time"])
        self.assertFalse(report["validation"]["mappingValidated"])
        self.assertEqual("unverified", report["validation"]["calibrationAndTimeUnits"])

    def test_malformed_cloud_is_counted(self):
        audit = probe.Audit()
        message = cloud()
        message.data = b""
        audit.cloud(message)
        self.assertEqual({"truncated_pointcloud": 1}, audit.report()["cloud"]["errors"])
        self.assertIsNone(audit.report()["cloud"]["lastLayout"])

    def test_stamp_discontinuity_and_zero_stamp_are_visible(self):
        stream = probe.Stream()
        for index, stamp in enumerate((100, 101, 101, 99, 0)):
            stream.observe(header(stamp), arrival=index, wall_time=stamp + 0.1)
        report = stream.report()
        self.assertEqual(2, report["nonIncreasingHeaderStamps"])
        self.assertEqual(1, report["invalidHeaderStamps"])
        self.assertEqual(1, report["arrivalHz"])
        self.assertNotIn("firstStamp", report)

    def test_unavailable_imu_orientation_is_not_used(self):
        audit = probe.Audit()
        vector = NS(x=0, y=0, z=9.81)
        audit.imu(NS(header=header(frame="imu"), linear_acceleration=vector,
                     angular_velocity=NS(x=0, y=0, z=0),
                     orientation=NS(x=0, y=0, z=0, w=1),
                     orientation_covariance=[-1] + [0] * 8))
        report = audit.report()["imu"]
        self.assertEqual(1, report["orientationUnavailableMessages"])
        self.assertEqual(0, report["orientationNorm"]["count"])
        self.assertAlmostEqual(9.81, report["accelerationNorm"]["min"])

    def test_frame_paths_support_reverse_and_missing_edges(self):
        edges = {("base", "lidar"), ("base", "imu")}
        self.assertEqual(["lidar", "base", "imu"], probe.frame_path(edges, "lidar", "imu"))
        self.assertIsNone(probe.frame_path(edges, "lidar", "map"))
        self.assertEqual(["lidar"], probe.frame_path(edges, "lidar", "lidar"))

    def test_invalid_tf_is_not_a_calibration_path(self):
        audit = probe.Audit()
        transform = NS(header=header(frame="base"), child_frame_id="lidar",
                       transform=NS(translation=NS(x=0, y=0, z=0),
                                    rotation=NS(x=0, y=0, z=0, w=0)))
        audit.transforms(NS(transforms=[transform]), static=True)
        self.assertEqual(1, audit.report()["tf"]["invalidTransforms"])
        self.assertEqual([], audit.report()["tf"]["edges"])

    def test_report_contains_no_coordinates_or_raw_sensor_payload(self):
        audit = probe.Audit()
        audit.cloud(cloud())
        report = audit.report()
        self.assertNotIn("data", report["cloud"])
        self.assertNotIn("points", report["cloud"])
        self.assertNotIn("pose", report["odometry"])

    def test_tool_has_no_publishers_service_clients_or_process_execution(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        forbidden = {"create_publisher", "create_client", "call", "call_async",
                     "system", "popen", "Popen", "run", "exec", "eval", "open"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                self.assertNotIn(name, forbidden)


class TimeInterpretations(unittest.TestCase):
    def test_absolute_nanoseconds_match_the_header_without_exposing_epoch(self):
        epoch = 1700000000
        values = [epoch * 10**9, epoch * 10**9 + 95000000]
        result = probe.time_hypotheses(values, epoch, 2)
        self.assertEqual({"absolute_nanoseconds"}, set(result))
        self.assertAlmostEqual(0.095, result["absolute_nanoseconds"]["spanSeconds"])
        self.assertAlmostEqual(0, result["absolute_nanoseconds"]["firstMinusHeaderSeconds"])
        self.assertTrue(all(abs(value) <= 1 for value in result["absolute_nanoseconds"].values()))

    def test_absolute_seconds_and_relative_seconds_are_distinct(self):
        self.assertEqual(
            {"absolute_seconds"}, set(probe.time_hypotheses([100, 100.1], 100, 2))
        )
        self.assertEqual(
            {"relative_seconds"}, set(probe.time_hypotheses([0, 0.1], 100, 2))
        )

    def test_relative_nanoseconds(self):
        result = probe.time_hypotheses([0, 95000000], 1700000000, 2)
        self.assertEqual({"relative_nanoseconds"}, set(result))

    def test_missing_nonvarying_or_incomplete_point_times_do_not_match(self):
        for values, count in (([], 2), ([0, 0], 2), ([0, 0.1], 3)):
            self.assertEqual({}, probe.time_hypotheses(values, 100, count))

    def test_invalid_header_does_not_offer_time_configuration(self):
        for stamp in (0, -1, math.nan, math.inf):
            self.assertEqual({}, probe.time_hypotheses([0, 0.1], stamp, 2))

    def test_frame_end_reference_is_visible_but_not_rewritten(self):
        result = probe.time_hypotheses([99.9, 100], 100, 2)
        self.assertAlmostEqual(-0.1, result["absolute_seconds"]["firstMinusHeaderSeconds"])
        self.assertAlmostEqual(0, result["absolute_seconds"]["lastMinusHeaderSeconds"])

    def test_ambiguous_units_are_reported_not_chosen(self):
        result = probe.time_hypotheses([0, 1], 100, 2)
        self.assertIn("relative_seconds", result)
        self.assertIn("relative_milliseconds", result)

    def test_source_field_alias_is_supported(self):
        message = cloud()
        message.fields[-1].name = "time_stamp"
        result = probe.sample_cloud(message)
        self.assertIn("time_stamp", result["pointTimes"])

    def test_aggregated_evidence_keeps_calibration_unverified(self):
        audit = probe.Audit()
        audit.cloud(cloud())
        result = audit.report()
        self.assertIn("relative_seconds", result["cloud"]["pointTimeInterpretationEvidence"]["time"])
        self.assertEqual("unverified", result["cloud"]["pointTimeUnits"])
        self.assertFalse(result["validation"]["mappingValidated"])


if __name__ == "__main__":
    unittest.main()
