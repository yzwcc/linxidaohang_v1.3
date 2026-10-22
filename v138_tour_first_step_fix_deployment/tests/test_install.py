"""Linux-only installation transactions against a temporary robot filesystem."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
REFERENCE = Path(os.environ.get("LINGXI_REFERENCE_ROOT", HERE / "reference"))
spec = importlib.util.spec_from_file_location("first_step_installer", HERE / "install.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BRIDGE, CLIENT, MANIFEST, CONTROL = module.BRIDGE, module.CLIENT, module.MANIFEST, module.CONTROL


class Harness(module.Installer):
    def __init__(self, root, package):
        super().__init__(root, package)
        self.active = True
        self.actions = []
        self.fail_next_start = False
        self.status = {
            "ok": True, "navigation": {"status": "idle"},
            "task": {"active": False}, "auto_return": {"state": "idle"},
        }

    def service_state(self):
        return self.active

    def stop(self):
        self.actions.append("stop")
        self.active = False

    def start(self):
        self.actions.append("start")
        if self.fail_next_start:
            self.fail_next_start = False
            raise RuntimeError("simulated startup failure")
        self.active = True

    def read_status(self):
        return self.status

    def check_access(self):
        compile(module.regular(self.release / BRIDGE), BRIDGE, "exec")


@unittest.skipUnless(os.name == "posix", "requires Linux ownership and symlink semantics")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "robot"
        self.package = Path(self.temp.name) / "package"
        self.package.mkdir()
        archive = Path(os.environ["LINGXI_ARCHIVE"]) if "LINGXI_ARCHIVE" in os.environ else next(
            (HERE / "dist").glob("*tour-first-step-fix-1.*.tar.gz")
        )
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                relative = member.name.split("/", 1)[1]
                self.assertTrue(module.safe_name(relative))
                target = self.package / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
        meta = json.loads((self.package / "release.json").read_bytes())
        self.release = self.root / "opt/sparkrobot/lingxi-nav/releases" / meta["allowed_releases"][0]
        self.release.mkdir(parents=True)
        (self.release.parent.parent / "current").symlink_to(self.release, target_is_directory=True)
        files = {
            BRIDGE: (REFERENCE / "runtime" / BRIDGE).read_bytes(),
            CLIENT: (REFERENCE / "navigation" / CLIENT.removeprefix("navigation/")).read_bytes(),
            CONTROL: (REFERENCE / "runtime" / CONTROL).read_bytes(),
        }
        manifest = json.loads((REFERENCE / "runtime/payload-manifest.json").read_bytes())
        for relative, content in files.items():
            path = self.release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(0o640)
        (self.release / MANIFEST).write_bytes(module.json_bytes(manifest))
        (self.release / MANIFEST).chmod(0o640)
        self.original = {name: (self.release / name).read_bytes() for name in (BRIDGE, MANIFEST)}
        self.installer = Harness(self.root, self.package)

    def backup(self):
        return next(self.installer.backups.iterdir())

    def assert_original(self):
        for name, content in self.original.items():
            self.assertEqual((self.release / name).read_bytes(), content)
            self.assertEqual((self.release / name).stat().st_mode & 0o777, 0o640)

    def test_package_checksums_and_lf_shell(self):
        module.verify_package(self.package)
        self.assertNotIn(b"\r", (self.package / "SHA256SUMS").read_bytes())
        subprocess.run(["bash", "-n", str(self.package / "install.sh")], check=True)

    def test_install_idempotence_preserves_metadata_and_silence_marker(self):
        before = json.loads(self.original[MANIFEST])
        self.installer.install()
        self.assertTrue(self.installer.active)
        self.assertEqual(self.installer.actions, ["stop", "start"])
        after = json.loads((self.release / MANIFEST).read_bytes())
        self.assertEqual(
            {k: v for k, v in after.items() if k not in {"files", "tour_first_step_fix"}},
            {k: v for k, v in before.items() if k != "files"},
        )
        self.assertEqual(after["tour_first_step_fix"], "1.0.0-20261022")
        for name in (BRIDGE, MANIFEST):
            self.assertEqual((self.release / name).stat().st_mode & 0o777, 0o640)
        self.installer.install()
        self.assertEqual(self.installer.actions, ["stop", "start"])
        self.assertEqual(len(list(self.installer.backups.iterdir())), 1)

    def test_rollback_restores_bytes_and_original_modes(self):
        self.installer.install()
        self.installer.rollback(self.backup())
        self.assert_original()
        self.assertTrue(self.installer.active)

    def test_stopped_services_remain_stopped_on_install_and_rollback(self):
        self.installer.active = False
        self.installer.install()
        self.assertEqual(self.installer.actions, ["stop"])
        self.installer.rollback(self.backup())
        self.assertFalse(self.installer.active)
        self.assertNotIn("start", self.installer.actions)
        self.assert_original()

    def test_start_failure_rolls_back_and_recovers(self):
        self.installer.fail_next_start = True
        with self.assertRaisesRegex(RuntimeError, "已恢复"):
            self.installer.install()
        self.assert_original()
        self.assertTrue(self.installer.active)
        self.assertEqual(self.installer.actions, ["stop", "start", "stop", "start"])

    def test_mid_write_failure_restores_both_files(self):
        write = module.atomic_write
        calls = []

        def fail_second(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 2:
                raise OSError("simulated disk write failure")
            return write(*args, **kwargs)

        with patch.object(module, "atomic_write", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "已恢复"):
                self.installer.install()
        self.assert_original()
        self.assertTrue(self.installer.active)

    def test_external_edit_during_failure_is_not_overwritten(self):
        write = module.atomic_write
        calls = []
        external = b"external change during installation\n"

        def change_during_second_write(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 2:
                (self.release / BRIDGE).write_bytes(external)
                raise OSError("simulated disk write failure")
            return write(*args, **kwargs)

        with patch.object(module, "atomic_write", side_effect=change_during_second_write):
            with self.assertRaisesRegex(RuntimeError, "自动恢复未完成"):
                self.installer.install()
        self.assertEqual((self.release / BRIDGE).read_bytes(), external)
        self.assertFalse(self.installer.active)
        self.assertTrue((self.backup() / "transaction.json").is_file())

    def test_recovery_start_failure_does_not_report_successful_restore(self):
        with patch.object(self.installer, "start", side_effect=RuntimeError("startup still broken")):
            with self.assertRaisesRegex(RuntimeError, "自动恢复未完成"):
                self.installer.install()
        self.assert_original()
        self.assertFalse(self.installer.active)

    def test_active_tour_is_rejected_without_stopping(self):
        self.installer.status["task"]["active"] = True
        with self.assertRaisesRegex(RuntimeError, "讲解任务"):
            self.installer.install()
        self.assertEqual(self.installer.actions, [])
        self.assert_original()

    def test_active_navigation_is_rejected_without_stopping(self):
        self.installer.status["navigation"]["status"] = "running"
        with self.assertRaisesRegex(RuntimeError, "导航仍在运行"):
            self.installer.install()
        self.assertEqual(self.installer.actions, [])

    def test_unknown_bridge_is_rejected(self):
        (self.release / BRIDGE).write_bytes(b"unknown local change\n")
        with self.assertRaisesRegex(RuntimeError, "拒绝覆盖"):
            self.installer.install()
        self.assertEqual(self.installer.actions, [])

    def test_other_release_is_rejected_before_stopping_or_backup(self):
        current = self.release.parent.parent / "current"
        other = self.release.parent / "unsupported-release"
        other.mkdir()
        current.unlink()
        current.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "本增量只支持"):
            self.installer.install()
        self.assertEqual(self.installer.actions, [])
        self.assertFalse(self.installer.backups.exists())
        self.assert_original()

    def test_damaged_manifest_is_rejected(self):
        manifest = json.loads(self.original[MANIFEST])
        next(e for e in manifest["files"] if e["path"] == BRIDGE)["sha256"] = "0" * 64
        (self.release / MANIFEST).write_bytes(module.json_bytes(manifest))
        with self.assertRaisesRegex(RuntimeError, "清单不一致"):
            self.installer.install()
        self.assertEqual(self.installer.actions, [])

    def test_unknown_client_or_controller_is_rejected(self):
        for name in (CLIENT, CONTROL):
            with self.subTest(name=name):
                old = (self.release / name).read_bytes()
                (self.release / name).write_bytes(old + b"\n")
                with self.assertRaisesRegex(RuntimeError, "依赖"):
                    self.installer.validate()
                (self.release / name).write_bytes(old)

    def test_later_patch_cannot_be_overwritten_on_rollback(self):
        self.installer.install()
        manifest = json.loads((self.release / MANIFEST).read_bytes())
        manifest["later_patch"] = True
        (self.release / MANIFEST).write_bytes(module.json_bytes(manifest))
        actions = list(self.installer.actions)
        with self.assertRaisesRegex(RuntimeError, "其他程序修改"):
            self.installer.rollback(self.backup())
        self.assertEqual(self.installer.actions, actions)

    def test_corrupt_backup_rejected_before_stop(self):
        self.installer.install()
        (self.backup() / "files" / BRIDGE).write_bytes(b"broken")
        actions = list(self.installer.actions)
        with self.assertRaisesRegex(RuntimeError, "备份损坏"):
            self.installer.rollback(self.backup())
        self.assertEqual(self.installer.actions, actions)

    def test_crlf_checksum_is_rejected(self):
        path = self.package / "SHA256SUMS"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(RuntimeError, "CRLF"):
            module.verify_package(self.package)

    def test_runtime_manifest_path_traversal_is_rejected(self):
        manifest = json.loads(self.original[MANIFEST])
        manifest["files"].append({"path": "../escape", "sha256": "0" * 64, "size": 0})
        (self.release / MANIFEST).write_bytes(module.json_bytes(manifest))
        with self.assertRaisesRegex(RuntimeError, "不安全"):
            self.installer.validate()

    def test_partial_or_unparseable_service_state_is_rejected(self):
        for output, rc in (
            ("lingxi_nav_bridge=running pid=12\nmap_label_server_v3=stopped\nvoice_nav_gateway=stopped", 1),
            ("unexpected output", 1),
        ):
            result = subprocess.CompletedProcess([], rc, output, "")
            with self.subTest(output=output):
                with patch.object(self.installer, "control", return_value=result):
                    with self.assertRaisesRegex(RuntimeError, "不完整"):
                        module.Installer.service_state(self.installer)


if __name__ == "__main__":
    unittest.main()
