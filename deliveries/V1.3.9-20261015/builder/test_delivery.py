from pathlib import Path
import importlib.util
import json
import os
import tempfile
import unittest

HERE = Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

config = load("release_config")
backup = load("backup_state")

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
        policy = json.loads((HERE / "pnc_policy/policy.json").read_text(encoding="utf-8"))
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

class BackupTests(unittest.TestCase):
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
