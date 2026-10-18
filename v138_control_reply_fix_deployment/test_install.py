"""Linux deployment scenarios using temporary roots; never touches a robot."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("reply_installer", HERE / "install.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def jar(marker):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr(module.MAIN, marker)
    return result.getvalue()


class FakeInstaller(module.Installer):
    def __init__(self, root, package):
        super().__init__(root, package)
        self.active = True
        self.events = []
        self.fail_start = False
        self.fail_access = False

    def state(self):
        return self.active

    def stop(self):
        self.events.append("stop")
        self.active = False

    def start(self):
        self.events.append("start")
        if self.fail_start:
            self.fail_start = False
            raise RuntimeError("simulated startup failure")
        self.active = True

    def check_access(self):
        self.events.append("access")
        if self.fail_access:
            raise RuntimeError("service user cannot read JAR")

    def check_runtime(self, live=False):
        self.events.append("runtime")


@unittest.skipUnless(os.name == "posix", "requires Linux filesystem ownership")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="control-reply-tests-")
        self.root = Path(self.temp.name) / "robot"
        self.package = Path(self.temp.name) / "package"
        self.release = self.root / "opt/sparkrobot/lingxi-nav/releases/1.0.19-20261017-r15"
        self.release.mkdir(parents=True)
        (self.release.parent.parent / "current").symlink_to(self.release, target_is_directory=True)
        self.old, self.new, self.sdk = jar("old"), jar("new"), jar("sdk")
        for path, data in ((self.release / module.JAR, self.old),
                           (self.release / "java/linksoul-agentsdk-1.4.0.jar", self.sdk),
                           (self.package / "payload" / module.JAR, self.new)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.release / module.JAR).chmod(0o640)
        manifest = {"files": [dict(path=module.JAR, sha256=module.digest(self.old), size=len(self.old))]}
        (self.release / module.MANIFEST).write_bytes(module.json_bytes(manifest))
        self.old_manifest = (self.release / module.MANIFEST).read_bytes()
        self.metadata = dict(version="test", allowed_releases=[self.release.name],
            baseline_jar_sha256=module.digest(self.old), patched_jar_sha256=module.digest(self.new),
            sdk_sha256=module.digest(self.sdk))
        (self.package / "release.json").write_bytes(module.json_bytes(self.metadata))
        self.installer = FakeInstaller(self.root, self.package)
        config = self.root / "etc/sparkrobot/lingxi-nav/lingxi-nav.properties"
        config.parent.mkdir(parents=True)
        config.write_text("unchanged=test\n")
        self.protected = config

    def tearDown(self):
        self.temp.cleanup()

    def backup(self):
        return next(self.installer.backups.iterdir())

    def test_install_repeat_rollback_preserves_data_permissions_and_running_state(self):
        before = self.protected.read_bytes()
        self.installer.install()
        self.assertEqual((self.release / module.JAR).read_bytes(), self.new)
        self.assertEqual(stat.S_IMODE((self.release / module.JAR).stat().st_mode), 0o640)
        self.assertTrue(self.installer.active)
        backup = self.backup()
        self.assertEqual(self.installer.events.count("start"), 1)
        self.installer.install()
        self.assertEqual(self.installer.events.count("start"), 1)
        self.assertEqual(len(list(self.installer.backups.iterdir())), 1)
        self.installer.rollback(backup)
        self.assertEqual((self.release / module.JAR).read_bytes(), self.old)
        self.assertEqual((self.release / module.MANIFEST).read_bytes(), self.old_manifest)
        self.assertEqual(self.protected.read_bytes(), before)
        self.assertTrue(self.installer.active)

    def test_stopped_service_stays_stopped_during_install_and_rollback(self):
        self.installer.active = False
        self.installer.install()
        self.assertFalse(self.installer.active)
        self.installer.rollback(self.backup())
        self.assertFalse(self.installer.active)
        self.assertNotIn("start", self.installer.events)

    def test_previous_patch_can_upgrade_and_rollback_to_its_exact_bytes(self):
        previous = jar("previous-v1.0.0")
        (self.release / module.JAR).write_bytes(previous)
        manifest = {"files": [dict(path=module.JAR, sha256=module.digest(previous), size=len(previous))],
                    "control_reply_fix": "1.0.0-20261018"}
        previous_manifest = module.json_bytes(manifest)
        (self.release / module.MANIFEST).write_bytes(previous_manifest)
        self.installer.meta["superseded_jar_sha256"] = [module.digest(previous)]
        self.installer.install()
        self.assertEqual((self.release / module.JAR).read_bytes(), self.new)
        self.installer.rollback(self.backup())
        self.assertEqual((self.release / module.JAR).read_bytes(), previous)
        self.assertEqual((self.release / module.MANIFEST).read_bytes(), previous_manifest)

    def test_startup_failure_rolls_back_once_and_restores_running_service(self):
        self.installer.fail_start = True
        with self.assertRaisesRegex(RuntimeError, "startup"):
            self.installer.install()
        self.assertEqual((self.release / module.JAR).read_bytes(), self.old)
        self.assertEqual((self.release / module.MANIFEST).read_bytes(), self.old_manifest)
        self.assertEqual(self.installer.events.count("start"), 2)
        self.assertEqual(self.installer.events.count("stop"), 2)
        self.assertTrue(self.installer.active)

    def test_invalid_base_sdk_manifest_and_service_access_fail_before_stop(self):
        for mode in ("jar", "sdk", "manifest", "access", "version"):
            with self.subTest(mode=mode):
                path = self.release / (module.MANIFEST if mode == "manifest"
                                       else "java/linksoul-agentsdk-1.4.0.jar" if mode == "sdk" else module.JAR)
                old = path.read_bytes()
                if mode in {"jar", "sdk"}:
                    path.write_bytes(b"unknown-field-modification")
                elif mode == "manifest":
                    path.write_bytes(module.json_bytes({"files": []}))
                elif mode == "access":
                    self.installer.fail_access = True
                else:
                    self.installer.meta["allowed_releases"] = []
                with self.assertRaises(RuntimeError):
                    self.installer.install()
                self.assertNotIn("stop", self.installer.events)
                path.write_bytes(old)
                self.installer.fail_access = False
                self.installer.meta["allowed_releases"] = [self.release.name]

    def test_modified_post_install_manifest_or_backup_cannot_be_overwritten(self):
        self.installer.install()
        backup = self.backup()
        manifest = self.release / module.MANIFEST
        new_manifest = manifest.read_bytes()
        value = json.loads(new_manifest)
        value["other_field_patch"] = True
        manifest.write_bytes(module.json_bytes(value))
        count = self.installer.events.count("stop")
        with self.assertRaisesRegex(RuntimeError, "其他变更"):
            self.installer.rollback(backup)
        manifest.write_bytes(new_manifest)
        (backup / "files" / module.JAR).write_bytes(b"corrupted")
        with self.assertRaisesRegex(RuntimeError, "损坏"):
            self.installer.rollback(backup)
        self.assertEqual(self.installer.events.count("stop"), count)

    def test_package_integrity_rejects_changed_payload_and_crlf(self):
        for name in ("install.py", "install.sh"):
            (self.package / name).write_bytes(b"fixture")
        files = [p for p in self.package.rglob("*") if p.is_file()]
        sums = "".join(module.digest(p.read_bytes()) + "  " + p.relative_to(self.package).as_posix() + "\n"
                       for p in sorted(files)).encode()
        (self.package / "SHA256SUMS").write_bytes(sums)
        module.verify_package(self.package)
        (self.package / "payload" / module.JAR).write_bytes(b"damaged")
        with self.assertRaisesRegex(RuntimeError, "校验失败"):
            module.verify_package(self.package)
        (self.package / "SHA256SUMS").write_bytes(sums.replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(RuntimeError, "LF"):
            module.verify_package(self.package)

    def test_backup_from_another_release_is_rejected_before_stopping_service(self):
        self.installer.install()
        backup = self.backup()
        transaction_path = backup / "transaction.json"
        transaction = json.loads(transaction_path.read_bytes())
        transaction["release"] = str(self.release.parent / "1.0.19-20261017-r14")
        transaction_path.write_bytes(module.json_bytes(transaction))
        stops = self.installer.events.count("stop")
        with self.assertRaisesRegex(RuntimeError, "另一个 release"):
            self.installer.rollback(backup)
        self.assertEqual(self.installer.events.count("stop"), stops)
        self.assertTrue(self.installer.active)
        self.assertEqual((self.release / module.JAR).read_bytes(), self.new)

    def test_effective_start_command_and_live_process_use_current_jar(self):
        actual = module.Installer(self.root, self.package)
        actual.release = self.release
        alias = self.release.parent.parent / "current"
        command = f"{{ path=/usr/bin/java ; argv[]=/usr/bin/java -cp {alias / module.JAR}:{alias / module.SDK} {module.MAIN_CLASS} ; }}"
        proc = self.root / "proc/123/cmdline"
        proc.parent.mkdir(parents=True)
        args = ["/usr/bin/java", "-cp", f"{alias / module.JAR}:{alias / module.SDK}", module.MAIN_CLASS]
        proc.write_bytes(("\0".join(args) + "\0").encode())

        def properties(values, **kwargs):
            if "ExecStart" in values:
                return command
            if "MainPID" in values:
                return "123"
            raise AssertionError(values)

        with patch.object(module, "run", side_effect=properties):
            actual.check_runtime(live=True)
            original_command = command
            for altered in (original_command.replace(module.JAR, module.JAR + ".old"),
                            original_command.replace(str(alias / module.JAR), "/var/lib/overrides/old.jar"),
                            original_command.replace(module.SDK, "java/old-sdk.jar")):
                command = altered
                with self.assertRaisesRegex(RuntimeError, "启动命令"):
                    actual.check_runtime(live=False)
            command = original_command
            for bad in (
                ["/usr/bin/java", "-cp", f"/var/lib/old.jar:{alias / module.SDK}", module.MAIN_CLASS],
                ["/usr/bin/java", "-cp", f"/var/lib/old.jar:{args[2]}", module.MAIN_CLASS],
                ["/usr/bin/java", "-cp", args[2], "other.Main"],
            ):
                proc.write_bytes(("\0".join(bad) + "\0").encode())
                with self.assertRaisesRegex(RuntimeError, "实际进程"):
                    actual.check_runtime(live=True)

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "requires root for runuser")
    def test_actual_service_user_readability_including_parent_directories(self):
        for parent in [Path(self.temp.name), self.root, *self.release.parents]:
            if str(parent).startswith(self.temp.name):
                parent.chmod(0o755)
        (self.release / "java").chmod(0o755)
        (self.release / "java/linksoul-agentsdk-1.4.0.jar").chmod(0o644)
        for parent in self.protected.parents:
            if str(parent).startswith(self.temp.name):
                parent.chmod(0o755)
        self.protected.chmod(0o644)
        actual = module.Installer(self.root, self.package)
        actual.release = self.release
        original_run = module.run
        def fake_systemctl(command, **kwargs):
            if command[0] == "systemctl":
                return "nobody"
            return original_run(command, **kwargs)
        with patch.object(module, "run", side_effect=fake_systemctl):
            with self.assertRaises(RuntimeError):
                actual.check_access()
            (self.release / module.JAR).chmod(0o644)
            actual.check_access()
            (self.release / "java").chmod(0o700)
            with self.assertRaises(RuntimeError):
                actual.check_access()


if __name__ == "__main__":
    unittest.main(verbosity=2)
