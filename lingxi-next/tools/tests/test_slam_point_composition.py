"""Synthetic peers and read-only published aggregates; no robot or ARM execution."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import export_glim_point_composition as exporter
import extract_glim_point_consumer as extractor
import run_glim_point_composition_target as coordinator
from test_slam_point_consumer_evidence import fixture as archive_fixture
import test_slam_point_pipe_evidence as pipe_fixture
from test_slam_point_target_coordinator import MemorySftp


def arguments(root, **changes):
    return argparse.Namespace(**{
        "operator_confirmed": True, "host": "synthetic.invalid", "user": "synthetic",
        "known_identity": "saved.synthetic.invalid",
        "directory": "lingxi-next-validation-arm64-point-source-synthetic",
        "consumer_directory": "lingxi-next-validation-arm64-point-consumer-synthetic",
        "recorded_directory": "lingxi-next-validation-arm64-recorded-synthetic",
        "core_directory": "lingxi-next-validation-arm64-core-synthetic",
        "point_directory": "lingxi-next-validation-arm64-point-synthetic",
        "run_name": "glim-point-channel-synthetic", "rate": 1., "hypothesis": "header_is_scan_start",
        "output": root / ".local/new", "preparation": root / "preparation",
        "core_manifest_sha256": "a" * 64, "replay_manifest_sha256": "b" * 64,
        "point_manifest_sha256": "c" * 64, **changes,
    })


class ExtractionTests(unittest.TestCase):
    def test_synthetic_archive_extracted_and_checked_without_node_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, report = archive_fixture(root)
            result = extractor.extract(root / "point-consumer-arm64.tar.gz", root / "new",
                                       report["archiveSha256"], report["manifestSha256"])
            self.assertEqual(extractor.RESULT, result["result"])
            self.assertFalse(result["nodeExecuted"])
            self.assertEqual(8, result["bundleFiles"])
            self.assertEqual((root / "bundle/consumer.mjs").read_bytes(), (root / "new/consumer.mjs").read_bytes())
            if os.name == "posix":
                self.assertEqual(0o700, (root / "new/node/bin/node").stat().st_mode & 0o777)
                self.assertEqual(0o600, (root / "new/consumer.mjs").stat().st_mode & 0o777)

    def test_existing_output_preserved_before_any_file_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, report = archive_fixture(root)
            (root / "new").mkdir()
            sentinel = root / "new/preserved"
            sentinel.write_bytes(b"synthetic original")
            with self.assertRaises(ValueError):
                extractor.extract(root / "point-consumer-arm64.tar.gz", root / "new",
                                  report["archiveSha256"], report["manifestSha256"])
            self.assertEqual(b"synthetic original", sentinel.read_bytes())

    def test_wrong_archive_or_manifest_identity_creates_no_output(self):
        for key in ("archiveSha256", "manifestSha256"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _, report = archive_fixture(root)
                report[key] = "0" * 64
                with self.subTest(key=key), self.assertRaises(ValueError):
                    extractor.extract(root / "point-consumer-arm64.tar.gz", root / "new",
                                      report["archiveSha256"], report["manifestSha256"])
                self.assertFalse((root / "new").exists())

    def test_incomplete_or_changed_archive_rejected_before_extraction(self):
        for change in ({"omitted": "consumer.mjs"}, {"changed": "consumer.mjs"}):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _, report = archive_fixture(root, **change)
                with self.subTest(change=change), self.assertRaises(ValueError):
                    extractor.extract(root / "point-consumer-arm64.tar.gz", root / "new",
                                      report["archiveSha256"], report["manifestSha256"])
                self.assertFalse((root / "new").exists())


class CoordinatorTests(unittest.TestCase):
    def test_confirmation_precedes_input_resolution_or_connection(self):
        for value in (None, False, 1, "true"):
            with self.subTest(value=value), patch.object(coordinator.remote, "connect") as connect, \
                    patch.object(coordinator.preparation, "evidence") as preparation:
                with self.assertRaisesRegex(ValueError, "confirmation"):
                    coordinator.run(argparse.Namespace(operator_confirmed=value))
                connect.assert_not_called()
                preparation.assert_not_called()

    def test_paths_reject_aliases_names_invalid_rate_or_unbounded_socket(self):
        home = PurePosixPath("/home/synthetic")
        for changes in (
            {"consumer_directory": arguments(Path()).directory}, {"directory": "../escape"},
            {"run_name": "../escape"}, {"core_directory": arguments(Path()).point_directory},
            {"rate": True}, {"hypothesis": "automatic"}, {"run_name": "glim-point-channel-" + "a" * 90},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                coordinator.paths(home, arguments(Path(), **changes))

    def test_replay_command_is_recorded_bounded_clean_and_uses_prepared_arm64_runtime(self):
        args = arguments(Path())
        values = coordinator.paths(PurePosixPath("/home/synthetic"), args)
        tokens = shlex.split(coordinator.replay_command(values, args, "d" * 64))
        self.assertEqual(["timeout", "--signal=INT", "--kill-after=10s", "180s",
                          "/usr/bin/nice", "-n", "10", "env", "-i"], tokens[:9])
        self.assertIn("OPENBLAS_NUM_THREADS=2", tokens)
        self.assertIn("OMP_NUM_THREADS=2", tokens)
        self.assertEqual(str(values[1]), tokens[tokens.index("--consumer-root") + 1])
        self.assertEqual(str(values[2] / "result-live"), tokens[tokens.index("--reference") + 1])
        self.assertIn("--operator-confirmed", tokens)
        self.assertNotIn("glim_live_session.py", " ".join(tokens))
        self.assertFalse(any(token.startswith(("ROS_DOMAIN_ID=", "LD_PRELOAD=", "HOME=")) for token in tokens))

    def case(self, root, *, fail=False, bad_preflight=False, changed_download=False, existing_remote=False,
             bad_node=False, failed_extraction=False):
        args = arguments(root)
        args.preparation.mkdir()
        docs = root / "docs"
        docs.mkdir()
        prepared = {"manifestSha256": "d" * 64, "archiveSha256": "e" * 64,
                    "archiveBytes": 9, "bundleFiles": 8}
        (docs / "slam-point-consumer-preparation-evidence.json").write_text(json.dumps(prepared))
        (args.preparation / "point-consumer-arm64.tar.gz").write_bytes(b"synthetic")
        prepared["archiveSha256"] = coordinator.sha256(args.preparation / "point-consumer-arm64.tar.gz")
        (docs / "slam-point-consumer-preparation-evidence.json").write_text(json.dumps(prepared))
        source = root / "tools/synthetic.py"
        source.parent.mkdir()
        source.write_bytes(b"# synthetic source, not executable\n")
        sources = {"tools/synthetic.py": {"sha256": coordinator.sha256(source), "bytes": source.stat().st_size}}
        home = PurePosixPath("/home/synthetic")
        values = coordinator.paths(home, args)
        sftp = MemorySftp(home, values[3])
        sftp.get_channel = Mock()
        for path in (values[2], values[4]):
            sftp.mkdir(str(path), mode=0o700)
        if existing_remote:
            sftp.mkdir(str(values[1]), mode=0o700)
        client = Mock()
        client.open_sftp.return_value = sftp
        preflight = {
            "uid": 1000, "home": str(home), "machine": "aarch64", "python": "3.10.12",
            "libc": ["glibc", "2.35"], "freeDiskBytes": 10**10,
            "coreManifestSha256": "0" * 64 if bad_preflight else args.core_manifest_sha256,
            "replayManifestSha256": args.replay_manifest_sha256,
            "pointManifestSha256": args.point_manifest_sha256,
        }
        extraction = {
            "result": extractor.RESULT, "archiveSha256": prepared["archiveSha256"],
            "manifestSha256": prepared["manifestSha256"], "bundleFiles": prepared["bundleFiles"],
            "nodeExecuted": False,
        }
        node = {"machine": "x64" if bad_node else "arm64",
                "node": "v" + coordinator.binding.consumer_bundle.VERSION, "platform": "linux"}
        commands = []

        def execute(_client, command):
            commands.append(command)
            if len(commands) <= 3:
                return (13 if failed_extraction and len(commands) == 2 else 0), json.dumps(
                    (preflight, extraction, node)[len(commands) - 1]).encode()
            for name in coordinator.FILES[:2] if fail else coordinator.FILES:
                destination = str(values[-1] / name)
                sftp.files[destination], sftp.modes[destination] = b'{"synthetic":true}', 0o600
            return (7 if fail else 0), b"synthetic native command output"

        original_get = sftp.get

        def get(source, destination):
            original_get(source, destination)
            if changed_download:
                Path(destination).write_bytes(b"changed synthetic download")

        sftp.get = get
        with patch.object(coordinator, "ROOT", root), \
                patch.object(coordinator, "source_files", return_value=sources), \
                patch.object(coordinator.preparation, "evidence", return_value=prepared), \
                patch.object(coordinator.remote, "connect", return_value=client), \
                patch.object(coordinator.remote, "execute", side_effect=execute), \
                patch.object(exporter, "validate_reports") as validate:
            if fail or bad_preflight or changed_download or existing_remote or bad_node or failed_extraction:
                with self.assertRaises((ValueError, RuntimeError)):
                    coordinator.run(args)
                validate.assert_not_called()
            else:
                self.assertEqual(coordinator.RESULT, coordinator.run(args)["result"])
                validate.assert_called_once()
        client.close.assert_called_once()
        sftp.get_channel.return_value.settimeout.assert_called_once_with(30.)
        collection = json.loads((args.output / "collection-report.json").read_text())
        self.assertTrue(collection["connectionClosed"])
        return args, collection, commands, sftp

    def test_complete_collection_checks_sources_archive_runtime_and_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, result, commands, _ = self.case(Path(temporary))
            self.assertEqual(4, len(commands))
            self.assertEqual(10, len(result["filesSha256"]))
            self.assertEqual("arm64", result["nodeRuntime"]["machine"])
            self.assertTrue(result["hostIdentityVerified"])

    def test_failed_remote_native_run_retains_partial_results_without_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, result, commands, _ = self.case(Path(temporary), fail=True)
            self.assertEqual(7, result["exitCode"])
            self.assertEqual(4, len(commands))
            self.assertTrue((args.output / "report.json").exists())
            self.assertFalse((args.output / "consumer-report.json").exists())

    def test_wrong_preserved_input_identity_prevents_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, result, commands, sftp = self.case(Path(temporary), bad_preflight=True)
            self.assertEqual(1, len(commands))
            self.assertEqual({}, sftp.files)
            self.assertNotIn("exitCode", result)

    def test_existing_remote_consumer_is_preserved_before_upload_or_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, commands, sftp = self.case(Path(temporary), existing_remote=True)
            self.assertEqual(1, len(commands))
            self.assertEqual({}, sftp.files)

    def test_changed_download_is_not_accepted_as_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, collection, _, _ = self.case(Path(temporary), changed_download=True)
            self.assertEqual("failed_or_interrupted", collection["result"])

    def test_wrong_node_architecture_stops_before_native_composition(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, collection, commands, _ = self.case(Path(temporary), bad_node=True)
            self.assertEqual(3, len(commands))
            self.assertNotIn("exitCode", collection)

    def test_failed_extraction_stops_before_node_and_preserves_its_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, collection, commands, _ = self.case(Path(temporary), failed_extraction=True)
            self.assertEqual(2, len(commands))
            self.assertNotIn("nodeRuntime", collection)
            self.assertTrue((args.output / "private-extract-command.log").is_file())

    def test_local_output_or_root_user_rejection_precedes_preparation_and_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "preparation").mkdir()
            (root / ".local").mkdir()
            for changes in ({"user": "root"}, {"output": root / "outside"}, {"output": root / ".local"}):
                with self.subTest(changes=changes), patch.object(coordinator, "ROOT", root), \
                        patch.object(coordinator.remote, "connect") as connect, \
                        patch.object(coordinator.preparation, "evidence") as prepared:
                    with self.assertRaises(ValueError):
                        coordinator.run(arguments(root, **changes))
                    connect.assert_not_called()
                    prepared.assert_not_called()

    def test_real_source_inventory_includes_binding_api_transport_and_current_tools(self):
        names = coordinator.source_files()
        self.assertTrue(set(coordinator.binding.source_hashes()).issubset(names))
        for name in ("tools/extract_glim_point_consumer.py", "tools/run_glim_point_composition_target.py",
                     "tools/export_glim_point_composition.py", "robot-api/src/slam-points.ts"):
            self.assertIn(name, names)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = pipe_fixture.PointPipeEvidenceTests(
            "test_complete_evidence_keeps_live_target_ui_and_navigation_unaccepted")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.directory = self.fixture.directory
        self.prepared = coordinator.binding.shared.load(ROOT / "docs/slam-point-consumer-preparation-evidence.json")
        loading = coordinator.binding.shared.load(ROOT / "docs/slam-point-arm64-target-evidence.json")
        self.sources = coordinator.source_files()
        frozen = self.fixture.frozen
        frozen.update(
            sourceSha256=coordinator.binding.target.hashes(),
            pointSourceSha256=coordinator.binding.source_hashes(),
            consumerManifestSha256=self.prepared["manifestSha256"],
            consumerBundleSha256=self.prepared["consumerBundleSha256"],
            nodeBinarySha256=self.prepared["nodeBinarySha256"],
            pointManifestSha256=loading["bundleManifestSha256"],
            coreBundleManifestSha256=loading["coreManifestSha256"], replayManifestSha256="1" * 64,
            pointExtensionLibrarySha256=loading["actualLoadedLibrariesSha256"][coordinator.binding.probe.LIBRARY],
            pointLoadingEvidenceSha256=coordinator.sha256(ROOT / "docs/slam-point-arm64-target-evidence.json"),
            referenceLibrariesSha256=coordinator.binding.shared.load(
                ROOT / "docs/slam-supervised-arm64-evidence.json")["actualLoadedLibrariesSha256"])
        self.fixture.child.update(
            inputIntegrity=deepcopy(frozen), machine="aarch64",
            versions={"python": "3.10.12", "numpy": "0.0.0", "decoder": "installed_humble_rclpy"},
            actualLoadedLibrariesSha256=frozen["referenceLibrariesSha256"])
        self.fixture.native["actualLoadedExtensionSha256"] = {
            coordinator.binding.probe.LIBRARY: frozen["pointExtensionLibrarySha256"]}
        self.fixture.consumer["host"] = {"machine": "arm64", "node": "v" + coordinator.binding.consumer_bundle.VERSION}
        self.fixture.parent.update(
            result=coordinator.binding.RESULT, inputIntegrity=deepcopy(frozen),
            runtimeSourceFilesSha256={name: entry["sha256"] for name, entry in self.sources.items()},
            pacedRecordedInputNotLivePerformance=True)
        for name in (*coordinator.FILES[4:], *exporter.PRIVATE_LOGS):
            (self.directory / name).write_bytes(b"PRIVATE_MARKER synthetic log")
        self.collection = {
            "schemaVersion": 1, "result": coordinator.RESULT, "exitCode": 0,
            "requestedReplayRate": 1., "requestedTimingHypothesis": "header_is_scan_start",
            "coordinatorSha256": coordinator.sha256(coordinator.__file__),
            "preparationEvidenceSha256": coordinator.sha256(ROOT / "docs/slam-point-consumer-preparation-evidence.json"),
            "operatorConfirmedForThisExecution": True, "hostIdentityVerified": True, "connectionClosed": True,
            "uploadedSourceFiles": self.sources,
            "uploadedArchive": {"sha256": self.prepared["archiveSha256"], "bytes": self.prepared["archiveBytes"]},
            "consumerExtraction": {
                "result": extractor.RESULT, "archiveSha256": self.prepared["archiveSha256"],
                "manifestSha256": self.prepared["manifestSha256"], "bundleFiles": self.prepared["bundleFiles"],
                "nodeExecuted": False},
            "nodeRuntime": {**self.fixture.consumer["host"], "platform": "linux"},
            "readOnlyPreflight": {
                "uid": 1000, "home": "/home/synthetic", "libc": ["glibc", "2.35"], "freeDiskBytes": 10**10,
                "coreManifestSha256": frozen["coreBundleManifestSha256"],
                "replayManifestSha256": frozen["replayManifestSha256"],
                "pointManifestSha256": frozen["pointManifestSha256"], "python": "3.10.12", "machine": "aarch64"},
        }
        self.write_reports()

    def write_reports(self):
        self.fixture.write_reports()
        self.fixture.parent["privateReportsSha256"] = {
            name: coordinator.sha256(self.directory / name) for name in coordinator.FILES[1:]}
        (self.directory / "report.json").write_text(json.dumps(self.fixture.parent), encoding="utf-8")
        self.collection["filesSha256"] = {
            name: coordinator.sha256(self.directory / name) for name in (*coordinator.FILES, *exporter.PRIVATE_LOGS)}
        (self.directory / "collection-report.json").write_text(json.dumps(self.collection), encoding="utf-8")

    def evidence(self):
        with patch.object(coordinator.preparation, "evidence", return_value=self.prepared):
            return exporter.evidence(self.directory, Path("unused-synthetic-preparation"))

    def test_only_named_aggregates_are_exported_and_acceptance_remains_false(self):
        self.fixture.parent["PRIVATE_MARKER"] = "/private/recording"
        self.fixture.child["worker"]["counts"]["PRIVATE_MARKER"] = "private"
        self.fixture.native["PRIVATE_MARKER"] = [1, 2, 3]
        self.write_reports()
        result = self.evidence()
        text = json.dumps(result)
        self.assertNotIn("PRIVATE_MARKER", text)
        self.assertNotIn("/private/recording", text)
        self.assertNotIn(self.fixture.child["session"], text)
        self.assertEqual(1, result["points"]["packets"])
        self.assertFalse(any(result["acceptance"].values()))
        self.assertEqual(result, self.evidence())

    def test_collection_requires_identity_confirmation_closed_connection_and_actual_node(self):
        original = deepcopy(self.collection)
        for key, value in (("connectionClosed", False), ("hostIdentityVerified", False),
                           ("operatorConfirmedForThisExecution", 1), ("exitCode", False),
                           ("requestedReplayRate", .5),
                           ("nodeRuntime", {"machine": "x64", "node": "v22.23.4", "platform": "linux"}),
                           ("uploadedSourceFiles", {})):
            self.collection = {**deepcopy(original), key: value}
            self.write_reports()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.evidence()

    def test_matching_point_counts_cannot_hide_forced_native_or_consumer_exit(self):
        original = deepcopy(self.fixture.parent)
        for section, key, value in (
            ("process", "forcedTermination", True), ("process", "cleanupComplete", False),
            ("consumerProcess", "forcedTermination", True), ("consumerProcess", "exitCode", False),
        ):
            self.fixture.parent = deepcopy(original)
            self.fixture.parent[section][key] = value
            self.write_reports()
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                self.evidence()

    def test_old_or_x64_or_wrong_session_callback_reports_are_rejected(self):
        original = deepcopy(self.fixture.child)
        for key, value in (("machine", "x86_64"), ("session", "0" * 32), ("replayRate", .5),
                           ("versions", {"python": "3.10.12", "decoder": "other", "numpy": "0.0.0"})):
            self.fixture.child = {**deepcopy(original), key: value}
            self.write_reports()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.evidence()

    def test_changed_packet_digest_incomplete_delivery_or_false_readiness_is_rejected(self):
        modifications = (
            ("consumer", ("points", "allPacketBytesSha256"), "9" * 64),
            ("consumer", ("api", "recordedInputRejectedAsLive"), False),
            ("native", ("writer", "replaced"), 1), ("native", ("writer", "threadAlive"), True),
            ("parent", ("process", "navigationReady"), True),
        )
        originals = {name: deepcopy(getattr(self.fixture, name)) for name in ("parent", "consumer", "native")}
        for section, path, value in modifications:
            for name, original in originals.items():
                setattr(self.fixture, name, deepcopy(original))
            target = getattr(self.fixture, section)
            target[path[0]][path[1]] = value
            self.write_reports()
            with self.subTest(section=section, path=path), self.assertRaises(ValueError):
                self.evidence()

    def test_changed_downloaded_log_bytes_fail_independent_verification(self):
        (self.directory / "private-consumer.log").write_bytes(b"changed synthetic log")
        with self.assertRaises(ValueError):
            self.evidence()


class PublishedArm64ResultTests(unittest.TestCase):
    def result(self):
        return coordinator.binding.shared.load(ROOT / "docs/slam-point-composition-arm64-evidence.json")

    def test_recorded_result_has_complete_delivery_normal_cleanup_and_no_live_acceptance(self):
        result = self.result()
        self.assertEqual(coordinator.binding.RESULT, result["result"])
        self.assertEqual("recorded", result["inputMode"])
        self.assertEqual("aarch64", result["host"]["machine"])
        self.assertEqual("v22.23.4", result["host"]["node"])
        self.assertEqual(4144, result["inputJournal"]["records"])
        self.assertEqual(4144, result["completedWorkerCounts"]["completedMessages"])
        self.assertEqual(164, result["points"]["packets"])
        self.assertEqual(result["points"]["packets"], result["consumerPoints"]["records"])
        self.assertEqual(1246242, result["points"]["copiedPoints"])
        self.assertEqual(result["points"]["copiedPoints"], result["consumerPoints"]["copiedPoints"])
        self.assertEqual(result["points"]["allPacketBytesSha256"], result["consumerPoints"]["allPacketBytesSha256"])
        self.assertEqual({"sent": 164, "replaced": 0, "failed": False, "threadAlive": False}, result["writer"])
        for section in ("process", "consumerProcess"):
            self.assertEqual(0, result[section]["exitCode"])
            self.assertIs(False, result[section]["processAlive"])
            self.assertIs(False, result[section]["forcedTermination"])
        self.assertTrue(all(result["observations"].values()))
        self.assertFalse(any(result["acceptance"].values()))
        self.assertIs(True, result["api"]["recordedInputRejectedAsLive"])
        self.assertIs(True, result["api"]["readinessUnchanged"])

    def test_published_result_keeps_frozen_sources_and_excludes_private_payloads(self):
        result = self.result()
        self.assertEqual(coordinator.binding.source_hashes(), result["sourceSha256"])
        self.assertEqual(coordinator.sha256(coordinator.__file__), result["coordinatorSha256"])
        self.assertEqual(coordinator.sha256(exporter.__file__), result["exporterSha256"])
        text = json.dumps(result)
        for private in ('"/home/', '"/mnt/', '"session":', '"worldLidarPose":', '"points": [', "10.201.", "192.168."):
            self.assertNotIn(private, text)


if __name__ == "__main__":
    unittest.main()
