"""Isolated synthetic map contracts, not robot geometry or motion tests."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "json5"))
if AVAILABLE:
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        import refine_saved_map as refine


@unittest.skipUnless(AVAILABLE, "requires isolated scientific Python environment")
class SavedMapRefinement(unittest.TestCase):
    def fixture(self, root, lines=()):
        root.mkdir()
        (root / "graph.bin").write_bytes(b"isolated-synthetic-graph")
        (root / "values.bin").write_bytes(b"isolated-synthetic-values")
        (root / "graph.txt").write_text("num_submaps: 3\nnum_all_frames: 2\nnum_matching_cost_factors: "
                                        + str(len(lines)) + "\n" + "".join(line + "\n" for line in lines))
        for name in ("odom_lidar.txt", "traj_lidar.txt", "odom_imu.txt", "traj_imu.txt"):
            (root / name).write_text("0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 1\n")
        for index in range(3):
            folder = root / f"{index:06d}"
            folder.mkdir()
            (folder / "cloud.bin").write_bytes(b"isolated-synthetic-cloud")
        return root

    def test_inventory_accepts_pose_only_and_explicit_nonadjacent_matching_pairs(self):
        for lines, expected in (((), 0), (("matching_cost vgicp 0 1",), 0),
                                (("matching_cost vgicp 0 2", "matching_cost vgicp 1 2"), 1)):
            with self.subTest(lines=lines), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(Path(directory) / "map", lines)
                result = refine.inventory(root)
                self.assertEqual(expected, result["nonAdjacentMatchingPairs"])
                self.assertEqual(len(lines), result["num_matching_cost_factors"])
                self.assertEqual(3, result["num_submaps"])
                self.assertEqual(result, refine.inventory(root))

    def test_bad_matching_metadata_and_incomplete_submaps_are_not_accepted(self):
        for lines in (("matching_cost vgicp 0 3",), ("matching_cost vgicp 1 0",),
                      ("matching_cost vgicp 0 2", "matching_cost vgicp 0 2"),
                      ("matching_cost vgicp_gpu 0 2",), ("matching_cost vgicp x 2",)):
            with self.subTest(lines=lines), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(Path(directory) / "map", lines)
                with self.assertRaises(ValueError):
                    refine.inventory(root)
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(Path(directory) / "map")
            (root / "000002").rename(root / "000003")
            with self.assertRaisesRegex(ValueError, "numbered_submaps"):
                refine.inventory(root)

    def test_rounding_is_permitted_but_missing_or_invalid_trajectory_is_not(self):
        for content in ("0 0 0 0 0 0 0 1\n", "0 0 0 0 0 0 0 1\n0 1 0 0 0 0 0 1\n",
                        "0 0 0 0 0 0 0 1\n1 1 0 0 0 0 0 2\n",
                        "0 0 0 0 0 0 0 1\n1 nan 0 0 0 0 0 1\n"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = self.fixture(Path(directory) / "map")
                (root / "traj_lidar.txt").write_text(content)
                with self.assertRaises(ValueError):
                    refine.inventory(root)

    def test_saved_configuration_directory_is_part_of_immutable_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.fixture(Path(directory) / "map")
            initial = refine.inventory(root)
            (root / "config").mkdir()
            (root / "config/config.json").write_text('{"classification":"isolated_synthetic"}')
            changed = refine.inventory(root)
            self.assertNotEqual(initial["artifactInventorySha256"], changed["artifactInventorySha256"])
            self.assertEqual(initial["fileCount"] + 1, changed["fileCount"])

    def test_existing_output_and_source_ancestors_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.fixture(root / "map")
            with self.assertRaisesRegex(ValueError, "new_direct"):
                refine.private_output(source)
            with self.assertRaisesRegex(ValueError, "separate"):
                refine.private_output(source / "new", source)
            with self.assertRaisesRegex(ValueError, "private_workspace"):
                refine.private_output(root / "unapproved-new", source)
            self.assertEqual(3, refine.inventory(source)["num_submaps"])

    def test_configuration_uses_upstream_cpu_without_changing_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original"
            original.mkdir()
            (original / "config.json").write_text('{"global":{"config_global_mapping":"pose-only.json"}}')
            initial = (original / "config.json").read_bytes()
            upstream = root / "upstream"
            (upstream / "config").mkdir(parents=True)
            (upstream / "config/config_global_mapping_cpu.json").write_text('{"global_mapping":{}}')
            with patch.object(refine, "verify_source", return_value="isolated-synthetic-commit"):
                result = refine.prepare_config(upstream, original, root / "output")
            self.assertEqual(initial, (original / "config.json").read_bytes())
            self.assertEqual("config_global_mapping_cpu.json",
                             json.loads((root / "output/config.json").read_text())["global"]["config_global_mapping"])
            self.assertFalse(result["effectiveOverrides"]["reloadOptimization"])
            self.assertFalse(result["effectiveOverrides"]["enable_imu"])
            self.assertEqual(refine.configuration_hashes(original), result["originalConfigurationSha256"])
            self.assertEqual(refine.configuration_hashes(root / "output"), result["effectiveConfigurationSha256"])

    def test_invalid_overlap_is_rejected_before_touching_maps_or_starting_native(self):
        for overlap in (0, -1, float("nan"), float("inf"), 1.1, True):
            with self.subTest(overlap=overlap), patch.object(refine.subprocess, "run") as launch:
                with self.assertRaisesRegex(ValueError, "overlap"):
                    refine.run(type("Args", (), {"overlap": overlap})())
                launch.assert_not_called()

    def receipt(self, path, source, points_sha, config):
        value = {"schemaVersion": 2, "session": "a" * 32, "inputMode": "recorded",
                 "counts": {"finalizedFrames": 2, "savedFrames": 2, "submaps": 3, "points": 2},
                 "mapInventory": {key: val for key, val in source.items() if key != "nonAdjacentMatchingPairs"},
                 "savedPointsSha256": points_sha, "freshProcessReloadPassed": True,
                 "sensorProvenance": {"schemaVersion": 1, "configurationSha256": config}}
        path.write_text(json.dumps(value))
        return value

    def test_receipt_binds_graph_points_frames_and_original_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = refine.inventory(self.fixture(root / "map"))
            path, digest, config = root / "receipt.json", "b" * 64, {"config.json": "c" * 64}
            value = self.receipt(path, source, digest, config)
            result = refine.source_receipt(path, source, digest, config)
            self.assertTrue(result["identityBound"])
            self.assertEqual("slam-" + "a" * 32, result["mapId"])
            self.assertFalse(refine.source_receipt(None, source, digest, config)["identityBound"])
            changes = [{**value, "savedPointsSha256": "d" * 64}, {**value, "schemaVersion": True},
                       {**value, "counts": None}, {**value, "counts": {**value["counts"], "savedFrames": 1}},
                       {**value, "counts": {**value["counts"], "points": True}},
                       {**value, "mapInventory": {**value["mapInventory"], "artifactInventorySha256": "e" * 64}},
                       {**value, "sensorProvenance": {"schemaVersion": 1, "configurationSha256": {}}},
                       {**value, "freshProcessReloadPassed": False}, {**value, "session": "../other"}]
            for changed in changes:
                with self.subTest(changed=changed):
                    path.write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError, "identity_mismatch"):
                        refine.source_receipt(path, source, digest, config)

    def test_display_arguments_are_checked_before_native_or_source_access(self):
        for render, band in ((True, None), (True, [1, 0]), (True, [0, float("nan")]),
                             (False, [0, 1])):
            with self.subTest(render=render, band=band), patch.object(refine.subprocess, "run") as launch:
                with self.assertRaisesRegex(ValueError, "display_z_range"):
                    refine.run(SimpleNamespace(overlap=0.5, render_comparison=render, z_range=band))
                launch.assert_not_called()

    def exercise_run(self, root, mutate=None):
        source = self.fixture(root / "source")
        original = root / "original"
        original.mkdir()
        (original / "config.json").write_text('{"global":{"config_global_mapping":"pose-only.json"}}')
        upstream = root / "upstream"
        (upstream / "config").mkdir(parents=True)
        (upstream / "config/config_global_mapping_cpu.json").write_text('{"global_mapping":{}}')
        points = root / "source.f64"
        cloud = refine.np.array([[0., 0., 0.], [1., 0., 0.]])
        cloud.tofile(points)
        adapter = root / "adapter"
        adapter.mkdir()
        (adapter / "build-report.json").write_text('{"classification":"isolated_synthetic"}')
        receipt = root / "receipt.json"
        self.receipt(receipt, refine.inventory(source), refine.sha256(points), refine.configuration_hashes(original))
        output = root / "output"
        args = SimpleNamespace(overlap=0.5, map=source, points=points, adapter=adapter, work=root,
                               upstream=upstream, config=original, output=output, receipt=receipt)
        calls = []

        def native(command, **kwargs):
            stage = len(calls)
            calls.append(command[1])
            exported = cloud if stage == 0 else cloud + 0.01
            exported.tofile(command[5])
            if stage == 1:
                shutil.copytree(source, command[4])
                (Path(command[4]) / "graph.txt").write_text(
                    "num_submaps: 3\nnum_all_frames: 2\nnum_matching_cost_factors: 1\nmatching_cost vgicp 0 2\n")
            report = {"pid": stage + 100, "loadedSubmaps": 3, "points": 2,
                      "refined": stage == 1, "wallSeconds": 0.001, "peakRssKiB": 1024}
            Path(command[6]).write_text(json.dumps(report))
            if mutate and stage == 2:
                if mutate == "native-log":
                    kwargs["stdout"].write("recovering factor graph\n")
                else:
                    target = {"configuration": original / "config.json", "source-points": points,
                              "receipt": receipt}[mutate]
                    target.write_bytes(target.read_bytes() + b" ")
            return SimpleNamespace(returncode=0)

        with patch.object(refine, "private_output", return_value=output), \
                patch.object(refine, "verified_executable", return_value=root / "synthetic-native"), \
                patch.object(refine, "implementation_inputs", return_value={"classification": "isolated_synthetic"}), \
                patch.object(refine, "verify_source", return_value="isolated-synthetic-commit"), \
                patch.object(refine, "library_environment", return_value={}), \
                patch.object(refine.subprocess, "run", side_effect=native):
            result = refine.run(args)
        self.assertEqual(["reload", "refine", "reload"], calls)
        return result, output

    def test_three_process_pipeline_keeps_evidence_separate_from_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            result, output = self.exercise_run(Path(directory))
            self.assertEqual(1, result["refinedMap"]["nonAdjacentMatchingPairs"])
            self.assertTrue(result["sourceReceipt"]["identityBound"])
            self.assertTrue(result["sourceInputsUnchanged"])
            self.assertTrue(all(value is False for value in result["boundaries"].values()))
            self.assertEqual(0, result["refinedRoundTrip"]["maximumPointDisplacementRawUnits"])
            self.assertEqual(result, json.loads((output / "report.json").read_text()))

    def test_changed_source_configuration_points_or_receipt_never_produce_success_report(self):
        for mutation in ("configuration", "source-points", "receipt", "native-log"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(ValueError):
                    self.exercise_run(root, mutation)
                self.assertFalse((root / "output/report.json").exists())

    def test_changed_runner_or_binary_cannot_reuse_a_build_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build").mkdir()
            binary = root / "build/lingxi_glim_refine"
            binary.write_bytes(b"isolated-synthetic-executable-not-executed")
            inputs = {"runnerSources": {"refine_saved_map.py": "a" * 64}}
            value = {"result": "offline_refinement_tool_built_not_geometry_acceptance",
                     "sourceCommits": refine.PINS, **inputs, "executableSha256": refine.sha256(binary)}
            (root / "build-report.json").write_text(json.dumps(value))
            with patch.object(refine, "implementation_inputs", return_value=inputs):
                self.assertEqual(binary, refine.verified_executable(root, root))
                original = binary.read_bytes()
                binary.write_bytes(b"changed-isolated-synthetic-binary")
                with self.assertRaisesRegex(ValueError, "source_bound"):
                    refine.verified_executable(root, root)
                binary.write_bytes(original)
            with patch.object(refine, "implementation_inputs", return_value={"runnerSources": {}}):
                with self.assertRaisesRegex(ValueError, "source_bound"):
                    refine.verified_executable(root, root)

    def test_public_evidence_is_source_bound_and_does_not_approve_geometry_or_publish_raw_data(self):
        value = json.loads((ROOT / "docs/slam-saved-refinement-evidence.json").read_text())
        self.assertEqual("anonymized_offline_saved_map_refinement", value["classification"])
        for name, digest in value["sourceSha256"].items():
            self.assertEqual(digest, refine.sha256(ROOT / name))
        self.assertEqual([90103, 83911], [trial["points"] for trial in value["trials"]])
        self.assertTrue(all(trial["sourceInputsUnchanged"] for trial in value["trials"]))
        self.assertTrue(all(flag is False for flag in value["boundaries"].values()))
        self.assertNotIn("mapId", json.dumps(value))
        self.assertNotIn("xyLowerRaw", json.dumps(value))

    def test_comparison_keeps_identical_limits_and_an_explicit_display_band(self):
        if importlib.util.find_spec("matplotlib") is None:
            self.skipTest("optional static plotting dependency unavailable")
        cloud = refine.np.array([[0., 0., 0.], [1., 2., 0.5], [2., 3., 1.]])
        with tempfile.TemporaryDirectory() as directory:
            result = refine.render_comparison(cloud, cloud + 0.1, (0.4, 0.6), Path(directory))
            self.assertEqual([3, 3, 1, 1], result["panelRetainedPointCounts"])
            self.assertEqual([0.4, 0.6], result["zBandRaw"])
            self.assertTrue(result["displayOnly"])
            self.assertFalse(result["geometricAccuracyValidated"])
            self.assertGreater((Path(directory) / "comparison.private.png").stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
