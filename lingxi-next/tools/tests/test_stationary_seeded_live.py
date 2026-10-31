import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_stationary_seeded_live as check

try:
    import numpy as np
    from lingxi_slam.seeded_localization import rigid_pose
except ImportError:
    np = None


@unittest.skipIf(np is None, "isolated numerical runtime required")
class StationarySeededTests(unittest.TestCase):
    def setUp(self):
        self.binding = dict(session="a" * 32, map_id="slam-" + "b" * 32,
            input_mode="live", receipt_sha256="c" * 64, points_sha256="d" * 64, profile_sha256="e" * 64)
        self.receipt = dict(inputMode="live", counts=dict(savedFrames=100))
        self.refs = dict(source="glim-final-lidar-trajectory", sourceFrame="lidar_chest_front",
            coordinateUnits="native-raw", profileSha256="e" * 64, trajectorySha256="f" * 64,
            frames=100, last=dict(frameIndex=100, mapFromSensor=np.eye(4).tolist()))
        self.preview = dict(mapId=self.binding["map_id"], inputMode="live", receiptSha256="c" * 64,
            pointsSha256="d" * 64, seedReferences=self.refs)
        self.state = dict(binding=self.binding, generation=0, state="unseeded", mapFromSensor=None,
            localizationReady=False, navigationReady=False, referenceFrame=5)

    def test_seed_is_explicit_bound_last_lidar_pose(self):
        value = check.seed_request(self.preview, self.state, self.receipt, "f" * 64)
        self.assertEqual(5, value["referenceFrame"])
        self.assertEqual("lidar_chest_front", value["sourceFrame"])
        self.assertEqual(self.refs["last"]["mapFromSensor"], value["mapFromSensor"])

    def test_seed_rejects_map_receipt_points_and_mode_mismatch(self):
        for field, value in (("mapId", "slam-" + "9" * 32), ("receiptSha256", "9" * 64),
                             ("pointsSha256", "9" * 64), ("inputMode", "recorded")):
            changed = {**self.preview, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                check.seed_request(changed, self.state, self.receipt, "f" * 64)

    def test_seed_rejects_trajectory_profile_source_units_and_frame_mismatch(self):
        for field, value in (("trajectorySha256", "9" * 64), ("profileSha256", "9" * 64),
                             ("source", "odometry"), ("sourceFrame", "imu"),
                             ("coordinateUnits", "metres"), ("frames", 99), ("frames", True)):
            changed = copy.deepcopy(self.preview)
            changed["seedReferences"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                check.seed_request(changed, self.state, self.receipt, "f" * 64)

    def test_seed_rejects_missing_reference_and_nonrigid_pose(self):
        for frame in (None, 0, True):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                check.seed_request(self.preview, {**self.state, "referenceFrame": frame}, self.receipt, "f" * 64)
        changed = copy.deepcopy(self.preview)
        changed["seedReferences"]["last"]["mapFromSensor"][0][0] = 2
        with self.assertRaises(ValueError):
            check.seed_request(changed, self.state, self.receipt, "f" * 64)

    def tracking(self):
        return {**self.state, "state": "tracking", "generation": 1, "lastFrame": 10,
            "mapFromSensor": np.eye(4).tolist(), "requestPending": False,
            "workerReady": True, "workerClosed": False, "consecutiveMatches": 3}

    def test_tracking_rejects_loss_late_generation_frame_regression_and_readiness(self):
        state = self.tracking()
        np.testing.assert_array_equal(np.eye(4), check.tracking_sample(state, self.binding, 1, 9))
        for field, value in (("state", "lost"), ("generation", 0), ("lastFrame", 8),
                             ("requestPending", True), ("workerReady", False),
                             ("workerClosed", True), ("consecutiveMatches", 2),
                             ("localizationReady", True), ("navigationReady", True)):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                check.tracking_sample({**state, field: value}, self.binding, 1, 9)
        with self.assertRaises(RuntimeError):
            check.tracking_sample(state, {**self.binding, "session": "9" * 32}, 1, 9)

    def test_spread_uses_raw_units_without_physical_accuracy_claim(self):
        origin, next_pose = np.eye(4), np.eye(4)
        next_pose[0, 3] = .25
        next_pose[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        result = check.spread([origin, next_pose])
        self.assertAlmostEqual(.25, result["maxTranslationFromFirstRaw"])
        self.assertAlmostEqual(90, result["maxRotationFromFirstDegrees"])
        self.assertFalse(result["physicalAccuracyMeasured"])
        with self.assertRaises(ValueError):
            check.spread([origin])

    def test_requires_current_operator_confirmation_before_configuration_or_native_access(self):
        from types import SimpleNamespace
        with self.assertRaisesRegex(ValueError, "explicit_stationary"):
            check.check(SimpleNamespace(stationary_confirmed=False))


class PublishedStationaryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.value = json.loads((self.root / "docs/slam-stationary-seeded-live-evidence.json").read_bytes())

    def test_sanitized_evidence_has_no_motion_or_physical_accuracy_claim(self):
        value = self.value
        self.assertEqual(check.RESULT, value["result"])
        self.assertTrue(value["operatorConfirmedStationary"])
        self.assertEqual(1, value["seedRequests"])
        self.assertEqual("new_stationary_map_last_lidar_pose", value["seedSource"])
        self.assertTrue(all(item is False for item in value["boundaries"].values()))
        self.assertFalse(value["tracking"]["physicalAccuracyMeasured"])
        self.assertGreaterEqual(value["tracking"]["distinctFrames"], 20)
        self.assertGreaterEqual(value["tracking"]["observedSeconds"], 10)
        text = json.dumps(value)
        for marker in ("/home/", "192.168.", "10.201.", '"password"', '"pairingCode"',
                       '"token"', '"mapFromSensor"', '"pointCoordinates"'):
            self.assertNotIn(marker, text)

    def test_sources_match_tested_bytes(self):
        for name, expected in self.value["sourceSha256"].items():
            self.assertEqual(expected, hashlib.sha256((self.root / name).read_bytes()).hexdigest())

    def test_retained_reports_bind_the_published_observations(self):
        folder = self.root / ".local/stationary-seeded-arm64-20261031-a"
        if not folder.is_dir():
            self.skipTest("private commissioning data is intentionally not committed")
        for name, field in (("check-report.json", "privateReportSha256"),
                            ("collection-report.json", "collectionReportSha256"),
                            ("final-observation.json", "finalObservationSha256"),
                            ("private-tracking-samples.json", "trackingSamplesSha256")):
            self.assertEqual(self.value[field], hashlib.sha256((folder / name).read_bytes()).hexdigest())
        report = json.loads((folder / "check-report.json").read_bytes())
        self.assertEqual(check.RESULT, report["result"])
        for name in ("localizationCounts", "nativeCalls", "seedRequests", "seedSource", "pauseConfirmed",
                     "stopConfirmed", "staleSeedRejected", "stoppedQueries", "allProcessesCleaned"):
            self.assertEqual(self.value[name], report[name])
        for name, actual in report["tracking"].items():
            self.assertEqual(self.value["tracking"][name], actual)
        self.assertEqual(self.value["tracking"]["firstTrackingSecondsAfterSubmission"],
                         report["firstTrackingSecondsAfterSubmission"])
        samples = json.loads((folder / "private-tracking-samples.json").read_bytes())
        self.assertEqual(self.value["tracking"]["distinctFrames"], len(samples))
        previous_frame, previous_time = 0, -1.
        for sample in samples:
            state = sample["state"]
            self.assertEqual("tracking", state["state"])
            self.assertEqual(samples[0]["state"]["binding"], state["binding"])
            self.assertEqual(samples[0]["state"]["generation"], state["generation"])
            self.assertGreater(state["lastFrame"], previous_frame)
            self.assertGreater(sample["elapsedSeconds"], previous_time)
            self.assertFalse(state["localizationReady"])
            self.assertFalse(state["navigationReady"])
            previous_frame, previous_time = state["lastFrame"], sample["elapsedSeconds"]
        self.assertEqual(self.value["tracking"]["lastFrame"], previous_frame)
        self.assertEqual(self.value["tracking"]["observedSeconds"], previous_time)
        owner = json.loads((folder / "o/owner-report.json").read_bytes())
        check.clean_owner(owner)
        collection = json.loads((folder / "collection-report.json").read_bytes())
        self.assertEqual("collected", collection["result"])
        self.assertTrue(collection["connectionClosed"])
        self.assertEqual(self.value["finalObservation"], collection["finalObservation"])
        for name, expected in collection["filesSha256"].items():
            self.assertEqual(expected, hashlib.sha256((folder / name).read_bytes()).hexdigest())

    def test_retained_archive_contains_tested_checker_and_reference_sources(self):
        archive = self.root / "artifacts/lingxi-next-shared-slam-20261031-r9-arm64.tar.gz"
        if not archive.is_file():
            self.skipTest("generated archives are intentionally not committed")
        import live_bundle
        self.assertEqual(self.value["package"]["archiveBytes"], archive.stat().st_size)
        self.assertEqual(self.value["package"]["archiveSha256"], live_bundle.sha256(archive))
        manifest = live_bundle.verify_archive(archive, self.value["package"]["manifestSha256"], profile=live_bundle.SLAM)
        for name, expected in self.value["sourceSha256"].items():
            self.assertEqual(expected, manifest["sourceSha256"][name])


if __name__ == "__main__":
    unittest.main()
