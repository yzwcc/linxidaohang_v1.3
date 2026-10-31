"""Source preparation tests with explicitly synthetic upstream bytes."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import prepare_navfn as preparation


class SourcePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.project, self.upstream = base / "project", base / "upstream"
        (self.project / "robot-navigation").mkdir(parents=True)
        (self.project / ".local").mkdir()
        self.upstream.mkdir()
        (self.upstream / "navfn.cpp").write_bytes(b"synthetic fixture only\n")
        self.lock = {"commit": "a" * 40, "files": {"src/navfn.cpp":
            hashlib.sha256((self.upstream / "navfn.cpp").read_bytes()).hexdigest()}}
        (self.project / "robot-navigation/navfn.lock.json").write_text(json.dumps(self.lock))
        (self.project / "fixture.py").write_bytes(b"# isolated synthetic fixture\n")
        self.sources = ("robot-navigation/navfn.lock.json", "fixture.py")
        self.output = self.project / ".local/prepared"
        self.addCleanup(patch.stopall)
        patch.object(preparation, "ROOT", self.project).start()
        patch.object(preparation, "SOURCES", self.sources).start()

    def test_preparation_keeps_exact_source_bytes_and_binds_manifest(self):
        report = preparation.prepare(self.upstream, self.output)
        manifest = json.loads((self.output / "navfn-source-manifest.json").read_bytes())
        self.assertEqual(3, report["sourceFiles"])
        self.assertEqual(preparation.digest(self.output / "navfn-source-manifest.json"), report["sourceManifestSha256"])
        for name, entry in manifest["files"].items():
            self.assertEqual(preparation.digest(self.output / name), entry["sha256"])
        self.assertEqual((self.upstream / "navfn.cpp").read_bytes(), (self.output / "upstream/src/navfn.cpp").read_bytes())
        self.assertFalse(manifest["navigationReady"])
        self.assertFalse(manifest["motionCommandsSent"])

    def test_changed_upstream_is_rejected_before_output_creation(self):
        (self.upstream / "navfn.cpp").write_bytes(b"different synthetic fixture\n")
        with self.assertRaisesRegex(ValueError, "hash_pinned"):
            preparation.prepare(self.upstream, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_or_input_overlapping_outputs_are_preserved(self):
        for output in (self.upstream, self.upstream / "nested", self.project,
                       self.project / "not-local", self.project / "robot-navigation"):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "separate"):
                preparation.prepare(self.upstream, output)
        preparation.prepare(self.upstream, self.output)
        before = (self.output / "navfn-source-manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "separate"):
            preparation.prepare(self.upstream, self.output)
        self.assertEqual(before, (self.output / "navfn-source-manifest.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
