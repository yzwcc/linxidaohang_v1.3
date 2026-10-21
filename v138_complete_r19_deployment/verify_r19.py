"""Verify r19 preserves the r18 runtime and changes only the validator package."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OLD = ROOT / "v138_complete_r18_deployment/dist/lingxi-v1.3.8-complete-20261019-r18-deployment.tar.gz"
NEW = HERE / "dist/lingxi-v1.3.8-complete-20261021-r19-deployment.tar.gz"
OLD_NAME = "lingxi-v1.3.8-complete-20261019-r18-deployment"
NEW_NAME = "lingxi-v1.3.8-complete-20261021-r19-deployment"


def archive_files(path, prefix):
    result = {}
    with tarfile.open(path, "r:gz") as archive:
        for entry in archive:
            if not entry.isfile() or not entry.name.startswith(prefix + "/"):
                continue
            result[entry.name[len(prefix) + 1 :]] = archive.extractfile(entry).read()
    return result


class R19RuntimePreservationTests(unittest.TestCase):
    def test_component_and_runtime_scripts_are_byte_identical(self):
        old = archive_files(OLD, OLD_NAME)
        new = archive_files(NEW, NEW_NAME)
        component_names = sorted(
            name for name in old if name.startswith("components/")
        )
        self.assertEqual(component_names, sorted(name for name in new if name.startswith("components/")))
        for name in component_names:
            self.assertEqual(new[name], old[name], name)
        for name in ("install_all.sh", "control_all.sh"):
            self.assertEqual(new[name], old[name], name)

    def test_outer_changes_are_limited_to_r19_metadata_docs_and_helper(self):
        old = archive_files(OLD, OLD_NAME)
        new = archive_files(NEW, NEW_NAME)
        changed = {name for name in old if old[name] != new.get(name)}
        missing = set(old) - set(new)
        removed_docs = {
            "docs/V1.3.8-r18-完整部署教程.md",
            "docs/V1.3.8-r18-已编译AIMDK新机部署教程.md",
        }
        self.assertEqual(missing, removed_docs)
        allowed = {
            "README.md",
            "VERSION",
            "bundle-manifest.json",
            "merged-increments.json",
            *removed_docs,
            "SHA256SUMS.txt",
            "docs/V1.3.8-r19-完整部署教程.md",
            "docs/V1.3.8-r19-已编译AIMDK新机部署教程.md",
        }
        allowed.update(
            name
            for name in changed
            if name.startswith("scripts/native-tour-quiet/")
        )
        self.assertTrue(changed <= allowed, sorted(changed - allowed))


if __name__ == "__main__":
    unittest.main(verbosity=2)
