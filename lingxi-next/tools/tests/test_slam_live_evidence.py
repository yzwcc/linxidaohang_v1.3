"""Synthetic live evidence/accounting tests; no target or real sensor data."""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import platform
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
AVAILABLE = (platform.system() == "Linux" and importlib.util.find_spec("numpy") is not None
             and importlib.util.find_spec("json5") is not None)
if AVAILABLE:
    sys.path.insert(0, str(ROOT / "tools"))
    import export_glim_live_evidence as evidence
    from lingxi_slam.stream import Limits
    from lingxi_slam.worker import WorkerLimits


@unittest.skipUnless(AVAILABLE, "requires Linux isolated NumPy and json5")
class LiveEvidence(unittest.TestCase):
    def state(self):
        counts = dict.fromkeys(evidence.COUNT_FIELDS, 0)
        counts.update(acceptedClouds=3, acceptedImu=3, completedMessages=6,
                      maximumQueuedMessages=2, maximumQueuedClouds=1, maximumQueuedPayloadBytes=1024)
        stream = dict.fromkeys((
            "receivedImu", "receivedClouds", "insertedImu", "processedScans", "initializationScans", "estimatedPoses",
            "skippedBeforeImu", "skippedAfterImuAtEndOfInput", "discardedOnFailure",
            "maximumPendingImu", "maximumPendingClouds", "maximumPendingBytes",
        ), 0)
        stream.update(receivedImu=3, receivedClouds=3, insertedImu=3, processedScans=3,
                      initializationScans=1, estimatedPoses=2, maximumPendingImu=3,
                      maximumPendingClouds=1, maximumPendingBytes=1024)
        return {
            "state": "stopped", "failure": None, "threadAlive": False, "counts": counts,
            "limits": asdict(WorkerLimits()), "queuedMessages": 0, "queuedClouds": 0,
            "queuedPayloadBytes": 0, "inFlightMessages": 0, "maximumObservedQueueAgeSeconds": .1,
            "stream": {"state": "finished", "failure": None, "counts": stream, "limits": asdict(Limits()),
                       "pendingImu": 0, "pendingClouds": 0, "pendingCloudBytes": 0,
                       "maximumObservedScanWaitSeconds": .05},
        }

    def observed(self):
        return {"imu": {"callbacks": 3}, "cloud": {"callbacks": 3},
                "processedScans": 3, "initializationScans": 1, "estimatedPoses": 2}

    def native(self):
        return {"create": 1, "close": 1, "imu": 3, "frame": 3}

    def test_complete_worker_aggregate_has_no_coordinates_headers_topics_or_private_logs(self):
        public = evidence.public_worker(self.state(), self.observed(), self.native())
        self.assertTrue(public["threadJoinedAndQueuesReleased"])
        self.assertEqual(0, public["counts"]["discardedMessages"])
        text = json.dumps(public)
        for private in ("poses", "headers_ns", "private-native", "topic", "192.168"):
            self.assertNotIn(private, text)

    def test_explicit_stop_discard_is_counted_not_misrepresented_as_processed(self):
        state = self.state()
        state["counts"].update(completedMessages=5, discardedMessages=1)
        state["stream"]["counts"].update(receivedImu=2, insertedImu=2)
        native = self.native()
        native["imu"] = 2
        public = evidence.public_worker(state, self.observed(), native)
        self.assertEqual(1, public["discardedMessagesAreExplicitSessionStopNotUnreportedLoss"])
        self.assertEqual(2, public["stream"]["counts"]["receivedImu"])

    def test_bad_accounting_rejection_unjoined_thread_and_queue_age_are_rejected(self):
        for mutate in (
            lambda state: state.update(threadAlive=True),
            lambda state: state.update(maximumObservedQueueAgeSeconds=float("nan")),
            lambda state: state.update(maximumObservedQueueAgeSeconds=.51),
            lambda state: state["counts"].update(rejectedMessages=1),
            lambda state: state["counts"].update(discardedMessages=1),
            lambda state: state["stream"]["counts"].update(processedScans=2),
        ):
            state = self.state()
            mutate(state)
            with self.assertRaises(ValueError):
                evidence.public_worker(state, self.observed(), self.native())

    def test_native_and_private_array_counts_must_match_not_only_worker_summary(self):
        native = self.native()
        native["close"] = 0
        with self.assertRaises(ValueError):
            evidence.public_worker(self.state(), self.observed(), native)
        observed = self.observed()
        observed["estimatedPoses"] = 3
        with self.assertRaises(ValueError):
            evidence.public_worker(self.state(), observed, self.native())

    def test_nested_metrics_recomputed_with_numeric_tolerance_and_exact_shape(self):
        expected = {"cloud": {"callbacks": 3, "milliseconds": [1., 2., 3.]}, "estimatedPoses": 2}
        self.assertTrue(evidence.matching_observations(expected, expected))
        actual = {"cloud": {"callbacks": 3, "milliseconds": [1., 2., 3.]}, "estimatedPoses": 1}
        self.assertFalse(evidence.matching_observations(actual, expected))
        self.assertFalse(evidence.matching_observations({"extra": 1, **expected}, expected))
        self.assertFalse(evidence.matching_observations(None, expected))


if __name__ == "__main__":
    unittest.main()
