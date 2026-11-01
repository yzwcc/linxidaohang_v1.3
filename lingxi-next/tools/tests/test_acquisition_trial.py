"""Recorded-ray diagnostics: bounded accounting and frozen inputs, without robot IO."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "robot-slam")]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "json5"))
if AVAILABLE:
    import numpy as np
    import run_glim_acquisition_trial as trial
    from lingxi_slam.acquisition_output import decode_acquisition
    from test_acquisition_output import packet_bytes


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python and JSON5")
class SummaryTests(unittest.TestCase):
    def packet(self, frame_id=1, stamp=10.):
        return decode_acquisition(packet_bytes(frame_id, stamp), (frame_id, 3, 2, 840), stamp, np.eye(4))

    def output(self, count, closed=True):
        return SimpleNamespace(closed=closed, counts=lambda: {
            "callbacks": count + 1, "initializationSkips": 1,
            "copies": count, "takes": count, "discardedOnClose": 0})

    def test_all_packets_accounted_but_only_first_and_latest_retained(self):
        summary = trial.RaySummary()
        for index in range(1, 8):
            summary.accept(self.packet(index, 9. + index))
        report = summary.report(self.output(7), 8)
        self.assertEqual((7, 21, [3, 3]), (report["packets"], report["returns"], report["returnsPerPacketMinMax"]))
        self.assertEqual((1, 7), (summary.first.native_frame_id, summary.latest.native_frame_id))
        self.assertEqual(2, report["maximumRetainedPackets"])
        self.assertFalse(report["receiptOrMetricAdmissionSynthesized"])
        self.assertNotIn("received_at", report)
        self.assertEqual("lingxi_glim_odom", report["coordinateFrame"])

    def test_source_and_geometry_change_digest(self):
        first, second, third = (trial.RaySummary() for _ in range(3))
        packet = self.packet()
        changed = packet.endpoints.copy()
        changed[0, 0] += .1
        changed.flags.writeable = False
        for summary, value in ((first, packet), (second, replace(packet, source_sha256="ab" * 32)),
                               (third, replace(packet, endpoints=changed))):
            summary.accept(value)
        self.assertEqual(3, len({item.digest.hexdigest() for item in (first, second, third)}))

    def test_duplicate_skipped_and_backwards_packets_rejected(self):
        summary = trial.RaySummary()
        summary.accept(self.packet())
        for packet in (self.packet(), self.packet(3, 11.), self.packet(2, 9.)):
            with self.subTest(frame_id=packet.native_frame_id), self.assertRaises(ValueError):
                summary.accept(packet)
        self.assertEqual(1, summary.count)

    def test_mutable_wrong_frame_or_mismatched_rays_rejected(self):
        packet = self.packet()
        for value in (replace(packet, origins=packet.origins.copy()),
                      replace(packet, coordinate_frame="saved_map"),
                      replace(packet, endpoints=packet.endpoints[:2]),
                      replace(packet, kind="finalized_submap")):
            with self.subTest(kind=value.kind), self.assertRaises(ValueError):
                trial.RaySummary().accept(value)

    def test_wrong_thread_cannot_accept_or_finish(self):
        from test_acquisition_output import other_thread
        summary = trial.RaySummary()
        for function in (lambda: summary.accept(self.packet()), lambda: summary.report(self.output(0), 1)):
            self.assertIsInstance(other_thread(function)[0], ValueError)

    def test_open_incomplete_or_discarded_output_cannot_report_success(self):
        summary = trial.RaySummary()
        summary.accept(self.packet())
        discarded = self.output(1)
        discarded.counts = lambda: {"callbacks": 2, "initializationSkips": 1,
                                   "copies": 1, "takes": 0, "discardedOnClose": 1}
        for output, poses in ((self.output(1, False), 2), (self.output(1), 3), (discarded, 2)):
            with self.subTest(poses=poses), self.assertRaises(ValueError):
                summary.report(output, poses)


@unittest.skipUnless(AVAILABLE, "requires isolated numerical Python and JSON5")
class InputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        self.config = root / "config/config_odometry_cpu.json"
        self.config.write_text('{// upstream GLIM comments\n"odometry_estimation": {"save_imu_rate_trajectory": true}}')
        self.args = SimpleNamespace(acquisition=root / "extension", bundle=root)
        self.built = {"result": trial.RESULT, "sourceCommits": trial.PINS, "sourceSha256": {"current": "source"},
                      "coreBuildReportSha256": "core", "librarySha256": "library", "nativeContracts": 29,
                      "stages": [{"name": name, "exitCode": 0} for name in ("configure", "build", "contracts")]}
        for mocker in (
                patch.object(trial.replay, "inputs", return_value={"coreBuildReportSha256": "core"}),
                patch.object(trial, "source_hashes", return_value={"current": "source"}),
                patch.object(trial, "load", side_effect=lambda path: self.built),
                patch.object(trial, "sha256", side_effect=lambda path: "library" if path.suffix == ".so" else "report")):
            mocker.start()
            self.addCleanup(mocker.stop)

    def test_commented_upstream_config_uses_structured_json5(self):
        frozen = trial.recorded_integrity(self.args)
        self.assertEqual("library", frozen["acquisitionLibrarySha256"])
        self.assertEqual("report", frozen["acquisitionBuildReportSha256"])

    def test_missing_disabled_or_string_trajectory_setting_rejected(self):
        for config in ('{}', '{"odometry_estimation": {"save_imu_rate_trajectory": false}}',
                       '{"odometry_estimation": {"save_imu_rate_trajectory": "true"}}'):
            self.config.write_text(config)
            with self.subTest(config=config), self.assertRaises(ValueError):
                trial.recorded_integrity(self.args)

    def test_duplicate_trajectory_keys_are_not_silently_accepted(self):
        self.config.write_text('{"odometry_estimation": {"save_imu_rate_trajectory": false, "save_imu_rate_trajectory": true}}')
        with self.assertRaises(ValueError):
            trial.recorded_integrity(self.args)

    def test_source_library_core_or_build_failure_rejects_before_replay(self):
        for field, value in (("sourceSha256", {}), ("librarySha256", "changed"),
                             ("coreBuildReportSha256", "other"), ("sourceCommits", {}),
                             ("nativeContracts", 28), ("stages", []), ("result", "failed_or_interrupted")):
            with self.subTest(field=field), patch.dict(self.built, {field: value}), self.assertRaises(ValueError):
                trial.recorded_integrity(self.args)


if __name__ == "__main__":
    unittest.main()
