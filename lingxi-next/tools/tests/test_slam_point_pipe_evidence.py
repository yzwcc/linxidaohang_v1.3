"""Explicitly synthetic reports only; never load native code or contact a robot."""
import argparse
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import export_glim_point_pipe_evidence as exporter
from test_slam_point_trial import summary_fixture
from test_slam_supervised_replay import child_report, stopped, EXPECTED


class PointPipeEvidenceTests(unittest.TestCase):
    def setUp(self):
        from build_slam_cpu_trial import PINS

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.args = argparse.Namespace(trial=self.directory, rate=1.)
        self.frozen = {
            "sourceCommits": PINS, "sourceSha256": exporter.replay.hashes(),
            "pointSourceSha256": exporter.points.source_hashes(),
            "transportSourceSha256": exporter.trial.source_hashes(), "journal": deepcopy(EXPECTED),
            "journalSha256": "a" * 64, "pointExtensionLibrarySha256": "b" * 64,
            "pointExtensionBuildSha256": "c" * 64, "consumerBundleSha256": "d" * 64,
            "nodeBinarySha256": "e" * 64,
        }
        for inventory in ("sourceSha256", "pointSourceSha256", "transportSourceSha256"):
            for name in self.frozen[inventory]:
                staged = self.directory / "runtime-source" / name
                if not staged.exists():
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, staged)
        self.child = child_report()
        baseline = exporter.replay.load(ROOT / "docs/slam-stream-input-evidence.json")["runs"]["static-start"]
        self.child.update(
            result=exporter.replay.CHILD_RESULT, inputIntegrity=deepcopy(self.frozen),
            session="f" * 32, replayRate=1., machine="x86_64",
            actualLoadedLibrariesSha256=baseline["actualLoadedLibrariesSha256"],
            versions={"numpy": "0.0.0", "rosbags": "0.0.0"})
        self.child["worker"]["stream"]["counts"]["initializationScans"] = 0
        self.native = summary_fixture()
        self.native.update(
            packets=1, copiedPoints=2, lastNativeFrameId=1, inputMode="recorded",
            nativeCounts={"callbacks": 2, "initializationSkips": 1, "copies": 1, "takes": 1, "discardedOnClose": 0},
            actualLoadedExtensionSha256={exporter.points.LIBRARY: "b" * 64},
            writer={"sent": 1, "replaced": 0, "failed": False, "threadAlive": False})
        self.consumer = {
            "schemaVersion": 1, "result": "recorded_native_points_received_api_live_guard_checked",
            "inputMode": "recorded", "transport": "private_same_host_unix_stream",
            "host": {"machine": "x64", "node": "v0.0.0"},
            "points": {
                "records": 1, "copiedPoints": 2, "retired": 0,
                "firstNativeFrameId": 1, "lastNativeFrameId": 1,
                "allPacketBytesSha256": self.native["allPacketBytesSha256"], "inputMode": "recorded",
            },
            "api": {
                "pairedWebSocketChecked": True, "recordedInputRejectedAsLive": True,
                "beforeAndAfterEofUnavailable": True, "readinessUnchanged": True,
                "robotCommandsSent": False, "tcpListenerStarted": False,
            },
        }
        process = stopped()
        process.update(
            session="f" * 32, workerState="stopped", failure=None, childFailure=None,
            processedScans=2, estimatedPoses=2, healthEvents=4, retiredHealthEvents=1,
            mappingReady=False, localizationReady=False, navigationReady=False)
        self.parent = {
            "schemaVersion": 1, "result": exporter.trial.RESULT, "inputIntegrity": deepcopy(self.frozen),
            "replayRate": 1., "inputMode": "recorded", "healthChannelContainsPoints": False,
            "limits": asdict(exporter.replay.LIMITS),
            "boundaries": dict.fromkeys(exporter.replay.BOUNDARIES, False),
            "process": process, "consumerProcess": {"exitCode": 0, "forcedTermination": False, "processAlive": False},
        }
        self.write_reports()

    def write_reports(self):
        for name, report in zip(exporter.REPORTS, (self.child, self.native, self.consumer)):
            (self.directory / name).write_text(json.dumps(report), encoding="utf-8")
        self.parent["privateReportsSha256"] = {
            name: exporter.sha256(self.directory / name) for name in exporter.REPORTS}
        (self.directory / "report.json").write_text(json.dumps(self.parent), encoding="utf-8")

    def evidence(self):
        # Only private input hashing is replaced. All report validation is real.
        with patch.object(exporter.trial, "integrity", return_value=self.frozen):
            return exporter.evidence(self.args)

    def mutate(self, section, path, value):
        target = getattr(self, section)
        for name in path[:-1]:
            target = target[name]
        target[path[-1]] = value
        self.write_reports()

    def test_complete_evidence_keeps_live_target_ui_and_navigation_unaccepted(self):
        result = self.evidence()
        self.assertEqual(1, result["points"]["packets"])
        self.assertEqual(result["points"]["allPacketBytesSha256"], result["consumerPoints"]["allPacketBytesSha256"])
        self.assertTrue(result["api"]["recordedInputRejectedAsLive"])
        self.assertFalse(any(result["acceptance"].values()))
        self.assertEqual(result, self.evidence())

    def test_extra_private_fields_never_pass_through_nested_reports(self):
        for target in (
            self.parent, self.parent["process"], self.parent["consumerProcess"], self.native,
            self.child, self.child["worker"], self.child["worker"]["counts"],
            self.child["worker"]["stream"]["counts"], self.child["nativeCalls"],
            self.consumer, self.consumer["host"], self.child["versions"],
        ):
            target["PRIVATE_MARKER"] = {"session": "secret", "xyz": [1, 2, 3], "path": "/private/input"}
        self.write_reports()
        text = json.dumps(self.evidence())
        self.assertNotIn("PRIVATE_MARKER", text)
        self.assertNotIn("f" * 32, text)
        self.assertNotIn("/private/input", text)

    def test_native_and_consumer_forced_exit_or_incomplete_cleanup_rejected(self):
        original = deepcopy(self.parent)
        modifications = (
            ("process", "forcedTermination", True), ("process", "cleanupComplete", False),
            ("process", "processAlive", True), ("process", "monitorAlive", True),
            ("process", "state", "failed"), ("process", "workerState", "running"),
            ("process", "failure", "synthetic_failure"), ("process", "childFailure", "synthetic_failure"),
            ("process", "exitCode", 1), ("consumerProcess", "forcedTermination", True),
            ("consumerProcess", "processAlive", True), ("consumerProcess", "exitCode", -15),
        )
        for section, name, value in modifications:
            self.parent = deepcopy(original)
            self.mutate("parent", (section, name), value)
            with self.subTest(section=section, name=name), self.assertRaises(ValueError):
                self.evidence()

    def test_report_mutations_rejected_even_with_recomputed_file_hashes(self):
        original = {name: deepcopy(getattr(self, name)) for name in ("parent", "child", "native", "consumer")}
        modifications = (
            ("parent", ("result",), "failed_or_interrupted"),
            ("parent", ("inputMode",), "live"),
            ("parent", ("healthChannelContainsPoints",), True),
            ("parent", ("boundaries", "robotContacted"), True),
            ("parent", ("limits", "progress_seconds"), 200.),
            ("parent", ("process", "completedMessages"), 4),
            ("parent", ("process", "navigationReady"), True),
            ("parent", ("process", "mappingReady"), True),
            ("parent", ("replayRate",), .5),
            ("child", ("session",), "a" * 32),
            ("child", ("stopReceived",), False),
            ("child", ("nativeCalls", "close"), 0),
            ("child", ("worker", "counts", "rejectedMessages"), 1),
            ("child", ("worker", "stream", "counts", "initializationScans"), 1),
            ("child", ("actualLoadedLibrariesSha256",), {}),
            ("child", ("machine",), "aarch64"),
            ("native", ("actualLoadedExtensionSha256",), {}),
            ("native", ("inputMode",), "live"),
            ("native", ("packets",), 0),
            ("native", ("poseFrame",), "robot_base"),
            ("native", ("writer", "sent"), 0),
            ("native", ("writer", "replaced"), 1),
            ("native", ("writer", "failed"), True),
            ("native", ("writer", "threadAlive"), True),
            ("consumer", ("points", "allPacketBytesSha256"), "9" * 64),
            ("consumer", ("points", "records"), 0),
            ("consumer", ("points", "retired"), 1),
            ("consumer", ("api", "recordedInputRejectedAsLive"), False),
            ("consumer", ("api", "tcpListenerStarted"), True),
            ("consumer", ("api", "readinessUnchanged"), False),
            ("consumer", ("host", "node"), "/private/node-version"),
        )
        for section, path, value in modifications:
            for name, report in original.items():
                setattr(self, name, deepcopy(report))
            self.mutate(section, path, value)
            with self.subTest(section=section, path=path), self.assertRaises(ValueError):
                self.evidence()

    def test_boolean_integer_aliases_do_not_satisfy_counters_or_flags(self):
        original = {name: deepcopy(getattr(self, name)) for name in ("parent", "native", "consumer")}
        for section, path, value in (
            ("parent", ("process", "exitCode"), False),
            ("parent", ("consumerProcess", "exitCode"), False),
            ("parent", ("process", "healthEvents"), True),
            ("parent", ("boundaries", "robotContacted"), 0),
            ("native", ("writer", "replaced"), False),
            ("native", ("writer", "failed"), 0),
            ("consumer", ("points", "records"), True),
            ("consumer", ("api", "pairedWebSocketChecked"), 1),
        ):
            for name, report in original.items():
                setattr(self, name, deepcopy(report))
            self.mutate(section, path, value)
            with self.subTest(section=section, path=path), self.assertRaises(ValueError):
                self.evidence()

    def test_changed_current_input_or_source_identity_rejected(self):
        frozen = deepcopy(self.frozen)
        frozen["consumerBundleSha256"] = "0" * 64
        with patch.object(exporter.trial, "integrity", return_value=frozen):
            with self.assertRaises(ValueError):
                exporter.evidence(self.args)
        with patch.object(exporter.trial, "source_hashes", return_value={"changed": "0" * 64}):
            with self.assertRaises(ValueError):
                self.evidence()

    def test_changed_private_report_bytes_rejected_without_hash_refresh(self):
        self.child["PRIVATE_MARKER"] = "changed"
        (self.directory / "child-report.json").write_text(json.dumps(self.child), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.evidence()

    def test_changed_staged_runtime_source_rejected(self):
        staged = self.directory / "runtime-source/tools/slam-point-api-consumer.ts"
        staged.write_text("// explicitly synthetic altered snapshot\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unchanged_staged"):
            self.evidence()

    def test_cli_rate_must_match_observed_replay_rate(self):
        self.args.rate = .5
        with self.assertRaises(ValueError):
            self.evidence()


if __name__ == "__main__":
    unittest.main()
