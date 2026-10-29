"""Synthetic files, reports and in-memory SSH peers; never contacts a robot."""
import argparse
from copy import deepcopy
import io
import json
from pathlib import Path, PurePosixPath
import shlex
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import export_glim_point_target as exporter
import glim_point_target_probe as probe
import run_glim_point_target as coordinator
from test_slam_point_target import fixture, write_manifest


def synthetic_report(manifest, manifest_hash, core_hash):
    return {
        "schemaVersion": 1, "result": probe.RESULT, "inputClassification": "isolated_synthetic_contracts",
        "bundleManifestSha256": manifest_hash, "coreManifestSha256": core_hash,
        "target": {"machine": "aarch64", "python": "3.10.12", "libc": ["glibc", "2.35"]},
        "actualLoadedLibrariesSha256": {
            **manifest["expectedCoreLibrariesSha256"],
            probe.LIBRARY: manifest["files"]["lib/" + probe.LIBRARY]["sha256"]},
        "emptyAbi": {"open": 1, "close": 1, "callbacks": 0, "copiedPackets": 0},
        "nativeContractExecutionsOnTarget": len(probe.SCENARIOS),
        "stages": [{"scenario": name, "exitCode": 0, "forcedTermination": False,
                    "processAlive": False, "timedOut": False} for name in probe.SCENARIOS],
        "boundaries": dict.fromkeys(probe.BOUNDARIES, False),
    }


class MemorySftp:
    def __init__(self, home, core):
        self.home, self.files, self.directories, self.modes = home, {}, set(), {}
        for path in (core, home / ".cache", home / ".cache/lingxi-next"):
            self.mkdir(str(path), mode=0o700)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def normalize(self, path):
        return str(self.home) if path == "." else path

    def lstat(self, path):
        if path in self.directories:
            return argparse.Namespace(st_mode=stat.S_IFDIR | self.modes[path], st_uid=1000)
        if path not in self.files:
            raise FileNotFoundError(path)
        return argparse.Namespace(st_mode=stat.S_IFREG | self.modes[path], st_size=len(self.files[path]))

    def mkdir(self, path, mode):
        if path in self.directories or path in self.files:
            raise FileExistsError(path)
        self.directories.add(path)
        self.modes[path] = mode

    def put(self, source, destination, confirm):
        assert confirm is True
        self.files[destination] = Path(source).read_bytes()

    def chmod(self, path, mode):
        self.modes[path] = mode

    def open(self, path, mode):
        assert mode == "rb"
        return io.BytesIO(self.files[path])

    def get(self, source, destination):
        Path(destination).write_bytes(self.files[source])


class CoordinatorTests(unittest.TestCase):
    def setup_case(self, root):
        bundle = root / "bundle"
        bundle.mkdir()
        manifest = fixture(bundle)
        manifest_hash = write_manifest(bundle, manifest)
        for name in probe.MEMBERS[2:]:
            source = root / name
            source.parent.mkdir(exist_ok=True)
            source.write_bytes((bundle / name).read_bytes())
        args = argparse.Namespace(
            operator_confirmed=True, output=root / ".local/new-result", user="synthetic",
            host="synthetic.invalid", known_identity="saved.synthetic.invalid",
            directory="lingxi-next-validation-arm64-point-unit",
            core_directory="lingxi-next-validation-arm64-core-unit",
            run_name="glim-point-target-unit", bundle=bundle, manifest_sha256=manifest_hash,
            core_manifest_sha256="d" * 64)
        home = PurePosixPath("/home/synthetic")
        _, core, output = coordinator.paths(home, args)
        sftp = MemorySftp(home, core)
        client = Mock()
        client.open_sftp.return_value = sftp
        preflight = {
            "uid": 1000, "home": str(home), "machine": "aarch64",
            "python": "3.10.12", "libc": ["glibc", "2.35"], "freeDiskBytes": 10**9,
            "coreManifestSha256": args.core_manifest_sha256,
            "productNodeExists": False, "systemNodeExists": False,
        }
        report = synthetic_report(manifest, manifest_hash, args.core_manifest_sha256)
        return args, manifest, client, sftp, preflight, report, output

    def execute_case(self, root, *, failed=False, corrupt=False, bad_preflight=False):
        args, manifest, client, sftp, preflight, report, remote_output = self.setup_case(root)
        if bad_preflight:
            preflight["coreManifestSha256"] = "e" * 64
        if failed:
            report.update(result="failed_or_interrupted", nativeContractExecutionsOnTarget=1)
            report["stages"] = [{**report["stages"][0], "exitCode": 5}]

        def execute(_client, command):
            if " -c " in command:
                return 0, json.dumps(preflight).encode()
            parsed = shlex.split(command)
            self.assertIn("--operator-confirmed", parsed)
            self.assertEqual(args.manifest_sha256, parsed[parsed.index("--manifest-sha256") + 1])
            names = ("report.json", coordinator.FILES[1]) if failed else coordinator.FILES
            for name in names:
                path = str(remote_output / name)
                sftp.files[path] = json.dumps(report).encode() if name == "report.json" else b"synthetic contract log"
                sftp.modes[path] = 0o600
            return (5 if failed else 0), b"explicit synthetic command output"

        original_get = sftp.get

        def get(source, destination):
            original_get(source, destination)
            if corrupt:
                Path(destination).write_bytes(b"changed synthetic download")

        sftp.get = get
        with patch.object(coordinator, "ROOT", root), \
                patch.object(coordinator, "connect", return_value=client) as connect, \
                patch.object(coordinator, "execute", side_effect=execute):
            if failed or corrupt or bad_preflight:
                with self.assertRaises((RuntimeError, ValueError)):
                    coordinator.run(args)
            else:
                self.assertEqual(coordinator.RESULT, coordinator.run(args)["result"])
            connect.assert_called_once_with(args.host, args.user, args.known_identity)
        client.close.assert_called_once()
        collection = json.loads((args.output / "collection-report.json").read_text())
        return args, manifest, collection, sftp

    def test_operator_gate_precedes_source_reads_and_connection(self):
        with patch.object(coordinator, "connect") as connect, patch.object(probe, "verify_bundle") as verify:
            with self.assertRaisesRegex(ValueError, "confirmation"):
                coordinator.run(argparse.Namespace(operator_confirmed=False))
            connect.assert_not_called()
            verify.assert_not_called()

    def test_existing_local_output_never_contacts_robot_or_overwrites_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args, *_ = self.setup_case(root)
            args.output.mkdir(parents=True)
            sentinel = args.output / "preserved"
            sentinel.write_bytes(b"synthetic original")
            with patch.object(coordinator, "ROOT", root), patch.object(coordinator, "connect") as connect:
                with self.assertRaises(ValueError):
                    coordinator.run(args)
            connect.assert_not_called()
            self.assertEqual(b"synthetic original", sentinel.read_bytes())

    def test_result_inside_frozen_bundle_is_rejected_before_modifying_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args, *_ = self.setup_case(root)
            local = root / ".local"
            local.mkdir()
            args.bundle = args.bundle.rename(local / "frozen-bundle")
            args.output = args.bundle / "new-result"
            before = {path.relative_to(args.bundle): path.read_bytes()
                      for path in args.bundle.rglob("*") if path.is_file()}
            with patch.object(coordinator, "ROOT", root), patch.object(coordinator, "connect") as connect:
                with self.assertRaisesRegex(ValueError, "overlap"):
                    coordinator.run(args)
            connect.assert_not_called()
            self.assertFalse(args.output.exists())
            self.assertEqual(before, {path.relative_to(args.bundle): path.read_bytes()
                                      for path in args.bundle.rglob("*") if path.is_file()})

    def test_five_files_are_hash_checked_and_all_outputs_collected_and_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, _, collection, _ = self.execute_case(Path(temporary))
            self.assertEqual(5, len(collection["uploadedFiles"]))
            self.assertEqual(set(coordinator.FILES) | {coordinator.PRIVATE_COMMAND_LOG},
                             set(collection["filesSha256"]))
            self.assertTrue(collection["connectionClosed"])
            self.assertTrue(collection["hostIdentityVerified"])
            self.assertEqual(11, json.loads((args.output / "report.json").read_text())["nativeContractExecutionsOnTarget"])

    def test_failed_stage_retains_partial_outputs_without_running_another_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, _, collection, _ = self.execute_case(Path(temporary), failed=True)
            self.assertEqual("failed_or_interrupted", collection["result"])
            self.assertEqual(5, collection["exitCode"])
            self.assertTrue((args.output / "report.json").is_file())
            self.assertEqual(3, len(collection["filesSha256"]))
            self.assertTrue(collection["connectionClosed"])

    def test_changed_download_fails_collection_and_closes_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, collection, _ = self.execute_case(Path(temporary), corrupt=True)
            self.assertEqual("failed_or_interrupted", collection["result"])
            self.assertTrue(collection["connectionClosed"])

    def test_wrong_core_identity_stops_before_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, collection, sftp = self.execute_case(Path(temporary), bad_preflight=True)
            self.assertEqual({}, collection["uploadedFiles"])
            self.assertEqual({}, sftp.files)
            self.assertNotIn("exitCode", collection)

    def test_command_has_clean_low_priority_no_ros_environment_and_bounded_timeout(self):
        home = PurePosixPath("/home/synthetic")
        bundle, core, output = home / "bundle", home / "core", home / ".cache/lingxi-next/point"
        tokens = shlex.split(coordinator.command(bundle, core, output))
        self.assertEqual(["timeout", "--signal=TERM", "--kill-after=5s", "120s",
                          "/usr/bin/nice", "-n", "10", "env", "-i"], tokens[:9])
        self.assertIn("LD_LIBRARY_PATH=/home/synthetic/bundle/lib:/home/synthetic/core/lib", tokens)
        self.assertNotIn("HOME=/home/synthetic", tokens)
        self.assertFalse(any("ROS" in value or "PRELOAD" in value or "PASSWORD" in value for value in tokens))

    def test_paths_cannot_reuse_core_escape_home_or_accept_arbitrary_run_name(self):
        home = PurePosixPath("/home/synthetic")
        for directory, core, run in (
            ("../escape", "lingxi-next-validation-arm64-core", "glim-point-target-unit"),
            ("lingxi-next-validation-arm64-same", "lingxi-next-validation-arm64-same", "glim-point-target-unit"),
            ("lingxi-next-validation-arm64-point", "lingxi-next-validation-arm64-core", "../escape"),
        ):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                coordinator.paths(home, argparse.Namespace(directory=directory, core_directory=core, run_name=run))

    def test_remote_file_bound_directory_ownership_and_existing_path_checks(self):
        home = PurePosixPath("/home/synthetic")
        sftp = MemorySftp(home, home / "core")
        path = home / "synthetic"
        sftp.files[str(path)], sftp.modes[str(path)] = b"synthetic", 0o600
        with self.assertRaises(ValueError):
            coordinator.remote_digest(sftp, path, maximum=2)
        with self.assertRaises(ValueError):
            coordinator.require_absent(sftp, path)
        coordinator.require_absent(sftp, home / "absent")
        sftp.modes[str(home / "core")] = 0o755
        with self.assertRaises(ValueError):
            coordinator.private_directory(sftp, home / "core", 1000)

    def test_export_only_bounded_aggregates_and_reject_mutated_accounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, _, collection, _ = self.execute_case(Path(temporary))
            result = exporter.evidence(args.output, args.bundle, args.manifest_sha256)
            text = json.dumps(result)
            self.assertNotIn("/home/synthetic", text)
            self.assertNotIn("synthetic command output", text)
            self.assertNotIn("synthetic contract log", text)
            self.assertEqual(11, result["nativeContractExecutionsOnTarget"])
            self.assertFalse(any(result["boundaries"].values()))
            self.assertFalse(any(result["acceptance"].values()))
            self.assertFalse(result["observations"]["nodeRuntimeExecutedOrValidated"])
            for key, value in (("connectionClosed", False), ("hostIdentityVerified", False),
                               ("operatorConfirmedForThisExecution", False), ("exitCode", False)):
                mutated = {**collection, key: value}
                (args.output / "collection-report.json").write_text(json.dumps(mutated))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    exporter.evidence(args.output, args.bundle, args.manifest_sha256)

    def test_export_rejects_changed_log_and_upload_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, _, collection, _ = self.execute_case(Path(temporary))
            name = coordinator.FILES[1]
            original = (args.output / name).read_bytes()
            (args.output / name).write_bytes(b"mutated synthetic log")
            with self.assertRaises(ValueError):
                exporter.evidence(args.output, args.bundle, args.manifest_sha256)
            (args.output / name).write_bytes(original)
            collection["uploadedFiles"]["point-manifest.json"]["mode"] = 0o777
            (args.output / "collection-report.json").write_text(json.dumps(collection))
            with self.assertRaises(ValueError):
                exporter.evidence(args.output, args.bundle, args.manifest_sha256)

    def test_report_rejects_incomplete_forced_wrong_library_or_false_readiness(self):
        with tempfile.TemporaryDirectory() as temporary:
            args, manifest, _, _, _, original, _ = self.setup_case(Path(temporary))
            for mutate in (
                lambda report: report["stages"].pop(),
                lambda report: report["stages"][0].update(forcedTermination=True),
                lambda report: report["stages"][0].update(exitCode=False),
                lambda report: report.update(nativeContractExecutionsOnTarget=True),
                lambda report: report["emptyAbi"].update(callbacks=False),
                lambda report: report["boundaries"].update(mappingReady=True),
                lambda report: report["actualLoadedLibrariesSha256"].update({probe.LIBRARY: "e" * 64}),
                lambda report: report["target"].update(machine="x86_64"),
            ):
                report = deepcopy(original)
                mutate(report)
                with self.assertRaises(ValueError):
                    coordinator.validate_report(report, manifest, args.manifest_sha256, args.core_manifest_sha256)


if __name__ == "__main__":
    unittest.main()
