"""Source manifests and probe shape only; synthetic headers are not executable evidence."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_observation_target as target

NUMERICAL = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy"))
if NUMERICAL:
    import numpy as np
    from scipy.spatial.transform import Rotation
    import check_observation_target_geometry as geometry
    from test_observation_archive import archive_bytes, pose


class ObservationTargetContracts(unittest.TestCase):
    def setUp(self):
        private = target.ROOT / ".local"
        private.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="isolated-observation-manifest-", dir=private)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).absolute()
        self.source = self.root / "synthetic-source"
        self.source.mkdir()
        for name in target.MEMBERS:
            path = self.source / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"\x7fELF\x02\x01" + b"\0" * 12 + (183).to_bytes(2, "little") + b"SYNTHETIC NOT EXECUTABLE")
        self.manifest = {"schemaVersion": 1, "purpose": target.PURPOSE,
            "runnerSha256": target.digest(target.__file__), "scenarios": list(target.SCENARIOS),
            "expectedCoreLibrariesSha256": {"isolated-synthetic.so": "a" * 64},
            "files": {name: {"sha256": target.digest(self.source / name), "bytes": (self.source / name).stat().st_size}
                      for name in target.MEMBERS}}
        self.refresh()

    def refresh(self):
        path = self.source / "manifest.json"
        path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.digest = target.digest(path)

    def test_exact_source_manifest_is_checked_without_loading_or_connecting(self):
        self.assertEqual(self.manifest, target.verify_source(self.source, self.digest))
        path = self.source / target.MEMBERS[0]
        path.write_bytes(path.read_bytes() + b"changed isolated fixture")
        with self.assertRaises(ValueError):
            target.verify_source(self.source, self.digest)

    def test_extra_file_changed_scenarios_and_different_runner_are_rejected(self):
        extra = self.source / "unexpected"
        extra.write_text("synthetic")
        with self.assertRaises(ValueError):
            target.verify_source(self.source, self.digest)
        extra.unlink()
        for key, value in (("runnerSha256", "b" * 64), ("scenarios", [])):
            before = self.manifest[key]
            self.manifest[key] = value
            self.refresh()
            with self.subTest(key=key), self.assertRaises(ValueError):
                target.verify_source(self.source, self.digest)
            self.manifest[key] = before

    def test_private_output_cannot_replace_existing_data(self):
        with self.assertRaises(ValueError):
            target.direct_output(self.source)
        self.assertEqual(self.root / "new-result", target.direct_output(self.root / "new-result"))

    def test_native_probe_has_no_ros_network_or_service_operations(self):
        tree = ast.parse(target.PROBE)
        forbidden = {"init", "create_node", "create_subscription", "create_publisher", "publish", "connect"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, forbidden)
        for command in ("ros2", "systemctl", "sudo", "apt-get"):
            self.assertNotIn(command, target.PROBE)
        self.assertEqual(7, len(target.SCENARIOS))


@unittest.skipUnless(NUMERICAL, "requires isolated numerical Python runtime")
class ObservationCollectedGeometry(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).absolute()
        self.manifest = {"expectedCoreLibrariesSha256": {"synthetic-core.so": "a" * 64},
                         "files": {"lib/" + target.LIBRARY: {"sha256": "b" * 64}}}
        replacement = patch.object(target, "verify_source", return_value=self.manifest)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.report = {"result": target.RESULT, "machine": "aarch64", "manifestSha256": "c" * 64,
            "syntheticFixtureNotTargetEvidence": True, "boundaries": dict.fromkeys(target.BOUNDARIES, False),
            "loadedLibrariesSha256": {"/isolated-synthetic/synthetic-core.so": "a" * 64,
                                     "/isolated-synthetic/" + target.LIBRARY: "b" * 64}, "scenarios": []}
        for mode in target.SCENARIOS:
            invalid = mode in ("unbracketed", "missing-trajectory")
            row = {"scenario": mode, "exitCode": 0, "loadedLibrariesMatched": True, "archiveCreated": not invalid}
            if not invalid:
                options = {"global_pose": pose(10), "local_pose": pose(-8)}
                if mode == "corrected":
                    options["global_pose"] = pose(20, 90)
                if mode == "reference-shift":
                    options["reference_pose"] = pose(5)
                    options["trajectory"] = np.array([[10, 5, 0, 0, 0, 0, 0, 1],
                        [11, 7, 0, 0, *Rotation.from_euler("z", 90, degrees=True).as_quat()]])
                if mode == "extrinsic":
                    extrinsic = pose()
                    extrinsic[:3, :3] = Rotation.from_euler("x", 90, degrees=True).as_matrix()
                    extrinsic[:3, 3] = [1, 2, 3]
                    options["extrinsic_pose"] = extrinsic
                data = archive_bytes(**options)
                if mode == "fallback":
                    import struct
                    data = bytearray(data)
                    struct.pack_into("<Q", data, 40 + 8 + 128 + 8 + 40, 2)
                path = self.root / (mode + ".bin")
                path.write_bytes(data)
                row.update(archiveSha256=target.digest(path), archiveBytes=len(data))
            self.report["scenarios"].append(row)
        self.write_reports()

    def write_reports(self):
        path = self.root / "report.json"
        path.write_text(json.dumps(self.report), encoding="utf-8")
        collection = {"result": target.RESULT, "manifestSha256": "c" * 64,
                      "hostIdentityVerified": True, "syntheticOnly": True, "reportSha256": target.digest(path),
                      "syntheticFixtureNotTargetEvidence": True}
        (self.root / "collection.json").write_text(json.dumps(collection), encoding="utf-8")

    def test_collected_fixture_geometry_is_reconstructed_without_a_readiness_claim(self):
        result = geometry.check(self.root, self.root, "c" * 64)
        self.assertEqual(5, result["geometryArchivesReconstructed"])
        self.assertTrue(all(value is False for value in result["boundaries"].values()))

    def test_changed_geometry_is_detected_even_after_synthetic_hashes_are_updated(self):
        path = self.root / "base.bin"
        path.write_bytes(archive_bytes(global_pose=pose(10), local_pose=pose(99)))
        self.report["scenarios"][0].update(archiveSha256=target.digest(path), archiveBytes=path.stat().st_size)
        self.write_reports()
        with self.assertRaises(AssertionError):
            geometry.check(self.root, self.root, "c" * 64)


if __name__ == "__main__":
    unittest.main()
