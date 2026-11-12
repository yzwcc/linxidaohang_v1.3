"""Offline platform contracts; no native library, DDS or robot initialization."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-runtime/python"), str(ROOT / "tools")]
from mapping_platform import MappingPlatform, configuration, localization_duration, verify_loaded_libraries


class PlatformConfiguration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.file = self.root / "config.json"

    def read(self, value):
        self.file.write_text(json.dumps(value), encoding="utf-8")
        return configuration(self.file)

    def arm(self, mode="live"):
        return {
            "platform": "arm64-humble", "inputMode": mode,
            **dict.fromkeys(("root", "coreRoot", "mappingRoot", "pointAdapter"), str(self.root)),
            "pointManifestSha256": "a" * 64, "hypothesis": "header_is_scan_start",
            **({"maxSeconds": 120} if mode == "live" else {"reference": str(self.root), "rate": 1}),
        }

    def test_x86_configuration_preserves_existing_recorded_contract(self):
        value = {
            "inputMode": "recorded",
            **dict.fromkeys(("work", "adapter", "mappingAdapter", "bundle", "reference"), str(self.root)),
            "hypothesis": "header_is_scan_end", "rate": .5,
        }
        with patch("run_glim_supervised_replay.child_environment", return_value={"TEST": "recorded"}):
            self.assertEqual((value, {"TEST": "recorded"}), self.read(value))
            value["pointAdapter"] = str(self.root)
            self.assertEqual(value, self.read(value)[0])

    def test_live_and_recorded_arm_environments_are_selected_explicitly(self):
        with patch("live_points.environment", return_value={"TEST": "live"}) as live, patch(
                "glim_supervised_target.child_environment", return_value={"TEST": "recorded"}) as recorded:
            for mode in ("live", "recorded"):
                value = self.arm(mode)
                self.assertEqual((value, {"TEST": mode}), self.read(value))
            live.assert_called_once_with(self.root, self.root)
            recorded.assert_called_once_with(self.root, self.root)

    def test_unbounded_ambiguous_and_unknown_configuration_is_rejected(self):
        cases = [
            {"maxSeconds": seconds} for seconds in (0, 4, 301, True, "120", float("inf"), float("nan"))
        ] + [
            {"pointManifestSha256": "invalid"}, {"hypothesis": "guess"},
            {"inputMode": "synthetic"}, {"platform": "other"},
            {"rate": 1}, {"pointAdapter": 7}, {"root": "relative"},
            {"coreRoot": str(self.root / "missing")},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises((ValueError, FileNotFoundError)):
                self.read({**self.arm(), **changes})
        for rate in (0, 2, True, "1"):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                self.read({**self.arm("recorded"), "rate": rate})
        for value in ([], None, {**self.arm(), "unexpected": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(value)

    def test_localization_duration_is_independent_and_legacy_limit_is_preserved(self):
        with patch("live_points.environment", return_value={}):
            legacy = self.read(self.arm())[0]
            self.assertEqual(120, localization_duration(legacy))
            value = self.read({**self.arm(), "localizationMaxSeconds": 3600})[0]
            self.assertEqual(120, value["maxSeconds"])
            self.assertEqual(3600, localization_duration(value))
            with self.assertRaises(ValueError):
                self.read({**value, "maxSeconds": 301})
        for seconds in (True, 0, 4, 3601, "3600", None, float("inf"), float("nan")):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.read({**self.arm(), "localizationMaxSeconds": seconds})
        with self.assertRaises(ValueError):
            self.read({**self.arm("recorded"), "localizationMaxSeconds": 3600})

    def test_nonregular_or_oversized_config_is_rejected(self):
        with self.assertRaises(ValueError):
            configuration(Path("relative.json"))
        self.file.write_bytes(b" " * 4097)
        with self.assertRaises(ValueError):
            configuration(self.file)

    def test_base_pose_requires_explicit_valid_scale_and_live_arm_input(self):
        with patch("live_points.environment", return_value={}):
            value = {**self.arm(), "basePose": {"metresPerMapUnit": 1.}}
            self.assertEqual(value, self.read(value)[0])
        for options in ({}, {"metresPerMapUnit": True}, {"metresPerMapUnit": 0},
                        {"metresPerMapUnit": float("nan")}, {"metresPerMapUnit": "1"},
                        {"metresPerMapUnit": 1., "navigationReady": True}, None):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.read({**self.arm(), "basePose": options})
        with self.assertRaises(ValueError):
            self.read({**self.arm("recorded"), "basePose": {"metresPerMapUnit": 1.}})

    def test_arm_library_paths_keep_sensor_and_mapper_bundles_separate(self):
        value = self.arm("recorded")
        value["mappingRoot"] = str(self.root / "mapper")
        with patch.object(MappingPlatform, "verify", return_value={"frozen": True}):
            binding = MappingPlatform(value)
        self.assertEqual(self.root / "lib/liblingxi_glim_offline.so", binding.odometry_library)
        self.assertEqual(self.root / "mapper/lib/liblingxi_glim_mapping.so", binding.mapping_library)
        self.assertEqual(self.root / "config", binding.config_dir)
        self.assertEqual(self.root / "lib/liblingxi_glim_point_output.so", binding.point_library)
        self.assertEqual("recorded", binding.mode)
        self.assertEqual({"frozen": True}, binding.frozen)

    def test_explicit_navigation_frame_is_rigid_numeric_bounded_and_preserved(self):
        from copy import deepcopy
        frame = [[0., -1., 0., 2.], [1., 0., 0., -1.], [0., 0., 1., .8], [0., 0., 0., 1.]]
        value = {**self.arm(), "basePose": {"metresPerMapUnit": .5, "navigationFromRaw": frame}}
        with patch("live_points.environment", return_value={}):
            self.assertEqual(value, self.read(value)[0])
        invalid = [None, [], "identity"]
        for position, cell in (((0, 0), True), ((1, 0), "1"), ((2, 2), 2.),
                               ((3, 0), 1.), ((0, 3), 501.), ((0, 3), float("nan"))):
            changed = deepcopy(frame)
            changed[position[0]][position[1]] = cell
            invalid.append(changed)
        for matrix in invalid:
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                self.read({**self.arm(), "basePose": {"metresPerMapUnit": 1., "navigationFromRaw": matrix}})

    def test_sensor_provenance_hashes_actual_profile_and_configuration_files(self):
        config = self.root / "config"
        config.mkdir()
        settings = config / "lidar.json"
        settings.write_bytes(b'{"synthetic":true}')
        profile = self.root / "private-profile.json"
        profile.write_bytes(b'{"syntheticProfile":true}')
        with patch.object(MappingPlatform, "verify", return_value={}):
            binding = MappingPlatform(self.arm())
        before = binding.sensor_provenance()
        self.assertEqual(hashlib.sha256(profile.read_bytes()).hexdigest(), before["profileSha256"])
        self.assertEqual({"lidar.json": hashlib.sha256(settings.read_bytes()).hexdigest()},
                         before["configurationSha256"])
        settings.write_bytes(b'{"synthetic":false}')
        self.assertNotEqual(before, binding.sensor_provenance())
        before = binding.sensor_provenance()
        profile.write_bytes(b'{"syntheticProfile":false}')
        self.assertNotEqual(before, binding.sensor_provenance())


class LoadedLibraries(unittest.TestCase):
    def test_optional_inventory_modules_need_not_be_loaded_but_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            required, optional = root / "required.so", root / "optional.so"
            for path in (required, optional):
                path.write_bytes(path.name.encode())
            expected = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (required, optional)}
            line = lambda path: f"0000-1000 r-xp 0000 00:00 1 {path}"
            verify_loaded_libraries(expected, line(required), required={required})
            verify_loaded_libraries(expected, "\n".join(map(line, expected)), required={required})
            with self.assertRaises(ValueError):
                verify_loaded_libraries(expected, line(optional), required={required})
            with self.assertRaises(ValueError):
                verify_loaded_libraries(expected, line(required), required={root / "unknown.so"})
            optional.write_bytes(b"replaced optional library")
            with self.assertRaises(ValueError):
                verify_loaded_libraries(expected, "\n".join(map(line, expected)), required={required})

    def test_all_expected_libraries_must_be_mapped_at_the_exact_path_with_frozen_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = [root / name for name in ("odom.so", "mapping.so", "points.so")]
            for path in paths:
                path.write_bytes(path.name.encode())
            expected = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
            lines = [f"0000-1000 r-xp 0000 00:00 1 {path}" for path in paths]
            verify_loaded_libraries(expected, "\n".join(lines + lines))
            for text in ("\n".join(lines[:-1]), "\n".join(lines + [
                    f"0000-1000 r-xp 0000 00:00 1 {root / 'other' / 'odom.so'}"])):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    verify_loaded_libraries(expected, text)
            paths[-1].write_bytes(b"changed library")
            with self.assertRaises(ValueError):
                verify_loaded_libraries(expected, "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
