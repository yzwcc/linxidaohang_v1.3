"""Synthetic full-mapper diagnostics over health IPC; never invoke a native mapper."""
import io
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.process_protocol import HealthEmitter, decode, mapper_health, validate
from lingxi_slam.mapping_worker import MappingWorker


def health():
    return {"engine": "glim-full-async-v1", "acceptedFrames": 50, "completedFrames": 45, "frameCapacity": 128,
            "acceptedImu": 1000, "completedImu": 990, "imuCapacity": 8192, "observedAtSeconds": 9.9,
            "maximumWaitMs": 6000., "maximumTaskMs": 5000.}


class MappingHealthContracts(unittest.TestCase):
    def test_optional_mapper_counters_survive_exact_health_roundtrip(self):
        sink = io.BytesIO()
        event = HealthEmitter(sink, "a" * 32, clock=lambda: 10.).emit({
            "state": "running", "failure": None, "counts": {"completedMessages": 2000},
            "stream": {"counts": {"processedScans": 100, "estimatedPoses": 80}},
        }, mapper=health())
        self.assertEqual(event, decode(sink.getvalue(), 4096))
        self.assertEqual(45, event["mapper"]["completedFrames"])
        self.assertEqual(2000, event["completedMessages"])
        self.assertTrue(set(event["mapper"]).isdisjoint(("points", "poses", "configuration")))
        with self.assertRaises(ValueError):
            validate({**event, "mapper": {**health(), "observedAtSeconds": 11.}})

    def test_invalid_overflow_or_private_mapper_data_is_rejected(self):
        for key, value in (("acceptedFrames", True), ("completedFrames", 51), ("acceptedFrames", 174),
                           ("frameCapacity", 256), ("acceptedImu", 20000), ("imuCapacity", 16000),
                           ("observedAtSeconds", float("nan")), ("maximumWaitMs", -1),
                           ("engine", "legacy"), ("points", [])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                mapper_health({**health(), key: value})

    def test_control_reader_only_reads_cache_and_returns_its_own_copy(self):
        cached = {**health(), "peakFrames": 10}
        worker = object.__new__(MappingWorker)
        worker.phase_lock = threading.Lock()
        worker.native_session = SimpleNamespace(mapping=SimpleNamespace(cached_health=lambda: cached.copy()))
        result = []
        thread = threading.Thread(target=lambda: result.append(worker.mapper_health()))
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(health(), result[0])
        result[0]["completedFrames"] = 999
        self.assertEqual(45, worker.mapper_health()["completedFrames"])
        worker.native_session = None
        self.assertIsNone(worker.mapper_health())


if __name__ == "__main__":
    unittest.main()
