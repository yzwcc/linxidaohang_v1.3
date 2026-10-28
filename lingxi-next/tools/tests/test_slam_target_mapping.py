"""Synthetic ARM64 mapping contracts; no network, robot control, or real map input."""
import ast
import copy
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shlex
import stat
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).parents[2]
try:
    import numpy as np
except ModuleNotFoundError:
    np = None


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec.loader.exec_module(module)
    return module


launcher = load("run_glim_target_replays")
if np is not None and sys.platform == "linux":
    mapping = load("glim_target_mapping")
    builder = load("build_glim_target_replay")
    historical = load("export_glim_target_replay_evidence")
    exporter = load("export_glim_target_mapping_evidence")


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy")
class TargetMappingEvidence(unittest.TestCase):
    def fixture(self, directory):
        local = Path(directory)
        root = local / "map"
        root.mkdir()
        for name in ("graph.bin", "values.bin"):
            (root / name).write_bytes(b"isolated-synthetic-not-gtsam")
        (root / "graph.txt").write_text("num_submaps: 1\nnum_all_frames: 2\nnum_matching_cost_factors: 0\n")
        for name in ("odom_lidar.txt", "traj_lidar.txt", "odom_imu.txt", "traj_imu.txt"):
            (root / name).write_text("1000 0 0 0 0 0 0 1\n1001 1 0 0 0 0 0 1\n")
        (root / "000000").mkdir()
        (root / "000000/data.txt").write_text("isolated-synthetic-not-a-real-submap\n")
        points = np.array([[1.12345678, 2, 3], [4, 5, 6]], dtype="<f8")
        points.tofile(local / "private-saved-points.f64")
        points.astype(np.float32).astype("<f8").tofile(local / "private-reloaded-points.f64")
        comparison = exporter.compare_points(
            local / "private-saved-points.f64", local / "private-reloaded-points.f64", 2,
        )
        inventory = exporter.inventory_map(root)
        counts = {"finalizedFramesFedOnce": 2, "submaps": 1, "points": 2,
                  "savedFrames": 2, "minimumLoopTravel": 50.0, "submapTravel": 0.0}
        mapped = {"mapping": counts, "mapInventory": inventory, "roundTrip": comparison,
                  "replayPid": 101, "totalWallSecondsIncludingReload": 1.0}
        base = {"mapping": counts, "estimatedPoses": 2}
        reloaded = {
            "mapInventory": inventory, "comparison": comparison, "pid": 202, "machine": "aarch64",
            "loadedSubmaps": 1, "loadedGraphFactors": 1, "loadedGraphValues": 1,
            "result": "fresh_process_arm64_map_round_trip_passed_not_relocalization",
            "boundaries": {name: False for name in exporter.BOUNDARIES},
        }
        return mapped, base, reloaded, local

    def test_complete_map_and_all_points_are_recomputed(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(directory)
            exporter.verify_mapping_round_trip(*args)
            self.assertFalse(args[0]["roundTrip"]["geometricAccuracyValidated"])
            self.assertGreater(args[0]["roundTrip"]["maximumPointDisplacementRawUnits"], 0)

    def test_same_process_reload_or_wrong_graph_counts_cannot_pass(self):
        for field, value in (("pid", 101), ("loadedGraphFactors", 2), ("machine", "x86_64")):
            with tempfile.TemporaryDirectory() as directory:
                mapped, base, reloaded, local = self.fixture(directory)
                reloaded[field] = value
                with self.assertRaisesRegex(ValueError, "independent_arm64"):
                    exporter.verify_mapping_round_trip(mapped, base, reloaded, local)

    def test_changed_map_file_is_not_a_clean_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(directory)
            (args[3] / "map/000000/data.txt").write_text("modified-isolated-synthetic-content")
            with self.assertRaisesRegex(ValueError, "round_trip_mismatch"):
                exporter.verify_mapping_round_trip(*args)

    def test_changed_point_map_or_claimed_tolerance_cannot_pass(self):
        for change in ("points", "tolerance"):
            with tempfile.TemporaryDirectory() as directory:
                mapped, base, reloaded, local = self.fixture(directory)
                if change == "points":
                    np.zeros((2, 3), dtype="<f8").tofile(local / "private-reloaded-points.f64")
                else:
                    mapped = copy.deepcopy(mapped)
                    mapped["roundTrip"]["predeclaredToleranceRawUnits"] = 1.0
                with self.assertRaises(ValueError):
                    exporter.verify_mapping_round_trip(mapped, base, reloaded, local)

    def test_every_nested_public_field_is_explicitly_whitelisted(self):
        mapped = {
            "dataset": "synthetic", "timingHypothesis": "synthetic", "totalWallSecondsIncludingReload": 1,
            "target": {name: 1 for name in ("machine", "python", "numpy")},
            "mapping": {name: 1 for name in exporter.MAPPING_FIELDS},
            "mapInventory": {name: 1 for name in exporter.INVENTORY_FIELDS},
            "roundTrip": {name: 1 for name in exporter.ROUND_TRIP_FIELDS},
            "checks": {name: True for name in exporter.CHECKS},
            "boundaries": {name: False for name in exporter.BOUNDARIES},
        }
        aggregate = {
            "counts": {name: 1 for name in exporter.COUNTS},
            "timingsNotRealtimeAcceptance": {name: 1 for name in exporter.TIMING_FIELDS},
            "estimatedMotionNotGroundTruth": {name: 1 for name in exporter.MOTION_FIELDS},
        }
        for value in list(mapped.values()) + list(aggregate.values()):
            if isinstance(value, dict):
                value["SYNTHETIC_PRIVATE_MARKER"] = [[1, 2, 3]]
        mapped["privateSensorProfile"] = "SYNTHETIC_PRIVATE_MARKER"
        result = json.dumps(exporter.public_run(mapped, aggregate))
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", result)
        self.assertNotIn("privateSensorProfile", result)


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy")
class TargetMappingRuntime(unittest.TestCase):
    def fixture(self):
        return {
            "finalizedFramesFedOnce": 5, "submaps": 2, "points": 100,
            "savedFrames": 5, "minimumLoopTravel": 50.0, "submapTravel": 2.0,
        }

    def test_every_initialized_frame_is_saved_once_without_claiming_loop_closure(self):
        mapping.validate_mapping_counts(self.fixture(), 5)
        for name in ("finalizedFramesFedOnce", "savedFrames"):
            changed = self.fixture()
            changed[name] += 1
            with self.assertRaisesRegex(ValueError, "once_only"):
                mapping.validate_mapping_counts(changed, 5)

    def test_empty_fractional_and_nonfinite_counters_are_rejected(self):
        for name, value in (
            ("points", 0), ("submaps", 1.5), ("savedFrames", True),
            ("minimumLoopTravel", float("inf")), ("submapTravel", float("nan")),
        ):
            changed = self.fixture()
            changed[name] = value
            with self.assertRaises(ValueError):
                mapping.validate_mapping_counts(changed, 5)

    def test_short_persistence_trial_does_not_silently_cover_a_loop_search(self):
        for value in (50, 51, -1):
            changed = self.fixture()
            changed["submapTravel"] = value
            with self.assertRaisesRegex(ValueError, "short_sequence"):
                mapping.validate_mapping_counts(changed, 5)

    def test_full_mapping_bundle_has_closed_local_python_dependencies(self):
        names = builder.PYTHON_SOURCES + ("glim_target_mapping.py", "run_glim_mapping_trial.py")
        builder.validate_python_sources(ROOT / "tools", names)
        with self.assertRaisesRegex(ValueError, "missing_private_bundle_dependency"):
            builder.validate_python_sources(
                ROOT / "tools", tuple(name for name in names if name != "run_glim_mapping_trial.py"),
            )

    def test_mapping_build_requires_explicit_matching_replay_bundle_selection(self):
        for args in (NS(mapping=True, replay_bundle=None),
                     NS(mapping=False, replay_bundle=Path("/isolated-synthetic-not-a-real-bundle"))):
            with self.assertRaisesRegex(ValueError, "explicit_verified_odometry_bundle"):
                builder.prepare(args)

    def test_historical_target_runner_is_pinned_to_the_original_bundle_not_rewritten(self):
        bundle = ROOT / ".local/glim-target-replay-bundle-20261028-c"
        if not bundle.exists():
            self.skipTest("private historical snapshot absent")
        self.assertEqual(historical.RECORDED_ODOMETRY_RUNNER_SHA256,
                         historical.sha256(bundle / "tools/glim_target_replay.py"))
        self.assertNotEqual(historical.RECORDED_ODOMETRY_RUNNER_SHA256,
                            historical.sha256(ROOT / "tools/glim_target_replay.py"))

    def test_completed_mapping_outputs_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "preserved"):
                mapping.run(NS(output=Path(directory)))


class TargetMappingBoundary(unittest.TestCase):
    def test_public_target_evidence_retains_four_complete_unaccepted_map_round_trips(self):
        evidence = json.loads((ROOT / "docs/slam-arm64-mapping-evidence.json").read_text())
        self.assertEqual({entry[0] for entry in launcher.RUNS}, set(evidence["runs"]))
        self.assertTrue(evidence["originalCapturesUnchangedAtExport"])
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        for label, dataset, hypothesis in launcher.RUNS:
            run = evidence["runs"][label]
            self.assertEqual((dataset, hypothesis), (run["dataset"], run["timingHypothesis"]))
            self.assertEqual("aarch64", run["target"]["machine"])
            self.assertTrue(run["reloadedInDifferentArm64Process"])
            self.assertTrue(all(value is True for value in run["checks"].values()))
            self.assertTrue(all(value is False for value in run["boundaries"].values()))
            self.assertEqual(run["mapping"]["savedFrames"], run["scanAccounting"]["estimatedPoses"])
            self.assertEqual(run["mapping"]["points"], run["roundTrip"]["pointCount"])
            self.assertLess(run["roundTrip"]["maximumPointDisplacementRawUnits"],
                            run["roundTrip"]["predeclaredToleranceRawUnits"])
            self.assertFalse(run["roundTrip"]["geometricAccuracyValidated"])
        host = json.loads((ROOT / "docs/slam-glim-mapping-evidence.json").read_text())
        self.assertEqual(host["adapterBuild"]["sources"], evidence["nativeSourceSha256"])

    def test_public_target_evidence_has_no_unlisted_map_or_connection_fields(self):
        evidence = json.loads((ROOT / "docs/slam-arm64-mapping-evidence.json").read_text())
        self.assertEqual({
            "schemaVersion", "observedDate", "purpose", "result", "sourceCommits",
            "captureManifestSha256", "replayManifestSha256", "odometryReplayManifestSha256",
            "privateCollectionReportSha256", "mappingBuildReportSha256",
            "mappingAdapterSha256", "odometryAdapterSha256", "nativeSourceSha256",
            "actualTargetSourcesSha256", "coordinatorSourceSha256", "toolSourcesAtExport",
            "runs", "originalCapturesUnchangedAtExport", "boundaries",
        }, set(evidence))
        for run in evidence["runs"].values():
            self.assertEqual({
                "dataset", "timingHypothesis", "target", "mapping", "mapInventory", "roundTrip",
                "scanAccounting", "timingsWithMappingCallbacksNotRealtimeAcceptance",
                "estimatedMotionNotGroundTruth", "totalWallSecondsIncludingReload", "checks",
                "boundaries", "reloadedInDifferentArm64Process", "privateMappingReportSha256",
                "baseReplayReportSha256", "reloadReportSha256", "privateSavedPointsSha256",
                "privateReloadedPointsSha256", "privateNativeLogSha256", "privateReloadLogSha256",
                "privateTrajectorySha256", "nativeLogsCheckedForErrorsAndRecovery",
            }, set(run))
            self.assertEqual({"machine", "python", "numpy"}, set(run["target"]))

    def test_mapping_command_retains_clean_environment_and_bounded_execution(self):
        home = PurePosixPath("/home/synthetic")
        root = home / "lingxi-next-validation-arm64-mapping-synthetic"
        command = shlex.split(launcher.replay_command(
            root, home / "lingxi-next-validation-arm64-core-synthetic", home,
            "dynamic", "header_is_scan_end", root / "result-dynamic", mapping=True,
        ))
        self.assertEqual(["timeout", "--signal=INT", "--kill-after=10s", "480s", "nice", "-n", "10"], command[:7])
        index = command.index("python3")
        self.assertEqual([str(root / "tools/glim_target_mapping.py"), "run"], command[index + 1:index + 3])
        self.assertIn("AMENT_PREFIX_PATH=/opt/ros/humble", command)
        self.assertIn("OPENBLAS_NUM_THREADS=2", command)

    def test_mapping_source_uses_recorded_replay_and_real_subprocess_reload(self):
        text = (ROOT / "tools/glim_target_mapping.py").read_text()
        ast.parse(text)
        self.assertIn("replay.run(args, native_factory=NativeMapping", text)
        self.assertIn("super().close()", text)
        self.assertIn('self.mapping.call("finish"', text)
        self.assertIn('self.mapping.call("shutdown")', text)
        self.assertIn('str(Path(__file__).resolve()), "reload"', text)
        self.assertIn('reloaded["pid"] == os.getpid()', text)
        self.assertIn("compare_points(", text)
        for token in ("rclpy.init", "create_node", "create_publisher", "create_subscription",
                      "cmd_vel", "McLocomotion", "publish("):
            self.assertNotIn(token, text)

    def test_only_direct_saved_map_files_and_numbered_submaps_are_collected(self):
        sftp = Mock()
        root = PurePosixPath("/synthetic-private/map")
        sftp.listdir_attr.side_effect = lambda path: (
            [NS(filename="000000", st_mode=stat.S_IFDIR),
             NS(filename="graph.bin", st_mode=stat.S_IFREG)]
            if path == str(root) else [NS(filename="frame.bin", st_mode=stat.S_IFREG)]
        )
        sftp.get.side_effect = lambda remote, local: Path(local).write_bytes(b"isolated-synthetic-map")
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "map"
            launcher.download_map(sftp, root, local)
            self.assertEqual(b"isolated-synthetic-map", (local / "000000/frame.bin").read_bytes())
            self.assertEqual(b"isolated-synthetic-map", (local / "graph.bin").read_bytes())

    def test_indirect_or_out_of_tree_map_members_are_not_collected(self):
        for name, mode in (("../outside", stat.S_IFREG), ("link", stat.S_IFLNK),
                           ("unexpected-directory", stat.S_IFDIR)):
            sftp = Mock()
            sftp.listdir_attr.return_value = [NS(filename=name, st_mode=mode)]
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    launcher.download_map(sftp, PurePosixPath("/synthetic/map"), Path(directory) / "map")
            sftp.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
