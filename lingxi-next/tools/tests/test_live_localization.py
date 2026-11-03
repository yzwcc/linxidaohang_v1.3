"""Live completion must prove bounded callback accounting and terminal revocation."""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")]
AVAILABLE = sys.platform == "linux" and importlib.util.find_spec("numpy") is not None
if AVAILABLE:
    from live_localization import verify_completion
    from lingxi_slam.localization_session import LocalizationBinding


@unittest.skipUnless(AVAILABLE, "requires Linux runtime dependencies")
class LiveCompletion(unittest.TestCase):
    def setUp(self):
        self.binding = LocalizationBinding(
            "1" * 32, "live", "slam-" + "2" * 32, "3" * 64, "4" * 64, "5" * 64)
        self.frozen = {"ddsProfileSha256": "6" * 64}
        self.sources, self.config_hash = {"source.py": "7" * 64}, "8" * 64
        self.report = {
            "result": "live_localization_runtime_closed", "session": self.binding.session,
            "inputMode": "live", "stopReceived": True, "failures": [], "controlThreadAlive": False,
            "worker": {
                "state": "stopped", "failure": None, "threadAlive": False,
                "counts": {"acceptedImu": 10, "acceptedClouds": 3, "completedMessages": 12,
                           "discardedMessages": 1, "rejectedMessages": 0},
                **dict.fromkeys(("queuedMessages", "queuedClouds", "queuedPayloadBytes", "inFlightMessages"), 0),
                "stream": {"state": "finished", "failure": None,
                           "counts": {"estimatedPoses": 1, "insertedImu": 10, "processedScans": 2}},
            },
            "nativeCalls": {"create": 1, "close": 1, "imu": 10, "frame": 2},
            "nativeOwnerIsSingleNonMainThread": True,
            "passive": {"stopReason": "requested_stop"},
            "inputIntegrity": {**self.frozen, "localizationBinding": asdict(self.binding),
                "localizationSourceSha256": self.sources, "localizationConfigSha256": self.config_hash},
            "robotContacted": True, "motionCommandsSent": False, "localizationReady": False,
            "navigationReady": False, "mapProfileAssociationVerified": False,
        }
        self.final = {"state": {
            "binding": asdict(self.binding), "state": "stopped", "mapFromSensor": None,
            "referenceFrame": None, "cleanupComplete": True, "workerClosed": True,
            "workerReady": False, "requestPending": False,
            "localizationReady": False, "navigationReady": False,
        }, "robotContacted": True, "motionCommandsSent": False, "mapProfileAssociationVerified": False}

    def verify(self, report=None, final=None):
        verify_completion(self.report if report is None else report,
                          self.final if final is None else final,
                          frozen=self.frozen, binding=self.binding,
                          sources=self.sources, config_hash=self.config_hash)

    def test_clean_discarded_tail_is_not_claimed_as_full_input(self):
        self.verify()

    def test_acquisition_is_explicit_and_counters_match_the_actual_pose_stream(self):
        expected = {'settingsSha256': 'a' * 64}
        self.report['acquisition'] = dict(configured=True, nativeCreated=True, nativeClosed=True,
            sourceRetired=True, gridAbsent=True, terrainOwnerClosed=True, terrainChildCleaned=True,
            failed=False, failureReason=None, physicalScaleValidated=False, groundSupportValidated=False,
            navigationReady=False, terrainConfigured=False, registeredPackets=0, registeredReturns=0,
            terrainSubmissions=0, inputIntegrity=expected,
            nativeCounts=dict(callbacks=1, initializationSkips=1, copies=0, takes=0, discardedOnClose=0))
        with self.assertRaisesRegex(ValueError, 'explicit_live_acquisition'):
            self.verify()
        def check():
            verify_completion(self.report, self.final, frozen=self.frozen, binding=self.binding,
                sources=self.sources, config_hash=self.config_hash, acquisition_integrity=expected)
        check()
        self.report['acquisition']['nativeCounts'].update(callbacks=2, copies=1, takes=1)
        with self.assertRaisesRegex(ValueError, 'counter_accounting'):
            check()
        del self.report['acquisition']
        with self.assertRaisesRegex(ValueError, 'complete_passive_acquisition'):
            check()

    def test_counter_ownership_and_input_mutations_are_rejected(self):
        cases = (
            (("stopReceived",), False), (("controlThreadAlive",), True),
            (("session",), "9" * 32), (("failures",), ["control_failed"]),
            (("worker", "counts", "completedMessages"), 13),
            (("worker", "counts", "rejectedMessages"), 1),
            (("worker", "counts", "acceptedImu"), True),
            (("worker", "queuedMessages"), 1), (("worker", "threadAlive"), True),
            (("worker", "stream", "counts", "estimatedPoses"), True),
            (("nativeCalls", "close"), 0), (("nativeCalls", "imu"), 9),
            (("nativeOwnerIsSingleNonMainThread",), False),
            (("passive", "stopReason"), "duration_reached"),
            (("inputIntegrity", "ddsProfileSha256"), "9" * 64),
            (("inputIntegrity", "localizationBinding"), {}),
            (("inputIntegrity", "localizationSourceSha256"), {}),
            (("inputIntegrity", "localizationConfigSha256"), "9" * 64),
        )
        for keys, value in cases:
            report = deepcopy(self.report)
            target = report
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                self.verify(report=report)

    def test_terminal_pose_reference_pending_and_readiness_are_rejected(self):
        for key, value in (("mapFromSensor", [[1]]), ("referenceFrame", 1),
                           ("state", "tracking"), ("cleanupComplete", False),
                           ("workerClosed", False), ("workerReady", True),
                           ("requestPending", True), ("localizationReady", True),
                           ("navigationReady", True)):
            final = deepcopy(self.final)
            final["state"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify(final=final)

    def test_optional_base_projection_requires_revoked_pose_and_empty_closed_inputs(self):
        self.final["state"]["basePose"] = {
            "status": "unavailable", "mapFromBase": None, "lastFrame": 0,
            "scanHeaderNs": None, "effectiveScanNs": None, "metresPerMapUnit": 1.,
            "coordinateFrame": "lingxi_saved_map", "bodyFrame": "base_link",
            "physicalScaleValidated": False, "navigationReady": False,
        }
        self.report["basePoseInput"] = {
            "closed": True, "pendingScanTimes": 0, "retainedDynamicTransforms": 0,
            "metresPerMapUnit": 1., "physicalScaleValidated": False, "navigationReady": False,
        }
        def check(frame=None):
            verify_completion(self.report, self.final, frozen=self.frozen, binding=self.binding,
                              sources=self.sources, config_hash=self.config_hash, base_scale=1.,
                              base_navigation_from_raw=frame)
        check()
        with self.assertRaises(ValueError):
            self.verify()
        for container, key, value in ((self.final["state"]["basePose"], "mapFromBase", [[1]]),
                                       (self.report["basePoseInput"], "closed", False),
                                       (self.report["basePoseInput"], "pendingScanTimes", 1),
                                       (self.report["basePoseInput"], "retainedDynamicTransforms", 1)):
            before = container[key]
            container[key] = value
            with self.assertRaises(ValueError):
                check()
            container[key] = before
        frame = [[0., -1., 0., 2.], [1., 0., 0., -1.], [0., 0., 1., .8], [0., 0., 0., 1.]]
        for container in (self.final["state"]["basePose"], self.report["basePoseInput"]):
            with self.assertRaises(ValueError):
                check(frame)
            container["navigationFromRaw"] = deepcopy(frame)
        check(frame)
        with self.assertRaises(ValueError):
            check()
        self.report["basePoseInput"]["navigationFromRaw"][0][3] += .001
        with self.assertRaises(ValueError):
            check(frame)


if __name__ == "__main__":
    unittest.main()
