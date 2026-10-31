"""Preparation checks use isolated, explicitly synthetic source fixtures."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_octomap as preparation


class OctomapPreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve() / "project"
        (self.project / "robot-navigation").mkdir(parents=True)
        (self.project / ".local").mkdir()
        (self.project / "robot-navigation/octomap.lock.json").write_text(json.dumps({"version": "synthetic-only"}))
        (self.project / "fixture.py").write_bytes(b"# isolated synthetic preparation fixture\n")
        self.sources = ("robot-navigation/octomap.lock.json", "fixture.py")
        self.addCleanup(patch.stopall)
        patch.object(preparation, "ROOT", self.project).start()
        patch.object(preparation, "SOURCES", self.sources).start()
        self.output = self.project / ".local/prepared"

    def test_exact_source_bytes_and_manifest_are_bound(self):
        report = preparation.prepare(self.output)
        manifest = json.loads((self.output / "octomap-source-manifest.json").read_bytes())
        self.assertEqual(set(self.sources), set(manifest["files"]))
        self.assertEqual(2, report["sourceFiles"])
        self.assertEqual(preparation.digest(self.output / "octomap-source-manifest.json"), report["sourceManifestSha256"])
        for name, entry in manifest["files"].items():
            self.assertEqual((self.project / name).read_bytes(), (self.output / name).read_bytes())
            self.assertEqual(preparation.digest(self.output / name), entry["sha256"])
        self.assertFalse(manifest["navigationReady"])
        self.assertFalse(manifest["motionCommandsSent"])

    def test_existing_outputs_and_nonprivate_project_paths_are_preserved(self):
        for output in (self.project, self.project / "not-local", self.project / "robot-navigation"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                preparation.prepare(output)
        preparation.prepare(self.output)
        before = (self.output / "octomap-source-manifest.json").read_bytes()
        with self.assertRaises(ValueError):
            preparation.prepare(self.output)
        self.assertEqual(before, (self.output / "octomap-source-manifest.json").read_bytes())

    def test_a_source_change_during_preparation_cannot_produce_a_valid_manifest(self):
        original = preparation.shutil.copyfile
        def copy_then_change(source, target, **kwargs):
            original(source, target, **kwargs)
            if Path(source).name == "fixture.py":
                Path(source).write_bytes(b"# changed isolated synthetic fixture\n")
        with patch.object(preparation.shutil, "copyfile", side_effect=copy_then_change):
            with self.assertRaisesRegex(ValueError, "changed_during"):
                preparation.prepare(self.output)
        self.assertFalse((self.output / "octomap-source-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
