import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_shared_slam_live_package as check


class SharedLivePackageTests(unittest.TestCase):
    def setUp(self):
        self.state = {"mappingSession": "a" * 32, "mappingStatus": "running",
                      "mappingProgress": {"processedScans": 120, "estimatedPoses": 60}}
        child = dict(exitCode=0, forcedTermination=False, processAlive=False, groupAlive=False)
        self.owner = dict(result="stopped", remainingChildren=0,
            adoptedChildren=dict(cleanupComplete=True, forcedTermination=False),
            serviceProcess=child.copy(), apiProcess=child.copy())

    def test_mapping_waits_for_progress_and_warmup(self):
        self.assertTrue(check.mapping_progress(self.state, "a" * 32, 10.))
        self.assertFalse(check.mapping_progress(self.state, "a" * 32, 9.9))
        for field, value in (("processedScans", 119), ("estimatedPoses", 59), ("processedScans", True)):
            with self.subTest(field=field, value=value):
                state = copy.deepcopy(self.state)
                state["mappingProgress"][field] = value
                self.assertFalse(check.mapping_progress(state, "a" * 32, 20.))

    def test_mapping_owner_change_or_failure_aborts(self):
        for value in ({"mappingSession": "b" * 32}, {"mappingStatus": "failed"}):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                check.mapping_progress({**self.state, **value}, "a" * 32, 20.)

    def test_mapping_incomplete_or_initializing_does_not_pass(self):
        for value in ({"mappingProgress": None}, {"mappingStatus": "starting"}):
            self.assertFalse(check.mapping_progress({**self.state, **value}, "a" * 32, 20.))

    def test_unseeded_state_never_claims_a_pose(self):
        state = dict(state="unseeded", mapFromSensor=None, localizationReady=False, navigationReady=False)
        check.unseeded_state(state)
        for field, value in (("state", "tracking"), ("mapFromSensor", [1]),
                             ("localizationReady", True), ("navigationReady", True)):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                check.unseeded_state({**state, field: value})

    def test_clean_owner_requires_actual_clean_children(self):
        check.clean_owner(self.owner)
        for field, value in (("result", "failed"), ("remainingChildren", 1)):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                check.clean_owner({**self.owner, field: value})
        for name in ("serviceProcess", "apiProcess"):
            for field, value in (("exitCode", 1), ("exitCode", False), ("forcedTermination", True),
                                 ("processAlive", True), ("groupAlive", True)):
                changed = copy.deepcopy(self.owner)
                changed[name][field] = value
                with self.subTest(name=name, field=field), self.assertRaises(RuntimeError):
                    check.clean_owner(changed)
        for field, value in (("cleanupComplete", False), ("forcedTermination", True)):
            changed = copy.deepcopy(self.owner)
            changed["adoptedChildren"][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                check.clean_owner(changed)


class PublishedSharedLivePackageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.value = json.loads((self.root / "docs/slam-workspace-live-arm64-evidence.json").read_bytes())

    def test_sources_and_isolated_python_lock_match_tested_bytes(self):
        for name, expected in self.value["sourceSha256"].items():
            self.assertEqual(expected, hashlib.sha256((self.root / name).read_bytes()).hexdigest())
        self.assertEqual(self.value["lockSha256"], hashlib.sha256(
            (self.root / "robot-slam/runtime-arm64-cp310.lock.json").read_bytes()).hexdigest())
        self.assertFalse(self.value["target"]["systemSitePackages"])
        self.assertEqual("1.26.4", self.value["target"]["versions"]["numpy"])

    def test_live_catalog_lifecycle_does_not_claim_navigation(self):
        self.assertEqual(check.RESULT, self.value["result"])
        self.assertTrue(all(value is False for value in self.value["boundaries"].values()))
        self.assertEqual(0, self.value["seedRequests"])
        self.assertEqual(2, self.value["cleanOwnerLaunches"])
        self.assertTrue(self.value["mapping"]["freshProcessReloadPassed"])
        self.assertTrue(self.value["mapping"]["restartPreservedMap"])
        self.assertGreater(self.value["mapping"]["savedCounts"]["points"], 0)
        self.assertEqual([1, 2, 3, 4, 5], self.value["localization"]["referenceFramesObserved"])
        for key in ("mappingLocalizationExclusionChecked", "mappingAdmissionRestored"):
            self.assertTrue(self.value[key])
        for key in ("pauseConfirmed", "stopConfirmed", "staleGenerationRejected"):
            self.assertTrue(self.value["localization"][key])
        self.assertEqual(0, self.value["finalObservation"]["remainingTrialProcesses"])
        self.assertTrue(self.value["finalObservation"]["testPortClosed"])
        self.assertTrue(self.value["finalObservation"]["existingConsoleListening"])
        self.assertTrue(self.value["basePoseInput"]["closed"])
        self.assertFalse(self.value["basePoseInput"]["physicalScaleValidated"])
        observed = self.value["basePoseInput"]["observations"]
        self.assertEqual(observed["timedFrames"], observed["tfLookupsAvailable"] + observed["tfLookupsUnavailable"])

    def test_public_evidence_excludes_connection_data_and_raw_geometry(self):
        text = json.dumps(self.value)
        for marker in ("/home/", "192.168.", "10.201.", '"password"', '"credentials"',
                       '"pairingCode"', '"token"', '"mapFromSensor"', '"pointCoordinates"'):
            self.assertNotIn(marker, text)

    def test_retained_private_reports_match_published_lifecycle(self):
        folder = self.root / ".local/shared-live-package-arm64-20261031-a"
        if not folder.is_dir():
            self.skipTest("private commissioning data is intentionally not committed")
        for filename, field in (("check-report.json", "privateReportSha256"),
                                ("collection-report.json", "collectionReportSha256"),
                                ("final-observation.json", "finalObservationSha256")):
            self.assertEqual(self.value[field], hashlib.sha256((folder / filename).read_bytes()).hexdigest())
        report = json.loads((folder / "check-report.json").read_bytes())
        self.assertEqual(check.RESULT, report["result"])
        self.assertEqual(self.value["mapping"]["savedCounts"], report["savedCounts"])
        self.assertEqual(self.value["localization"]["counts"], report["localization"]["counts"])
        for owner in report["ownerReports"]:
            check.clean_owner(owner["report"])
        collection = json.loads((folder / "collection-report.json").read_bytes())
        self.assertEqual("collected", collection["result"])
        self.assertTrue(collection["connectionClosed"])
        for name, expected in collection["filesSha256"].items():
            self.assertEqual(expected, hashlib.sha256((folder / name).read_bytes()).hexdigest())

    def test_retained_archive_is_the_verified_live_successor(self):
        archive = self.root / "artifacts/lingxi-next-shared-slam-20261031-r8-arm64.tar.gz"
        if not archive.is_file():
            self.skipTest("generated archives are intentionally not committed")
        sys.path.insert(0, str(self.root / "robot-runtime/python"))
        import live_bundle
        self.assertEqual(self.value["package"]["archiveSha256"], live_bundle.sha256(archive))
        manifest = live_bundle.verify_archive(archive, self.value["package"]["manifestSha256"], profile=live_bundle.SLAM)
        self.assertEqual(manifest["sourceSha256"], live_bundle.source_hashes(profile=live_bundle.SLAM))


if __name__ == "__main__":
    unittest.main()
