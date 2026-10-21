"""Regression checks for the r19 packaging-only validator fix."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
NAME = "lingxi-v1.3.8-complete-20261021-r19-deployment"
ARCHIVE = HERE / "dist" / (NAME + ".tar.gz")
SIDECAR = HERE / "dist" / (NAME + ".tar.gz.sha256")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def extract(path, target):
    with tarfile.open(path, "r:gz") as archive:
        archive.extractall(target)
    return target / NAME


class R19BuildTests(unittest.TestCase):
    def test_archive_sidecar_and_helper_checksum(self):
        raw = ARCHIVE.read_bytes()
        self.assertEqual(
            SIDECAR.read_text(encoding="ascii"),
            f"{sha256(raw)}  {ARCHIVE.name}\n",
        )
        with tempfile.TemporaryDirectory(prefix="lingxi-r19-test-") as directory:
            root = extract(ARCHIVE, Path(directory))
            helper = root / "scripts/native-tour-quiet"
            release = json.loads((helper / "release.json").read_text(encoding="utf-8"))
            script = (
                root
                / "components"
                / "sparkrobot-lingxi-nav-internal-1.0.19-20261019-r18.tar.gz"
            )
            with tarfile.open(script, "r:gz") as component:
                voice = next(
                    item
                    for item in component.getmembers()
                    if item.name.endswith("/scripts/robot_voice_bridge_control.sh")
                )
                voice_data = component.extractfile(voice).read()
            self.assertEqual(
                release["voice_bridge_control_sha256"], sha256(voice_data)
            )
            helper_sums = (helper / "SHA256SUMS").read_text(encoding="utf-8")
            for line in helper_sums.splitlines():
                expected, name = line.split("  ", 1)
                self.assertEqual(expected, sha256((helper / name).read_bytes()))
            source = (helper / "install.py").read_text(encoding="utf-8")
            self.assertIn("voice_bridge_control_sha256", source)
            self.assertNotIn(
                'next((item for item in manifest["files"] if item["path"] == VOICE_SCRIPT)',
                source,
            )

    def test_payload_manifest_has_no_runtime_script_expectation(self):
        with tempfile.TemporaryDirectory(prefix="lingxi-r19-test-") as directory:
            root = extract(ARCHIVE, Path(directory))
            component = (
                root
                / "components"
                / "sparkrobot-lingxi-nav-internal-1.0.19-20261019-r18.tar.gz"
            )
            with tarfile.open(component, "r:gz") as archive:
                manifest = next(
                    archive.extractfile(item).read()
                    for item in archive.getmembers()
                    if item.name.endswith("/payload-manifest.json")
                )
            paths = {entry["path"] for entry in json.loads(manifest)["files"]}
            self.assertFalse(any(path.startswith("scripts/") for path in paths))


if __name__ == "__main__":
    unittest.main(verbosity=2)
