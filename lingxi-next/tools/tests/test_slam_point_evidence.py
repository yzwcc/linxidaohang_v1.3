"""Synthetic files only: validate the point-evidence exporter without native code."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import export_glim_point_evidence as exporter
from test_slam_point_trial import summary_fixture

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, "Numerical evidence contracts run in isolated Linux")
class EvidenceTests(unittest.TestCase):
    def setUp(self):
        from build_slam_cpu_trial import PINS
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.directory, self.build = root / "synthetic-trial", root / "synthetic-build"
        self.directory.mkdir()
        (self.build / "build").mkdir(parents=True)
        extension = self.build / "build" / exporter.trial.LIBRARY
        extension.write_bytes(b"synthetic not-loadable native extension")
        native_build = {
            "result": exporter.trial.BUILD_RESULT, "sourceCommits": PINS, "buildType": "Release",
            "coreBuildReportSha256": "a" * 64,
            "nativeSourceSha256": {name: exporter.sha256(ROOT / name) for name in exporter.trial.NATIVE_NAMES},
            "stages": [{"name": name, "exitCode": 0} for name in ("configure", "build", "contracts")],
            "librarySha256": exporter.sha256(extension),
        }
        (self.build / "build-report.json").write_text(json.dumps(native_build), encoding="utf-8")
        log = self.directory / "private-native.log"
        log.write_text("synthetic private log", encoding="utf-8")
        baseline = exporter.trial.replay.load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
        points = summary_fixture()
        for name, frame_id in (("first", 1), ("latest", 2)):
            path = self.directory / ("private-" + name + "-points.npz")
            np.savez_compressed(path, points=np.ones((2, 3)), world_lidar_pose=np.eye(4),
                                frame_id=frame_id, algorithm_stamp=1000. + frame_id * .1)
            points[name + "SampleSha256"] = exporter.sha256(path)
        self.report = {
            "result": exporter.trial.LOGICAL_RESULT,
            "inputIntegrity": {
                "sourceSha256": exporter.trial.replay.hashes(),
                "pointSourceSha256": exporter.trial.source_hashes(), "sourceCommits": PINS,
                "coreBuildReportSha256": "a" * 64,
                "pointExtensionBuildSha256": exporter.sha256(self.build / "build-report.json"),
                "pointExtensionLibrarySha256": exporter.sha256(extension),
                "journal": {"records": 15, "counts": {"imu": 10, "cloud": 5}, "bytes": 123},
                "journalSha256": "b" * 64,
            },
            "stream": {"state": "finished", "failure": None, "counts": {
                "receivedImu": 10, "receivedClouds": 5, "insertedImu": 9,
                "processedScans": 4, "initializationScans": 1, "estimatedPoses": 3,
                "skippedBeforeImu": 1, "skippedAfterImuAtEndOfInput": 0,
            }},
            "nativeCalls": {"create": 1, "close": 1, "imu": 9, "frame": 4},
            "points": points,
            "boundaries": dict.fromkeys(exporter.trial.replay.BOUNDARIES, False),
            "privateLogSha256": exporter.sha256(log), "sensorWorkerOrProcessSupervisorExecuted": False,
            "nativeOwner": "calling_main_thread_only",
            "arrivalClock": "original_journal_elapsed_not_monotonic_wall_or_paced",
            "actualLoadedLibrariesSha256": baseline["actualLoadedLibrariesSha256"],
            "actualLoadedExtensionSha256": {exporter.trial.LIBRARY: exporter.sha256(extension)},
            "totalWallSecondsNotRealtimePerformance": .5,
            "host": {"machine": "synthetic-x86_64", "numpy": np.__version__},
        }
        self.write_report()

    def write_report(self):
        (self.directory / "logical-report.json").write_text(json.dumps(self.report), encoding="utf-8")

    def evidence(self):
        return exporter.evidence(self.directory, self.build)

    def test_complete_evidence_has_explicit_nonperformance_boundaries(self):
        result = self.evidence()
        self.assertEqual(2, result["points"]["packets"])
        self.assertFalse(result["pacedReplayValidated"])
        self.assertFalse(result["webOrAndroidIntegrated"])
        self.assertFalse(result["targetArm64PointExtensionValidated"])

    def test_private_report_fields_never_pass_through(self):
        self.report["PRIVATE_MARKER"] = {"xyz": [10, 20, 30]}
        self.report["points"]["PRIVATE_MARKER"] = "private extra content"
        self.report["host"]["PRIVATE_MARKER"] = "/private/recording/path"
        self.write_report()
        self.assertNotIn("PRIVATE_MARKER", json.dumps(self.evidence()))

    def test_missing_callbacks_or_incomplete_native_close_rejected(self):
        original = deepcopy(self.report)
        for section, key in (("nativeCalls", "close"), ("nativeCalls", "frame"),
                             ("stream", "receivedImu"), ("stream", "estimatedPoses")):
            self.report = deepcopy(original)
            target = self.report["stream"]["counts"] if section == "stream" else self.report[section]
            target[key] -= 1
            self.write_report()
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                self.evidence()

    def test_paced_or_base_pose_claims_rejected(self):
        original = deepcopy(self.report)
        for section, key, value in (
            (None, "sensorWorkerOrProcessSupervisorExecuted", True),
            (None, "arrivalClock", "monotonic"),
            ("points", "poseFrame", "robot_base"),
            ("boundaries", "navigationValidated", True),
        ):
            self.report = deepcopy(original)
            (self.report if section is None else self.report[section])[key] = value
            self.write_report()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.evidence()

    def test_changed_sample_or_library_bytes_are_rejected(self):
        for path in (self.directory / "private-first-points.npz",
                     self.build / "build" / exporter.trial.LIBRARY):
            original = path.read_bytes()
            path.write_bytes(original + b"synthetic modified bytes")
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.evidence()
            path.write_bytes(original)

    def test_invalid_geometry_is_rejected_even_with_updated_sample_hash(self):
        path = self.directory / "private-first-points.npz"
        np.savez_compressed(path, points=np.full((2, 3), np.nan), world_lidar_pose=np.eye(4),
                            frame_id=1, algorithm_stamp=1000.1)
        self.report["points"]["firstSampleSha256"] = exporter.sha256(path)
        self.write_report()
        with self.assertRaisesRegex(ValueError, "finite_matching"):
            self.evidence()


@unittest.skipIf(np is None, "Numerical evidence contracts run in isolated Linux")
class PacedEvidenceTests(unittest.TestCase):
    write_report = EvidenceTests.write_report

    def setUp(self):
        from test_slam_supervised_replay import child_report, stopped, EXPECTED
        EvidenceTests.setUp(self)
        self.points = deepcopy(self.report["points"])
        self.points.update(packets=1, copiedPoints=2, lastNativeFrameId=1)
        self.points["nativeCounts"] = {
            "callbacks": 2, "initializationSkips": 1, "copies": 1, "takes": 1, "discardedOnClose": 0,
        }
        self.points["actualLoadedExtensionSha256"] = self.report["actualLoadedExtensionSha256"]
        latest = self.directory / "private-latest-points.npz"
        np.savez_compressed(latest, points=np.ones((2, 3)), world_lidar_pose=np.eye(4),
                            frame_id=1, algorithm_stamp=1000.1)
        self.points["latestSampleSha256"] = exporter.sha256(latest)
        frozen = deepcopy(self.report["inputIntegrity"])
        frozen["journal"] = EXPECTED
        self.child = child_report()
        self.child.update(result=exporter.trial.replay.CHILD_RESULT, inputIntegrity=frozen,
                          session="synthetic-session", replayRate=1.,
                          actualLoadedLibrariesSha256=self.report["actualLoadedLibrariesSha256"],
                          machine="synthetic-x86_64", versions={"numpy": np.__version__, "rosbags": "synthetic"})
        process = stopped()
        process.update(session="synthetic-session", processedScans=2, estimatedPoses=2,
                       mappingReady=False, localizationReady=False, navigationReady=False)
        self.parent = {
            "result": exporter.trial.RESULT, "inputIntegrity": frozen,
            "boundaries": dict.fromkeys(exporter.trial.replay.BOUNDARIES, False),
            "process": process, "healthChannelContainsPoints": False, "replayRate": 1.,
        }
        (self.directory / "private-child.log").write_text("synthetic private log", encoding="utf-8")
        self.write_paced()

    def write_paced(self):
        for name, value in (("child-report.json", self.child), ("point-report.json", self.points)):
            (self.directory / name).write_text(json.dumps(value), encoding="utf-8")
        self.parent.update(
            childReportSha256=exporter.sha256(self.directory / "child-report.json"),
            pointReportSha256=exporter.sha256(self.directory / "point-report.json"),
            privateLogSha256=exporter.sha256(self.directory / "private-child.log"))
        (self.directory / "report.json").write_text(json.dumps(self.parent), encoding="utf-8")

    def test_successful_paced_evidence_does_not_claim_live_arm64_or_ui(self):
        result = exporter.trial.evidence(self.directory)
        self.assertEqual(1., result["replayRate"])
        self.assertEqual(1, result["points"]["packets"])
        self.assertFalse(result["targetArm64PointExtensionValidated"])
        self.assertFalse(result["webOrAndroidIntegrated"])
        self.assertFalse(result["boundaries"]["liveSensorsSubscribed"])

    def test_forced_or_incomplete_process_cleanup_rejected(self):
        original = deepcopy(self.parent)
        for key, value in (("forcedTermination", True), ("cleanupComplete", False),
                           ("processAlive", True), ("navigationReady", True)):
            self.parent = deepcopy(original)
            self.parent["process"][key] = value
            self.write_paced()
            with self.subTest(key=key), self.assertRaises(ValueError):
                exporter.trial.evidence(self.directory)

    def test_missing_point_packet_rejected_even_with_updated_report_hash(self):
        self.points["packets"] = 0
        self.write_paced()
        with self.assertRaises(ValueError):
            exporter.trial.evidence(self.directory)

    def test_late_different_session_report_rejected(self):
        self.child["session"] = "different-synthetic-session"
        self.write_paced()
        with self.assertRaises(ValueError):
            exporter.trial.evidence(self.directory)


if __name__ == "__main__":
    unittest.main()
