from pathlib import Path
import importlib.util
import json
import os
import tempfile
import tarfile
import io
import unittest

HERE = Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

def load_script(name):
    spec = importlib.util.spec_from_file_location(name, HERE / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

config = load_script("release_config")
backup = load_script("backup_state")

def yaml_fixture(updates):
    tree = {}
    for item in updates:
        node = tree
        for key in item["path"][:-1]:
            node = node.setdefault(key, {})
        node[item["path"][-1]] = "1.0"
    def lines(data, depth=0):
        result = []
        for key, value in data.items():
            if isinstance(value, dict):
                result.append("  " * depth + key + ":")
                result += lines(value, depth + 1)
            else:
                result.append("  " * depth + key + ": " + value)
        return result
    return "\n".join(lines(tree)) + "\nuntouched: retain-this\n"

class ReleaseConfigTests(unittest.TestCase):
    def setup_root(self, root):
        p = root / config.VECTOR
        p.parent.mkdir(parents=True)
        p.write_text("main_config: ../config/vectorflux_common/a2/app2.yaml\n")
        policy = json.loads((HERE / "scripts/pnc_policy/policy.json").read_text(encoding="utf-8"))
        for n,k in ((config.APP2, "app2.yaml"), (config.MPPI, "mppi_controller.yaml")):
            p = root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(yaml_fixture(policy["files"][k]))
        p = root / config.SKILLS
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps([
            {"skill_name": "start_fixed_line_intro", "enabled": True},
            {"skill_name": "other_skill", "enabled": True},
        ]))

    def test_preflight_is_readonly_and_apply_preserves_unrelated_fields(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.setup_root(root)
            before = {str(p):p.read_bytes() for p in root.rglob("*") if p.is_file()}
            config.run(root, "preflight")
            self.assertEqual(before, {str(p):p.read_bytes() for p in root.rglob("*") if p.is_file()})
            config.run(root, "apply")
            config.run(root, "verify")
            self.assertIn("untouched: retain-this", (root / config.APP2).read_text())
            skills = json.loads((root / config.SKILLS).read_text())
            self.assertFalse(skills[0]["enabled"])
            self.assertTrue(skills[1]["enabled"])

    def test_invalid_second_yaml_does_not_partially_apply_first(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.setup_root(root)
            first = (root / config.APP2).read_bytes()
            (root / config.MPPI).write_text("unrecognized: 1\n")
            with self.assertRaises(ValueError):
                config.run(root, "apply")
            self.assertEqual(first, (root / config.APP2).read_bytes())

    def test_non_a2_rejected_and_explicit_skip_supported(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.setup_root(root)
            (root / config.VECTOR).write_text("main_config: ../other-robot.yaml")
            before = (root / config.APP2).read_bytes()
            with self.assertRaises(ValueError):
                config.run(root, "apply")
            self.assertEqual(config.run(root, "apply", True)["pnc"], "explicitly_skipped")
            self.assertEqual(before, (root / config.APP2).read_bytes())

    def test_absent_legacy_skill_mapping_is_idempotent(self):
        data = [{"skill_name": "other", "enabled": True}]
        self.assertEqual(config.disable_legacy_skill(data), 0)
        self.assertEqual(config.disable_legacy_skill(data, verify=True), 0)
        self.assertTrue(data[0]["enabled"])

    def test_invalid_skills_rejected_before_any_firmware_change(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.setup_root(root)
            (root / config.SKILLS).write_text("{invalid")
            before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(ValueError):
                config.run(root, "apply")
            self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_commented_a2_path_does_not_enable_policy_for_other_robot(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.setup_root(root)
            (root / config.VECTOR).write_text(
                "# main_config: ../config/vectorflux_common/a2/app2.yaml\n"
                "main_config: ../config/another-model/app.yaml\n"
            )
            before = (root / config.APP2).read_bytes()
            with self.assertRaises(ValueError):
                config.run(root, "apply")
            self.assertEqual(before, (root / config.APP2).read_bytes())

class BackupTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Linux symlink checks")
    def test_restore_rejects_real_current_directory_before_moving_state(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p = root / "etc/sparkrobot/lingxi-nav/a"
            p.parent.mkdir(parents=True)
            p.write_text("before")
            output = backup.backup(root)
            current = root / backup.LINKS[0]
            current.mkdir(parents=True)
            p.write_text("after")
            with self.assertRaises(ValueError):
                backup.restore(root, output)
            self.assertEqual(p.read_text(), "after")
            self.assertTrue(current.is_dir())

    @unittest.skipIf(os.name == "nt", "Linux symlink checks")
    def test_relative_current_link_can_be_restored(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            base = root / "opt/sparkrobot/lingxi-nav"
            (base / "releases/old").mkdir(parents=True)
            (base / "current").symlink_to("releases/old")
            output = backup.backup(root)
            (base / "current").unlink()
            backup.restore(root, output)
            self.assertEqual(os.readlink(base / "current"), "releases/old")

    def test_restore_rejects_archive_symlink_traversal_before_state_changes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p = root / "etc/sparkrobot/lingxi-nav/a"
            p.parent.mkdir(parents=True)
            p.write_text("before")
            output = backup.backup(root)
            archive = output / "state.tar.gz"
            with tarfile.open(archive, "w:gz") as f:
                link = tarfile.TarInfo("etc/sparkrobot/lingxi-nav/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "/tmp"
                f.addfile(link)
                child = tarfile.TarInfo(link.name + "/escape")
                child.size = 1
                f.addfile(child, io.BytesIO(b"x"))
            metadata = json.loads((output / "metadata.json").read_text())
            metadata["sha256"] = backup.digest(archive)
            (output / "metadata.json").write_text(json.dumps(metadata))
            p.write_text("after")
            with self.assertRaises(ValueError):
                backup.restore(root, output)
            self.assertEqual(p.read_text(), "after")

    @unittest.skipIf(os.name == "nt", "符号链接恢复在 Linux 隔离环境验证")
    def test_version_links_and_prior_overrides_are_restored(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            base = root / "opt/sparkrobot/lingxi-nav"
            (base / "releases/old").mkdir(parents=True)
            (base / "releases/new").mkdir()
            current = base / "current"
            current.symlink_to("/opt/sparkrobot/lingxi-nav/releases/old")
            dropin = root / "etc/systemd/system/lingxi-java-agent-robot.service.d/20-wake-gate-r10.conf"
            dropin.parent.mkdir(parents=True)
            dropin.write_text("legacy override\n")
            output = backup.backup(root)
            current.unlink()
            current.symlink_to("/opt/sparkrobot/lingxi-nav/releases/new")
            dropin.unlink()
            backup.restore(root, output)
            self.assertEqual(os.readlink(current), "/opt/sparkrobot/lingxi-nav/releases/old")
            self.assertEqual(dropin.read_text(), "legacy override\n")

    def test_full_state_roundtrip_and_keeps_pre_restore_snapshot(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p = root / "etc/sparkrobot/lingxi-nav/lingxi-nav.properties"
            p.parent.mkdir(parents=True)
            p.write_text("credential-placeholder\n")
            p.chmod(0o640)
            state = root / "var/lib/sparkrobot/lingxi-nav/maps/demo.json"
            state.parent.mkdir(parents=True)
            state.write_text('{"loop_count":3}')
            output = backup.backup(root)
            p.write_text("changed\n")
            state.write_text('{"loop_count":9}')
            result = backup.restore(root, output)
            self.assertEqual(p.read_text(), "credential-placeholder\n")
            self.assertEqual(state.read_text(), '{"loop_count":3}')
            self.assertTrue((Path(result["before_restore_snapshot"]) / "state.tar.gz").is_file())
            if os.name != "nt":
                self.assertEqual(p.stat().st_mode & 0o777, 0o640)

    def test_tampered_backup_is_rejected_before_state_mutation(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p = root / "etc/sparkrobot/lingxi-nav/a"
            p.parent.mkdir(parents=True)
            p.write_text("before")
            output = backup.backup(root)
            (output / "state.tar.gz").write_bytes(b"tampered")
            p.write_text("after")
            with self.assertRaises(ValueError):
                backup.restore(root, output)
            self.assertEqual(p.read_text(), "after")

if __name__ == "__main__":
    unittest.main()
