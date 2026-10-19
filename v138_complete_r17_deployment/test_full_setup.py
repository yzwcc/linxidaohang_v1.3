"""Exercise the full-install adapter against the real native transaction fixtures."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import full_setup as full
import install as installer
from native_tour_quiet import encoded
import test_native_transaction as native_tests


class FullSetupTests(unittest.TestCase):
    def setUp(self):
        fixture = native_tests.TransactionTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        for name in ("root", "package", "release", "installer", "local", "remote", "original"):
            setattr(self, name, getattr(fixture, name))
        self.assert_native_original = fixture.assert_native_original
        release = self.release.with_name(full.EXPECTED_RELEASE)
        self.release.rename(release)
        current = self.installer.base / "current"
        current.unlink()
        current.symlink_to(release)
        self.release = release
        self.installer.meta["allowed_releases"].append(full.EXPECTED_RELEASE)
        self.installer.release = release
        for name, expected in self.installer.meta["payload_sha256"].items():
            target = release / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.package / "payload" / name).read_bytes())
        manifest = json.loads((release / installer.MANIFEST).read_bytes())
        entries = {entry["path"]: entry for entry in manifest["files"]}
        for name, expected in self.installer.meta["payload_sha256"].items():
            entries[name] = dict(path=name, sha256=expected, size=(release / name).stat().st_size)
        manifest["files"] = list(entries.values())
        (release / installer.MANIFEST).write_bytes(encoded(manifest))

    def call_full(self, **kwargs):
        with patch.object(installer, "STATE", self.root / "state"):
            return full.configure(self.installer, self.local, self.remote, **kwargs)

    def test_full_install_disables_native_mapping_without_runtime_replacement(self):
        result = self.call_full()
        self.assertEqual(result["status"], "installed")
        self.assertEqual(self.local.plan()["changes"], 0)
        self.assertEqual(self.remote.native.plan()["changes"], 0)
        record = json.loads(Path(result["transaction"]).read_bytes())
        self.assertIsNone(record["runtime_backup"])
        self.assertTrue(self.installer.java and self.installer.navigation)
        self.assertEqual(json.loads((self.root / "state/full-r17-status.json").read_bytes())["status"],
                         "installed")
        events = [event for event in self.installer.events if not event.endswith("status")]
        self.assertEqual(self.call_full()["status"], "already_disabled")
        self.assertEqual([event for event in self.installer.events if not event.endswith("status")], events)

    def test_full_install_missing_mapping_is_explicit_and_never_stops_services(self):
        for native in (self.local, self.remote.native):
            native.path("/agibot/software/agent_cfg/skills.json").write_bytes(encoded({"skills": []}))
        result = self.call_full()
        self.assertEqual(result["status"], "not_applicable")
        self.assertFalse(result["field_audio_verified"])
        self.assertNotIn("java-stop", self.installer.events)
        self.assertNotIn("apply", self.remote.calls)

    def test_preflight_fails_on_enabled_mapping_without_mutation(self):
        with self.assertRaisesRegex(RuntimeError, "仍启用"):
            self.call_full(check=True)
        self.assert_native_original()
        self.assertNotIn("java-stop", self.installer.events)
        self.assertFalse((self.root / "state/full-r17-status.json").exists())

    def test_preflight_validates_live_config_not_previous_status(self):
        self.call_full()
        self.remote.native.path("/agibot/software/agent_cfg/skills.json").write_bytes(self.original)
        with self.assertRaisesRegex(RuntimeError, "仍启用"):
            self.call_full(check=True)

    def test_full_install_rejects_runtime_regression_before_native_apply(self):
        (self.release / installer.JAR).write_bytes(b"unexpected runtime")
        with self.assertRaisesRegex(RuntimeError, "验证基线"):
            self.call_full()
        self.assert_native_original()
        self.assertNotIn("apply", self.remote.calls)

    def test_full_install_reload_failure_restores_both_configurations(self):
        self.remote.fail_reload_once = True
        with self.assertRaisesRegex(RuntimeError, "native restart"):
            self.call_full()
        self.assert_native_original()
        self.assertTrue(self.installer.java and self.installer.navigation)

    def test_full_install_rejects_busy_robot_before_native_apply(self):
        self.installer.busy = True
        with self.assertRaisesRegex(RuntimeError, "busy"):
            self.call_full()
        self.assert_native_original()
        self.assertNotIn("apply", self.remote.calls)


if __name__ == "__main__":
    unittest.main()
