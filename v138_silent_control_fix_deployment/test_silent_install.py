"""Test the additional accepted-baseline path against the shipped transaction engine."""
import json
from unittest.mock import patch

from test_install import InstallTests, NAV, NEW
import test_install as original


class SilentUpgradeTests(InstallTests):
    def setUp(self):
        super().setUp()
        meta_path = self.package / "release.json"
        meta = json.loads(meta_path.read_bytes())
        meta["python_accepted_sha256"] = {
            NAV: [original.m.digest(b"VALUE='old'\n"), original.m.digest(b"VALUE='1.0.7'\n")],
            NEW: [None, original.m.digest(b"ADDED_IN_1_0_7=True\n")],
        }
        meta_path.write_bytes(original.m.json_bytes(meta))
        self.installer = original.FakeInstaller(self.root, self.package)

    def test_previous_cumulative_python_variant_upgrades_and_rolls_back(self):
        old_nav, old_new = b"VALUE='1.0.7'\n", b"ADDED_IN_1_0_7=True\n"
        (self.release / NAV).write_bytes(old_nav)
        (self.release / NEW).write_bytes(old_new)
        manifest = json.loads((self.release / original.m.MANIFEST).read_bytes())
        entries = {entry["path"]: entry for entry in manifest["files"]}
        for name, data in ((NAV, old_nav), (NEW, old_new)):
            entries[name] = dict(path=name, sha256=original.m.digest(data), size=len(data))
        manifest["files"] = list(entries.values())
        (self.release / original.m.MANIFEST).write_bytes(original.m.json_bytes(manifest))
        self.installer.install()
        self.installer.rollback(self.backup())
        self.assertEqual((self.release / NAV).read_bytes(), old_nav)
        self.assertEqual((self.release / NEW).read_bytes(), old_new)
