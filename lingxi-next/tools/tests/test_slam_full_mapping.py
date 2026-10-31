"""Synthetic contracts and optional pinned-source patch checks, not map accuracy."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "json5"))
if AVAILABLE:
    with patch.object(sys, "path", [str(ROOT / "tools")] + sys.path):
        import run_glim_full_mapping as full

UPSTREAM = Path(os.environ.get("LINGXI_GLIM_UPSTREAM_ROOT", "/nonexistent-optional-pinned-upstream"))


@unittest.skipUnless(AVAILABLE, "requires isolated scientific Python environment")
class FullMappingContracts(unittest.TestCase):
    def stats(self):
        return full.np.array([100, 3, 5000, 100, 2000, 4, 3, 1, 3, 25.5])

    def events(self):
        return [{"submaps": submaps, "matchingFactors": matching, "nonAdjacentMatchingFactors": nonadjacent}
                for submaps, matching, nonadjacent in ((1, 0, 0), (2, 1, 0), (3, 3, 1), (3, 3, 1))]

    def test_terminal_frame_and_imu_accounting_are_explicit(self):
        value = full.checked_counts(self.stats(), 100)
        self.assertEqual(100, value["savedFrames"])
        self.assertEqual(2000, value["imuSamples"])
        self.assertEqual(3, value["localOptimizationCalls"])
        self.assertEqual(25.5, value["maximumGlobalInsertionMilliseconds"])

    def test_missing_duplicate_nonfinite_or_fractional_counts_are_rejected(self):
        for index, value in ((0, 99), (3, 99), (3, 101), (2, 0), (4, 0), (8, 0),
                             (1, 1.5), (9, float("inf")), (6, float("nan"))):
            stats = self.stats()
            stats[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                full.checked_counts(stats, 100)

    def test_incremental_updates_are_checked_against_final_native_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.jsonl"
            events = self.events()
            path.write_text("".join(json.dumps(event) + "\n" for event in events))
            self.assertEqual(events, full.checked_events(path, full.checked_counts(self.stats(), 100)))

    def test_omitted_reordered_or_inconsistent_update_is_not_a_complete_graph(self):
        good = self.events()
        changes = [good[:-1], [good[2], *good[:2], good[3]],
                   [*good[:-1], {**good[-1], "matchingFactors": 2}],
                   [{**good[0], "submaps": True}, *good[1:]],
                   [{**good[0], "nonAdjacentMatchingFactors": 1}, *good[1:]]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.jsonl"
            for events in changes:
                with self.subTest(events=events):
                    path.write_text("".join(json.dumps(event) + "\n" for event in events))
                    with self.assertRaises(ValueError):
                        full.checked_events(path, full.checked_counts(self.stats(), 100))

    def test_configuration_enables_full_local_and_global_without_modifying_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original"
            original.mkdir()
            (original / "config.json").write_text('{"global":{"config_sub_mapping":"passthrough.json"}}')
            upstream = root / "upstream"
            (upstream / "config").mkdir(parents=True)
            (upstream / "config/config_sub_mapping_cpu.json").write_text(
                '{"sub_mapping":{"enable_imu":true,"enable_optimization":false}}')
            (upstream / "config/config_global_mapping_cpu.json").write_text(
                '{"global_mapping":{"enable_imu":true,"enable_optimization":true,"randomsampling_rate":0.2}}')
            before = full.configuration_hashes(original)
            with patch("refine_saved_map.verify_source", return_value="isolated-synthetic-commit"):
                result = full.full_config(upstream, original, root / "output")
            local = json.loads((root / "output/config_sub_mapping_cpu.json").read_text())
            world = json.loads((root / "output/config_global_mapping_cpu.json").read_text())
            self.assertTrue(local["sub_mapping"]["enable_optimization"])
            self.assertTrue(local["sub_mapping"]["enable_imu"])
            self.assertTrue(world["global_mapping"]["enable_imu"])
            self.assertEqual(1.0, world["global_mapping"]["randomsampling_rate"])
            self.assertEqual(before, full.configuration_hashes(original))
            self.assertEqual(full.configuration_hashes(root / "output"), result["effectiveConfigurationSha256"])

    def test_all_acceptance_boundaries_remain_unaccepted(self):
        self.assertIn("loopClosureValidated", full.BOUNDARIES)
        self.assertIn("targetArm64RuntimeValidated", full.BOUNDARIES)
        text = (ROOT / "tools/glim-full-mapping/full_mapping.cpp").read_text()
        self.assertIn("saved_ids != finalized_ids", text)
        self.assertNotIn("get_remaining_frames", text)
        self.assertNotIn("min_loop_travel", text)
        self.assertIn("local->insert_imu(stamp, acc, gyro)", text)
        self.assertIn("global->insert_imu(stamp, acc, gyro)", text)

    def test_unique_eof_symbols_are_required_instead_of_the_original_class(self):
        result = type("Result", (), {"stdout": "synthetic-nm"})()
        names = ("glim::LingxiSubMappingEof::insert_finalized_frame()\n"
                 "glim::LingxiSubMappingEof::submit_end_of_sequence()\n")
        for text, accepted in ((names, True), (names + "glim::SubMapping::insert_frame()", False), ("", False)):
            with self.subTest(text=text), patch.object(full.subprocess, "run", side_effect=[
                    result, type("Result", (), {"stdout": text})()]):
                if accepted:
                    self.assertEqual(2, len(full.private_class_symbols(Path("synthetic.so"))))
                else:
                    with self.assertRaisesRegex(ValueError, "independent_patched"):
                        full.private_class_symbols(Path("synthetic.so"))

    def test_actual_libraries_match_canonical_sonames_and_reject_legacy_or_missing_implementation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            libs, adapter = root / "prefix/lib", root / "odometry/build"
            libs.mkdir(parents=True)
            adapter.mkdir(parents=True)
            names = ("libglim.so", "libgtsam.so", "libgtsam_points.so", "libodometry_estimation_cpu.so")
            paths = []
            for name in names:
                path = libs / name
                target = path.with_name(name + ".synthetic-version") if "gtsam" in name else path
                target.write_bytes(b"isolated-synthetic-library-not-executed")
                if target != path:
                    path.symlink_to(target.name)
                paths.append(target)
            library = root / full.LIBRARY
            library.write_bytes(b"isolated-synthetic-full-library-not-executed")
            odometry = adapter / "liblingxi_glim_offline.so"
            odometry.write_bytes(b"isolated-synthetic-odometry-library-not-executed")
            paths.extend((library, odometry))
            lines = ["00-01 r--p 000 00:00 0 " + str(path) for path in paths]
            args = SimpleNamespace(work=root, adapter=root / "odometry")
            with patch.object(Path, "read_text", return_value="\n".join(lines)):
                result = full.observed_libraries(args, library)
            self.assertEqual(set(names) | {full.LIBRARY, "liblingxi_glim_offline.so"}, set(result))
            for records in (lines[:-1], lines + ["00-01 r--p 000 00:00 0 " + str(libs / "libsub_mapping.so")]):
                with self.subTest(records=records), patch.object(Path, "read_text", return_value="\n".join(records)):
                    with self.assertRaises(ValueError):
                        full.observed_libraries(args, library)

    def test_published_evidence_retains_all_hypotheses_and_reports_latency_problem(self):
        value = json.loads((ROOT / "docs/slam-full-mapping-evidence.json").read_text())
        self.assertEqual({"dynamic-start", "dynamic-end", "static-start", "static-end"},
                         {trial["case"] for trial in value["trials"]})
        for name, digest in value["sourceSha256"].items():
            self.assertEqual(digest, full.sha256(ROOT / name))
        for trial in value["trials"]:
            self.assertEqual(trial["recordedScans"], trial["skippedUnbracketedScans"] +
                             trial["initializationScans"] + trial["savedFrames"])
            self.assertLess(trial["maximumRoundTripErrorRaw"], value["roundTripToleranceRaw"])
            self.assertTrue(trial["sourceInputsUnchanged"])
        moving = [trial for trial in value["trials"] if trial["case"].startswith("dynamic")]
        self.assertTrue(all(trial["worstCombinedFrameMilliseconds"] > 2000 for trial in moving))
        self.assertTrue(all(flag is False for flag in value["boundaries"].values()))
        self.assertNotIn("mapId", json.dumps(value))

    @unittest.skipUnless(UPSTREAM.is_dir(), "optional pinned source unavailable")
    def test_eof_patch_applies_to_clean_fixed_source_and_does_not_duplicate_input(self):
        full.verify_source(UPSTREAM)
        paths = ("include/glim/mapping/sub_mapping.hpp", "src/glim/mapping/sub_mapping.cpp")
        before = {name: full.sha256(UPSTREAM / name) for name in paths}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in paths:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(UPSTREAM / name, target)
            subprocess.run(["git", "-C", str(root), "apply", "--recount",
                            str(ROOT / "tools/glim-full-mapping/submapping-eof.patch")], check=True)
            text = (root / paths[1]).read_text()
            terminal = text.split("SubMapping::submit_end_of_sequence()", 1)[1]
            self.assertIn("delayed_input_queue.pop_front()", terminal)
            self.assertIn("insert_finalized_frame(frame)", terminal)
            self.assertIn("auto submaps = get_submaps()", terminal)
            self.assertNotIn("insert_frame(", terminal)
        self.assertEqual(before, {name: full.sha256(UPSTREAM / name) for name in paths})


if __name__ == "__main__":
    unittest.main()
