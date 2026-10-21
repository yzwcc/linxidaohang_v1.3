"""Linux filesystem and service lifecycle tests; no robot is contacted."""
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("arrival_installer", HERE / "install.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NAV = "navigation/robot_local_navigation_v3/navigation_client.py"
NEW = "navigation/robot_local_navigation_v3/near_goal_arrival.py"


def jar(text):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(m.base.MAIN, text)
    return stream.getvalue()


class FakeInstaller(m.Installer):
    def __init__(self, root, package):
        super().__init__(root, package)
        self.java, self.navigation, self.partial = True, True, False
        self.events = []
        self.fail_start, self.fail_access, self.busy = False, False, False

    def state(self):
        return self.java

    def stop(self):
        self.events.append("java-stop")
        self.java = False

    def start(self):
        self.events.append("java-start")
        self.java = True

    def nav_command(self, action):
        self.events.append("nav-" + action)
        if action == "stop":
            self.navigation = False
            self.partial = False
        elif action == "start":
            if self.fail_start:
                self.fail_start = False
                self.partial = True
                raise RuntimeError("simulated navigation startup failure")
            self.navigation = True
        elif action == "status":
            return "\n".join(name + "=" + (
                "stopped" if self.partial and i == 1 else "running pid=123" if self.navigation else "stopped"
            ) for i, name in enumerate(m.COMPONENTS))

    def check_access(self):
        if self.fail_access:
            raise RuntimeError("service access denied")

    def check_nav_access(self):
        pass

    def check_runtime(self, live=False):
        pass

    def idle_check(self):
        if self.busy:
            raise RuntimeError("robot busy")


@unittest.skipUnless(os.name == "posix", "Linux ownership and account checks")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arrival-tts-tests-")
        self.root = Path(self.temp.name) / "robot"
        self.package = Path(self.temp.name) / "package"
        self.release = self.root / "opt/sparkrobot/lingxi-nav/releases/1.0.19-20261019-r16"
        self.release.mkdir(parents=True)
        (self.release.parent.parent / "current").symlink_to(self.release, target_is_directory=True)
        self.old, self.new, self.sdk = jar("old"), jar("new"), jar("sdk")
        self.payload = {m.JAR: self.new, NAV: b"VALUE='new'\n", NEW: b"NEW=True\n"}
        original = {m.JAR: self.old, NAV: b"VALUE='old'\n", m.base.SDK: self.sdk, m.NAV_CONTROL: b"control"}
        for relative, data in original.items():
            path = self.release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o640)
        for relative, data in self.payload.items():
            path = self.package / "payload" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.old_manifest = m.json_bytes({"files": [
            dict(path=name, sha256=m.digest(data), size=len(data)) for name, data in original.items()
        ]})
        (self.release / m.MANIFEST).write_bytes(self.old_manifest)
        meta = dict(version="test", allowed_releases=[self.release.name],
            baseline_jar_sha256=m.digest(self.old), patched_jar_sha256=m.digest(self.new),
            sdk_sha256=m.digest(self.sdk), python_baselines={NAV: m.digest(original[NAV]), NEW: None},
            payload_sha256={name: m.digest(data) for name, data in self.payload.items()},
            navigation_control_sha256=m.digest(b"control"))
        (self.package / "release.json").write_bytes(m.json_bytes(meta))
        self.installer = FakeInstaller(self.root, self.package)
        self.protected = self.root / "var/lib/sparkrobot/lingxi-nav/maps/test.json"
        self.protected.parent.mkdir(parents=True)
        self.protected.write_bytes(b"untouched-map-task-data")
        self.before = {p.relative_to(self.release).as_posix(): (p.read_bytes(), p.stat())
                       for p in self.release.rglob("*") if p.is_file()}

    def tearDown(self):
        self.temp.cleanup()

    def backup(self):
        return next(self.installer.backups.iterdir())

    def assert_original(self):
        self.assertFalse((self.release / NEW).exists())
        for relative, (data, metadata) in self.before.items():
            path = self.release / relative
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), stat.S_IMODE(metadata.st_mode))
            self.assertEqual((path.stat().st_uid, path.stat().st_gid), (metadata.st_uid, metadata.st_gid))
        self.assertEqual(self.protected.read_bytes(), b"untouched-map-task-data")

    def test_install_repeat_rollback_preserves_all_metadata_and_user_data(self):
        self.installer.install()
        for relative, data in self.payload.items():
            self.assertEqual((self.release / relative).read_bytes(), data)
        self.assertEqual(stat.S_IMODE((self.release / NEW).stat().st_mode), 0o640)
        count = self.installer.events.count("nav-start")
        self.installer.install()
        self.assertEqual(self.installer.events.count("nav-start"), count)
        self.installer.rollback(self.backup())
        self.assert_original()
        self.assertTrue(self.installer.java and self.installer.navigation)

    def test_each_running_stopped_combination_is_preserved(self):
        for java, nav in ((False, False), (True, False), (False, True)):
            self.installer.java, self.installer.navigation = java, nav
            self.installer.install()
            self.assertEqual((self.installer.java, self.installer.navigation), (java, nav))
            self.installer.rollback(self.backup())
            self.assertEqual((self.installer.java, self.installer.navigation), (java, nav))
            self.assert_original()
            # Remove only the test's already-verified empty transaction tree.
            import shutil
            shutil.rmtree(self.installer.backups)

    def test_nav_start_failure_restores_exact_original_files_once(self):
        self.installer.fail_start = True
        with self.assertRaisesRegex(RuntimeError, "startup"):
            self.installer.install()
        self.assert_original()
        self.assertTrue(self.installer.java and self.installer.navigation)
        self.assertEqual(self.installer.events.count("nav-start"), 2)

    def test_partial_services_and_active_tour_are_rejected_before_stop(self):
        for partial, busy in ((True, False), (False, True)):
            self.installer.partial, self.installer.busy = partial, busy
            with self.assertRaises(RuntimeError):
                self.installer.install()
            self.assertNotIn("java-stop", self.installer.events)
            self.assert_original()

    def test_unknown_python_script_hash_and_manifest_fail_before_stop(self):
        for relative in (NAV, m.NAV_CONTROL, m.MANIFEST):
            path = self.release / relative
            previous = path.read_bytes()
            path.write_bytes(m.json_bytes({"files": []}) if relative == m.MANIFEST else b"unknown")
            with self.assertRaises(RuntimeError):
                self.installer.install()
            self.assertNotIn("java-stop", self.installer.events)
            path.write_bytes(previous)
        self.assert_original()

    def test_unknown_added_module_is_not_overwritten(self):
        (self.release / NEW).write_bytes(b"field-custom")
        with self.assertRaisesRegex(RuntimeError, "Python"):
            self.installer.install()
        self.assertEqual((self.release / NEW).read_bytes(), b"field-custom")
        self.assertNotIn("java-stop", self.installer.events)

    def test_symlinked_target_is_rejected(self):
        target = self.release / NEW
        target.symlink_to(self.protected)
        with self.assertRaisesRegex(RuntimeError, "软链接"):
            self.installer.install()
        self.assertEqual(self.protected.read_bytes(), b"untouched-map-task-data")

    def test_corrupt_payload_is_rejected_before_stop(self):
        (self.package / "payload" / NAV).write_bytes(b"broken")
        with self.assertRaisesRegex(RuntimeError, "载荷"):
            self.installer.install()
        self.assertNotIn("java-stop", self.installer.events)

    def test_corrupt_backup_and_later_changes_block_rollback_before_stop(self):
        self.installer.install()
        backup = self.backup()
        stops = self.installer.events.count("java-stop")
        (self.release / NAV).write_bytes(b"later-hotfix")
        with self.assertRaises(RuntimeError):
            self.installer.rollback(backup)
        self.assertEqual(self.installer.events.count("java-stop"), stops)
        (self.release / NAV).write_bytes(self.payload[NAV])
        (backup / "files" / NAV).write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "损坏"):
            self.installer.rollback(backup)
        self.assertEqual(self.installer.events.count("java-stop"), stops)

    def test_service_user_readability_is_checked_as_the_real_account(self):
        Path(self.temp.name).chmod(0o755)
        instance = m.Installer(self.root, self.package)
        instance.release = self.release
        with patch.object(m.runtime, "NAV_USER", "nobody"):
            with self.assertRaises(RuntimeError):
                instance.check_nav_access()
            (self.release / NAV).chmod(0o644)
            instance.check_nav_access()

    def test_check_validation_never_starts_or_stops_services(self):
        self.installer.validate()
        self.installer.snapshot(require_idle=True)
        self.assertNotIn("java-stop", self.installer.events)
        self.assertNotIn("nav-start", self.installer.events)
        self.assert_original()

    def test_write_failure_mid_transaction_restores_existing_and_missing_files(self):
        original = m.write_atomic
        failed = [False]
        def write(path, data, metadata):
            if path == self.release / NEW and not failed[0]:
                failed[0] = True
                raise OSError("simulated full disk")
            return original(path, data, metadata)
        with patch.object(m.runtime, "write_atomic", side_effect=write):
            with self.assertRaisesRegex(OSError, "full disk"):
                self.installer.install()
        self.assert_original()
        self.assertTrue(self.installer.java and self.installer.navigation)


if __name__ == "__main__":
    unittest.main(verbosity=2)
