from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from probe_reference_target import (BASE_FRAMES, CLOUD, IMU, IMU_FRAME, MC_STATE, NS_PER_S,
                                    SERVICE_TYPES, TOPIC_TYPES, Stream, build_report, candidate_profile,
                                    imu_valid, mc_state_valid, read_factory_lidar, sensor_qos_settings)
from check_reference_bag import FIELDS
from prepare_reference_profile import build_profile
from reference_calibration import check_target_mounting_document


def stamped(ns, frame_id=""):
    sec, nanosec = divmod(ns, NS_PER_S)
    return NS(header=NS(stamp=NS(sec=sec, nanosec=nanosec), frame_id=frame_id))


class ReferenceTargetProbeTests(unittest.TestCase):
    def setUp(self):
        self.now = 791 * NS_PER_S
        self.streams = {topic: Stream() for topic in TOPIC_TYPES}
        for topic, step_ms, count in ((CLOUD, 100, 6), (IMU, 10, 50), (MC_STATE, 40, 12)):
            for index in range(count, 0, -1):
                stamp = self.now - index * step_ms * 1_000_000
                self.streams[topic].observe(stamped(stamp), stamp + 5_000_000)
        cloud = stamped(self.now - 300_000_000, "lidar_chest_front")
        cloud.fields = [NS(name=name, offset=offset, datatype=kind, count=1) for name, (offset, kind) in FIELDS.items()]
        cloud.is_bigendian, cloud.point_step, cloud.width, cloud.height, cloud.row_step = False, 32, 2, 1, 64
        cloud.data = (struct.pack("<fff4xfH2xd", 1., 2., 3., 2., 1, 0.)
                      + struct.pack("<fff4xfH2xd", 1., 2., 4., 2., 2, 95_000_000.))
        self.clouds = [cloud] * 3
        self.topics = {name: [kind] for name, kind in TOPIC_TYPES.items()}
        self.services = {name: [kind] for name, kind in SERVICE_TYPES.items()}
        self.calibration = "matched"

    def report(self, lookup=None, architecture="aarch64"):
        return build_report(architecture=architecture, distro="humble", streams=self.streams, clouds=self.clouds,
                            topics=self.topics, services=self.services, calibration=self.calibration,
                            lookup=lookup or (lambda *_: True), now_ns=self.now)

    def test_good_samples_check_matched_timestamps_and_never_claim_motion(self):
        calls = []

        def lookup(base, frame, stamp):
            calls.append((base, frame, stamp))
            return True

        report = self.report(lookup)
        self.assertTrue(report["ok"])
        self.assertFalse(report["motionValidated"])
        self.assertEqual(report["sameStampBodyAndFeetTfSamples"], 6)
        self.assertEqual(report["cloud"]["maxPointOffsetMs"], 95)
        self.assertEqual(calls, [("base_link", frame, stamp) for stamp in self.streams[CLOUD].recent for frame in BASE_FRAMES])

    def test_rejects_stale_wrong_clock_and_out_of_order_samples(self):
        stream = self.streams[MC_STATE]
        stream.observe(stamped(self.now - 3 * NS_PER_S), self.now)
        report = self.report()
        self.assertFalse(report["ok"])
        self.assertFalse(report["streams"][MC_STATE]["fresh"])
        self.assertEqual(report["streams"][MC_STATE]["clockSkew"], 1)
        self.assertEqual(report["streams"][MC_STATE]["outOfOrder"], 1)

    def test_rejects_fifty_millisecond_imu_contract_violation(self):
        stream = self.streams[IMU]
        stream.observe(stamped(self.now + 45_000_000), self.now)
        report = self.report()
        self.assertFalse(report["ok"])
        self.assertEqual(report["streams"][IMU]["maxHeaderGapMs"], 55)

    def test_rejects_bad_layout_calibration_platform_and_tf(self):
        self.assertFalse(self.report(architecture="x86_64")["ok"])
        self.assertFalse(self.report(lambda base, frame, stamp: None if frame == BASE_FRAMES[-1] else True)["ok"])
        self.calibration = "mismatch"
        self.assertFalse(self.report()["ok"])
        self.calibration = "matched"
        self.clouds[0].point_step = 24
        self.assertFalse(self.report()["cloud"]["ok"])

    def test_requires_exact_graph_types(self):
        self.services[next(iter(self.services))] = ["wrong/srv/Type"]
        self.topics[MC_STATE].append("wrong/msg/AmbiguousType")
        report = self.report()
        self.assertFalse(report["ok"])
        self.assertFalse(report["topics"][MC_STATE])
        self.assertIn(False, report["aimdkServiceTypes"].values())

    def test_imu_requires_finite_vectors_and_expected_frame(self):
        message = stamped(self.now, IMU_FRAME)
        message.linear_acceleration = NS(x=0., y=0., z=9.8)
        message.angular_velocity = NS(x=0., y=0., z=0.)
        self.assertTrue(imu_valid(message))
        message.angular_velocity.z = float("nan")
        self.assertFalse(imu_valid(message))
        message.angular_velocity.z = 0.
        message.header.frame_id = "other"
        self.assertFalse(imu_valid(message))

    def test_aimdk_state_must_have_fields_consumed_by_reference_gateway(self):
        message = NS(input_source=NS(name=""), fsm_state=NS(current_state=2))
        self.assertTrue(mc_state_valid(message))
        del message.input_source
        self.assertFalse(mc_state_valid(message))

    def test_candidate_inverts_live_transform_not_factory_matrix(self):
        import numpy as np

        template = {"/**": {"ros__parameters": {"mapping": {"extrinsic_est_en": True,
                                                            "filter_size_surf": 0.3}}}}
        edge = np.eye(4)
        edge[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        edge[:3, 3] = [0.10, 0.23, -0.14]
        candidate = candidate_profile(template, edge)
        self.assertEqual("matched", check_target_mounting_document(candidate, lambda *_: edge))
        self.assertEqual("mismatch", check_target_mounting_document(candidate, lambda *_: np.eye(4)))
        self.assertEqual(0.3, candidate["/**"]["ros__parameters"]["mapping"]["filter_size_surf"])
        self.assertFalse(candidate["/**"]["ros__parameters"]["runtime_pos_log_enable"])
        edge[0, 0] = float("nan")
        with self.assertRaisesRegex(ValueError, "invalid_static"):
            candidate_profile(template, edge)

    def test_factory_requires_finite_explicit_lidar_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "factory.yml"
            file.write_text("chest_lidar:\n  R_baselink_sensor: [1, 0, 0, 0, 1, 0, 0, 0, 1]\n"
                            "  t_baselink_sensor: [.1, .2, .3]\n")
            self.assertEqual((4, 4), read_factory_lidar(file).shape)
            for text in (file.read_text().replace(".3", ".nan"), file.read_text().replace(".3", "true"),
                         "chest_lidar: ["):
                file.write_text(text)
                with self.assertRaises(ValueError):
                    read_factory_lidar(file)

    def test_prepared_app_profile_never_carries_template_motion_or_data_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config/app.yaml").write_text("motion_enabled: true\nvisualization: {enabled: true}\n"
                "color_mapping: {enabled: true}\nmapping: {enabled: false, loop_closure_enabled: true, grid_cache_enabled: true}\n"
                "allowed_recording_roots: [/home/other/recordings]\n")
            for file in ("config/localizer.yaml", "fastlio.yaml", "lib/fast_lio/fastlio_mapping",
                         "lib/robot_nav_recorder/mapping_recorder"):
                path = root / file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch(mode=0o755)
            result = build_profile(root, root, root / "fastlio.yaml", root / "data")
            self.assertIs(result["motion_enabled"], False)
            self.assertIs(result["visualization"]["enabled"], False)
            self.assertIs(result["color_mapping"]["enabled"], False)
            self.assertIs(result["mapping"]["loop_closure_enabled"], False)
            self.assertIs(result["mapping"]["grid_cache_enabled"], False)
            self.assertEqual([str(root / "data/recordings")], result["allowed_recording_roots"])
            self.assertEqual([str(root / "fastlio.yaml")], result["mapping"]["calibration_files"])

    def test_probe_uses_profile_qos_and_requires_expected_topics(self):
        common = {"lid_topic": CLOUD, "imu_topic": IMU, "sensor_reliable": True, "imu_queue_depth": 500}
        profile = {"/**": {"ros__parameters": {"common": common}}}
        self.assertEqual((True, 20, 500), sensor_qos_settings(profile))
        common["imu_queue_depth"] = 4001
        with self.assertRaises(ValueError):
            sensor_qos_settings(profile)
        common.update(imu_queue_depth=500, imu_topic="/some_other_imu")
        with self.assertRaises(ValueError):
            sensor_qos_settings(profile)
