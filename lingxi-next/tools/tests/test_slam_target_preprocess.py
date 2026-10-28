"""Synthetic ARM64 preprocessing contracts; no robot connection in tests."""
import ast
import hashlib
import json
import importlib.util
import itertools
from pathlib import Path, PurePosixPath
import shlex
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

try:
    import numpy as np
except ModuleNotFoundError:
    np = None

ROOT = Path(__file__).parents[2]


def module(name):
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value


coordinator = module("run_glim_target_preprocess")
if np is not None and sys.platform == "linux":
    target = module("glim_target_preprocess")
    builder = module("build_glim_target_preprocess")
    exporter = module("export_glim_target_preprocess_evidence")


class TargetBoundary(unittest.TestCase):
    def test_twelve_conditions_are_predeclared_and_unique(self):
        self.assertEqual(12, len(coordinator.CONDITIONS))
        self.assertEqual(12, len(set(coordinator.CONDITIONS)))
        for dataset in ("static", "dynamic"):
            for threads in (1, 2):
                self.assertEqual([1, 2, 3], [repeat for kind, count, repeat in coordinator.CONDITIONS
                                           if kind == dataset and count == threads])

    def test_remote_command_is_bounded_isolated_and_only_preprocessing(self):
        home = PurePosixPath("/home/synthetic")
        remote = home / "lingxi-next-validation-arm64-preprocess-synthetic"
        core = home / "lingxi-next-validation-arm64-core-synthetic"
        result = shlex.split(coordinator.command(remote, core, home, "dynamic", 1, remote / "result"))
        self.assertEqual(["timeout", "--signal=INT", "--kill-after=10s", "300s", "nice", "-n", "10"], result[:7])
        self.assertIn("env", result)
        self.assertIn("-i", result)
        for value in ("OMP_NUM_THREADS=2", "OPENBLAS_NUM_THREADS=2", "AMENT_PREFIX_PATH=/opt/ros/humble"):
            self.assertIn(value, result)
        self.assertEqual("1", result[result.index("--threads") + 1])
        self.assertEqual(str(remote / "tools/glim_target_preprocess.py"), result[result.index("python3") + 1])
        for token in ("sudo", "systemctl", "ros2", "cmd_vel", "source"):
            self.assertNotIn(token, result)

    def test_remote_conditions_cannot_escape_explicit_home_directories(self):
        home = PurePosixPath("/home/synthetic")
        root, core = home / "trial", home / "core"
        for dataset, threads, output in (("unknown", 1, root / "result"),
                                         ("static", 3, root / "result"),
                                         ("static", 2, home / "result")):
            with self.assertRaisesRegex(ValueError, "home_scoped"):
                coordinator.command(root, core, home, dataset, threads, output)

    def test_existing_output_and_root_are_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(coordinator, "connect") as connect:
            with self.assertRaisesRegex(ValueError, "new_private"):
                coordinator.run(NS(output=Path(directory), user="run"))
            with self.assertRaisesRegex(ValueError, "ordinary_user"):
                coordinator.run(NS(output=Path(directory) / "new", user="root"))
            connect.assert_not_called()

    def test_target_source_has_no_live_subscription_or_motion_calls(self):
        text = (ROOT / "tools/glim_target_preprocess.py").read_text()
        tree = ast.parse(text)
        calls = [node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        for name in ("init", "create_node", "create_subscription", "create_publisher", "imu"):
            self.assertNotIn(name, calls)
        self.assertIn("from run_glim_preprocess_trial import Native", text)
        self.assertIn("recorded_messages", text)

    def test_existing_host_runtime_and_native_sources_are_not_modified(self):
        import hashlib
        import json
        evidence = json.loads((ROOT / "docs/slam-preprocess-repeatability-evidence.json").read_text())
        self.assertEqual(evidence["runnerSourceSha256"],
                         hashlib.sha256((ROOT / "tools/run_glim_preprocess_trial.py").read_bytes()).hexdigest())
        for name, digest in evidence["adapterSourcesSha256"].items():
            self.assertEqual(digest, hashlib.sha256((ROOT / "tools/glim-preprocess" / name).read_bytes()).hexdigest())

    def test_public_evidence_pins_executed_sources_and_covers_all_pairs(self):
        evidence = json.loads((ROOT / "docs/slam-arm64-preprocess-evidence.json").read_text())
        for field, name in (
            ("runnerSourceSha256", "glim_target_preprocess.py"),
            ("coordinatorSourceSha256", "run_glim_target_preprocess.py"),
            ("exporterSourceSha256", "export_glim_target_preprocess_evidence.py"),
        ):
            self.assertEqual(hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest(), evidence[field])
        self.assertEqual(12, evidence["freshArm64ProcessCount"])
        self.assertEqual(3, evidence["repeatsPerCondition"])
        self.assertTrue(evidence["controlIsNotProductionConfiguration"])
        self.assertTrue(evidence["allOriginalCapturesAndCompletePrivateOutputsUnchangedAtExport"])
        self.assertTrue(all(value is False for value in evidence["boundaries"].values()))
        self.assertEqual({f"{kind}-threads{threads}-repeat{repeat}"
                          for kind, threads, repeat in coordinator.CONDITIONS}, set(evidence["runs"]))
        within, cross = set(), set()
        for dataset in ("static", "dynamic"):
            for threads in (2, 1):
                labels = [f"{dataset}-threads{threads}-repeat{repeat}" for repeat in (1, 2, 3)]
                within.update(first + "_vs_" + second for first, second in itertools.combinations(labels, 2))
                cross.update("arm64-" + first + "_vs_x86-" + second
                             for first, second in itertools.product(labels, labels))
        self.assertEqual(within, set(evidence["withinTargetConditionPairs"]))
        self.assertEqual(cross, set(evidence["crossArchitectureSameConditionPairs"]))
        for group in ("withinTargetConditionPairs", "crossArchitectureSameConditionPairs"):
            for name, value in evidence[group].items():
                self.assertTrue(value["allRawInputContentAndTimesIdentical"])
                self.assertEqual(342 if "static" in name else 1220, value["processedFrames"])
                self.assertEqual(0, value["sameExactXyztMultisetButDifferentOrderFrames"])
                self.assertEqual(0, value["neighborIndexSequenceDifferentFramesAmongIdenticalOrderedXyzt"])
                self.assertEqual(value["orderedXyztBitSequenceDifferentFrames"],
                                 value["exactXyztMultisetDifferentFrames"])
                if "-threads1-" in name:
                    self.assertEqual(0, value["preparedPointCountDifferentFrames"])
                    self.assertEqual(0, value["orderedXyztBitSequenceDifferentFrames"])
                else:
                    self.assertGreater(value["exactXyztMultisetDifferentFrames"], 0)

    def test_public_evidence_contains_only_aggregate_costs_and_no_private_values(self):
        evidence = json.loads((ROOT / "docs/slam-arm64-preprocess-evidence.json").read_text())
        for name, value in evidence["runs"].items():
            self.assertEqual("aarch64", value["hostMachine"])
            self.assertEqual(353 if name.startswith("static") else 1232, value["recordedScans"])
            self.assertEqual(11 if name.startswith("static") else 12, value["skippedUnbracketedScans"])
            timing = value["timing"]
            self.assertTrue(timing["callIncludesNativeWrapperAllocationAndCopy"])
            self.assertTrue(timing["doesNotMeasureLiveEndToEndOrVoiceConcurrency"])
            self.assertGreater(timing["callWallMillisecondsMaximum"], 0)
            self.assertEqual(3, len(timing["callWallMillisecondsP50P95P99"]))
        for token in ('"pid"', '"header_stamps_ns"', '"T_lidar_imu"', '"input_cloud_sha256"',
                      '"password"', '"username"', '"hostname"', '"points"', '"neighbors"'):
            self.assertNotIn(token, json.dumps(evidence))


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy environment")
class TargetSequence(unittest.TestCase):
    def message(self, stamp, frame):
        return NS(header=NS(stamp=NS(sec=stamp // 1_000_000_000, nanosec=stamp % 1_000_000_000),
                            frame_id=frame))

    def fixture(self, topic):
        if topic == target.IMU:
            return [self.message(value, "lidar_imu_chest_front")
                    for value in (1_000_000_000, 1_100_000_000, 1_200_000_000, 1_300_000_000)]
        return [self.message(value, "lidar_chest_front")
                for value in (990_000_000, 1_100_000_000, 1_200_000_000, 1_400_000_000)]

    def process(self, messages=None, dropped=0):
        calls = []

        def frame(stamp, points, offsets):
            calls.append(stamp)
            return np.column_stack((points, offsets)), np.zeros((len(points), 10), dtype="<i4")

        with tempfile.TemporaryFile() as points, tempfile.TemporaryFile() as neighbors:
            with patch.object(target, "cloud_arrays", return_value=(
                    np.array([[1., 2., 3.], [4., 5., 6.]]), np.array([0., 0.02]), dropped)):
                result = target.process_recording(
                    Path("/synthetic-not-real"), {"topicCounts": {"cloud": 4, "imu": 4}},
                    NS(frame=frame), points, neighbors,
                    messages=messages or (lambda bag, topic: self.fixture(topic)),
                )
            return result, calls, points.tell(), neighbors.tell()

    def test_same_scan_end_gating_accounts_for_all_input_and_only_reads_imu_times(self):
        (summary, index, metrics), calls, point_bytes, neighbor_bytes = self.process()
        self.assertEqual(4, summary["recordedScans"])
        self.assertEqual(2, summary["processedScans"])
        self.assertEqual(2, summary["skippedUnbracketedScans"])
        np.testing.assert_array_equal(index["header_stamps_ns"], [1_100_000_000, 1_200_000_000])
        np.testing.assert_array_equal(index["counts"], [[2, 2], [2, 2]])
        np.testing.assert_allclose(calls, [1000.08, 1000.18], rtol=0, atol=1e-12)
        self.assertEqual(4 * 4 * 8, point_bytes)
        self.assertEqual(4 * 10 * 4, neighbor_bytes)
        self.assertTrue(np.all(metrics["call_wall_ns"] > 0))
        self.assertTrue(np.all(metrics["call_cpu_ns"] >= 0))
        self.assertEqual(index["input_cloud_sha256"][0], index["input_cloud_sha256"][1])

    def test_incomplete_or_non_increasing_imu_is_not_processed(self):
        def messages(bag, topic):
            values = self.fixture(topic)
            return values[:-1] if topic == target.IMU else values

        with self.assertRaisesRegex(ValueError, "complete_ordered_recorded_imu"):
            self.process(messages)

        def unordered(bag, topic):
            values = self.fixture(topic)
            return values[::-1] if topic == target.IMU else values

        with self.assertRaisesRegex(ValueError, "complete_ordered_recorded_imu"):
            self.process(unordered)

    def test_non_finite_clouds_are_not_silently_discarded(self):
        with self.assertRaisesRegex(ValueError, "complete_finite_recorded_cloud"):
            self.process(dropped=1)

    def test_wrong_lidar_frame_is_rejected(self):
        def messages(bag, topic):
            values = self.fixture(topic)
            if topic == target.CLOUD:
                values[1].header.frame_id = "synthetic_wrong_frame"
            return values

        with self.assertRaisesRegex(ValueError, "ordered_recorded_lidar"):
            self.process(messages)

    def test_staged_python_sources_include_the_complete_import_closure(self):
        builder.validate_python_sources(ROOT / "tools", builder.SOURCES)
        with self.assertRaisesRegex(ValueError, "missing_private_bundle_dependency"):
            builder.validate_python_sources(ROOT / "tools",
                                            tuple(name for name in builder.SOURCES if name != "slam_preflight.py"))

    def test_actual_loaded_libraries_must_match_verified_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root, core = Path(directory) / "trial", Path(directory) / "core"
            (root / "lib").mkdir(parents=True)
            (core / "lib").mkdir(parents=True)
            names = ["libglim.so", "libgtsam_points.so", "libgtsam_points.so.1"]
            for name in names:
                (core / "lib" / name).write_bytes(b"synthetic library bytes, not executable")
            (root / "lib/liblingxi_glim_preprocess.so").write_bytes(b"synthetic")
            manifest = {"files": {f"lib/{name}": {} for name in names}}
            paths = [core / "lib" / name for name in names[:2]] + [root / "lib/liblingxi_glim_preprocess.so"]
            text = "\n".join(f"00-01 r-xp 000 00:00 1 {path}" for path in paths)
            self.assertEqual(3, len(target.loaded_libraries(root, core, manifest, text)))
            versioned = text.replace("libgtsam_points.so", "libgtsam_points.so.1")
            self.assertEqual(3, len(target.loaded_libraries(root, core, manifest, versioned)))
            with self.assertRaisesRegex(ValueError, "outside_verified"):
                target.loaded_libraries(root, core, manifest, versioned.replace(str(core), str(root)))
            with self.assertRaisesRegex(ValueError, "actual_native"):
                target.loaded_libraries(root, core, manifest, "")

    def test_preprocessor_run_cannot_reuse_an_existing_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(target, "validate_environment"):
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "new_private_predeclared"):
                target.run(NS(root=root, core_root=root, output=root, threads=2))


@unittest.skipIf(np is None or sys.platform != "linux", "requires isolated Linux NumPy environment")
class TargetEvidence(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        bundle, path = root / "bundle", root / "result"
        (bundle / "tools").mkdir(parents=True)
        path.mkdir()
        (bundle / "tools/glim_target_preprocess.py").write_text("synthetic not executable\n")
        (bundle / "preprocess-manifest.json").write_text("{}")
        manifest = {"files": {
            "capture-manifest.json": {"sha256": "a" * 64},
            "config/trial-manifest.json": {"sha256": "b" * 64},
            "lib/liblingxi_glim_preprocess.so": {"sha256": "c" * 64},
        }}
        index = {
            "counts": np.array([[12000, 50], [12000, 50]], dtype=np.int64),
            "header_stamps_ns": np.array([1000, 2000], dtype=np.int64),
            "algorithm_stamps_relative_seconds": np.array([1000., 1001.]),
            "input_cloud_sha256": np.array(["a" * 64, "b" * 64]),
        }
        np.savez_compressed(path / "private-index.npz", **index)
        points = np.zeros((100, 4), dtype="<f8")
        points[:, 0] = 1
        points.tofile(path / "private-prepared-xyzt.f64")
        np.zeros((100, 10), dtype="<i4").tofile(path / "private-neighbors.i32")
        np.savez_compressed(path / "private-metrics.npz",
                            call_wall_ns=np.array([1_000_000, 2_000_000], dtype=np.int64),
                            call_cpu_ns=np.array([500_000, 800_000], dtype=np.int64))
        (path / "private-native.log").write_text("synthetic fixture only\n")
        report = {
            "purpose": target.PURPOSE,
            "result": "complete_arm64_native_preprocessing_not_slam_acceptance",
            "dataset": "static", "diagnosticThreads": 2, "hostMachine": "aarch64", "pid": 7,
            "runnerSourceSha256": target.sha256(bundle / "tools/glim_target_preprocess.py"),
            "bundleManifestSha256": target.sha256(bundle / "preprocess-manifest.json"),
            "captureManifestSha256": "a" * 64, "configManifestSha256": "b" * 64,
            "nativeAdapterSha256": "c" * 64, "originalCapturesUnchanged": True,
            "boundaries": {name: False for name in target.BOUNDARIES},
            "processedScans": 2, "recordedScans": 3, "skippedUnbracketedScans": 1,
            "totalPreparedPoints": 100,
            "processingWallSeconds": 0.1, "processingCpuSeconds": 0.2,
            "totalWallSeconds": 0.3, "peakResidentMiB": 10,
        }
        collection = self.write_report(path, report)
        return path, bundle, manifest, report, collection

    def write_report(self, path, report):
        report["filesSha256"] = {name: target.sha256(path / name) for name in target.FILES}
        (path / "report.json").write_text(json.dumps(report))
        return {"exitCode": 0, "reportSha256": target.sha256(path / "report.json")}

    def load(self, path, bundle, manifest, collection):
        return exporter.load_target(path, ("static", 2, 1), collection, manifest, bundle)

    def test_complete_arrays_and_every_frame_cost_are_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path, bundle, manifest, _, collection = self.fixture(directory)
            value = self.load(path, bundle, manifest, collection)
            self.assertEqual((100, 4), value["points"].shape)
            self.assertEqual((100, 10), value["neighbors"].shape)
            self.assertEqual(2, len(value["metrics"]["call_wall_ns"]))
            summary = exporter.timing_summary(value)
            self.assertEqual(2., summary["callWallMillisecondsMaximum"])
            self.assertEqual(.003, summary["completeCallWallSeconds"])
            self.assertTrue(summary["doesNotMeasureLiveEndToEndOrVoiceConcurrency"])

    def test_point_and_metric_file_digest_changes_are_rejected(self):
        for name in ("private-prepared-xyzt.f64", "private-neighbors.i32", "private-metrics.npz"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path, bundle, manifest, _, collection = self.fixture(directory)
                with (path / name).open("ab") as stream:
                    stream.write(b"synthetic mutation")
                with self.assertRaisesRegex(ValueError, "digest_mismatch"):
                    self.load(path, bundle, manifest, collection)

    def test_correctly_hashed_but_invalid_neighbor_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, bundle, manifest, report, _ = self.fixture(directory)
            values = np.zeros((100, 10), dtype="<i4")
            values[0, 0] = 50
            values.tofile(path / "private-neighbors.i32")
            collection = self.write_report(path, report)
            with self.assertRaisesRegex(ValueError, "finite_complete_arm64"):
                self.load(path, bundle, manifest, collection)

    def test_correctly_hashed_nonfinite_or_truncated_points_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, bundle, manifest, report, _ = self.fixture(directory)
            values = np.zeros((100, 4), dtype="<f8")
            values[0, 0] = np.nan
            values.tofile(path / "private-prepared-xyzt.f64")
            collection = self.write_report(path, report)
            with self.assertRaisesRegex(ValueError, "finite_complete_arm64"):
                self.load(path, bundle, manifest, collection)
            values[:-1].tofile(path / "private-prepared-xyzt.f64")
            collection = self.write_report(path, report)
            with self.assertRaisesRegex(ValueError, "whole_arm64"):
                self.load(path, bundle, manifest, collection)

    def test_call_costs_must_cover_every_frame_and_be_integer_nonnegative(self):
        for wall, cpu in (([1], [1]), ([1, 0], [1, 1]), ([1, 2], [1, -1])):
            with self.subTest(wall=wall, cpu=cpu), self.assertRaisesRegex(ValueError, "integer_call_costs"):
                exporter.validate_metrics({"call_wall_ns": np.array(wall, dtype=np.int64),
                                           "call_cpu_ns": np.array(cpu, dtype=np.int64)}, 2)
        with self.assertRaisesRegex(ValueError, "integer_call_costs"):
            exporter.validate_metrics({"call_wall_ns": np.array([1., 2.]),
                                       "call_cpu_ns": np.array([1, 2], dtype=np.int64)}, 2)
        exporter.validate_metrics({"call_wall_ns": np.array([1, 2], dtype=np.int64),
                                   "call_cpu_ns": np.array([0, 0], dtype=np.int64)}, 2)

    def test_claimed_motion_or_wrong_architecture_is_not_a_completed_trial(self):
        for kind in ("architecture", "motion", "captures"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path, bundle, manifest, report, _ = self.fixture(directory)
                if kind == "architecture":
                    report["hostMachine"] = "x86_64"
                elif kind == "motion":
                    report["boundaries"]["motionCommandsSent"] = True
                else:
                    report["originalCapturesUnchanged"] = False
                collection = self.write_report(path, report)
                with self.assertRaisesRegex(ValueError, "actual_complete_arm64"):
                    self.load(path, bundle, manifest, collection)

    def test_failed_native_log_is_not_hidden_by_successful_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path, bundle, manifest, report, _ = self.fixture(directory)
            (path / "private-native.log").write_text("[error] synthetic failure marker\n")
            collection = self.write_report(path, report)
            with self.assertRaisesRegex(ValueError, "failure_in_log"):
                self.load(path, bundle, manifest, collection)

    def test_timing_export_does_not_include_added_private_arrays(self):
        with tempfile.TemporaryDirectory() as directory:
            path, bundle, manifest, _, collection = self.fixture(directory)
            value = self.load(path, bundle, manifest, collection)
            value["privateExtrinsic"] = "synthetic_private_marker"
            text = json.dumps(exporter.timing_summary(value))
            for token in ("synthetic_private_marker", "privateExtrinsic", "header_stamps_ns",
                          "input_cloud_sha256", '"points"', '"neighbors"'):
                self.assertNotIn(token, text)

    def test_loaded_library_export_is_limited_to_verified_providers(self):
        known = {name: "a" * 64 for name in
                 ("libglim.so", "libgtsam_points.so.1", "liblingxi_glim_preprocess.so")}
        self.assertEqual(known, exporter.validate_loaded_libraries(known, known))
        for observed in ({}, {"libglim.so": "a" * 64}, {**known, "unlisted": "b" * 64}):
            with self.assertRaisesRegex(ValueError, "actual_verified"):
                exporter.validate_loaded_libraries(observed, known)


if __name__ == "__main__":
    unittest.main()
