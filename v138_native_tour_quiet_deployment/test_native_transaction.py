import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import install as installer
from native_tour_quiet import NativeConfig, encoded
import test_install as prior
from test_native_tour_quiet import DOCUMENT, TX


class CoordinatedInstaller(prior.FakeInstaller):
    def voice_state(self):
        return True

    def restart_voice(self, active):
        self.events.append("voice-restart" if active else "voice-kept-stopped")


class FakeRemote:
    def __init__(self, root):
        self.native = NativeConfig(root)
        self.calls = []
        self.fail_after_apply = False
        self.fail_reload_once = False
        self.fail_rollback = False
        self.fail_rollback_only = False

    def call(self, mode, transaction=None, expected=None):
        self.calls.append(mode)
        if mode == "plan":
            return self.native.plan()
        if mode == "verify":
            value = self.native.plan()
            if value["changes"]:
                raise RuntimeError("not disabled")
            return value
        if mode == "apply":
            value = self.native.apply(transaction, expected)
            if self.fail_after_apply:
                self.fail_after_apply = False
                raise OSError("lost remote acknowledgement")
            return value
        if mode in {"rollback", "check-rollback"}:
            if self.fail_rollback or (self.fail_rollback_only and mode == "rollback"):
                raise OSError("SOC2 unreachable during rollback")
            return self.native.rollback(transaction, check=mode == "check-rollback")
        if mode == "recover":
            backup = self.native.backup_path(transaction)
            if (backup / "transaction.json").exists():
                return self.native.rollback(transaction)
            return {"recovery": "not_started"}
        raise AssertionError(mode)

    def service(self):
        return "active"

    def reload(self, state):
        self.calls.append("reload")
        if self.fail_reload_once:
            self.fail_reload_once = False
            raise RuntimeError("native restart failed")


class TransactionTests(prior.InstallTests):
    def setUp(self):
        super().setUp()
        self.installer = CoordinatedInstaller(self.root, self.package)
        self.local = NativeConfig(self.root)
        remote_root = Path(self.temp.name) / "soc2"
        remote_root.mkdir()
        self.remote = FakeRemote(remote_root)
        self.original = encoded(DOCUMENT)
        for native in (self.local, self.remote.native):
            path = native.path("/agibot/software/agent_cfg/skills.json")
            path.parent.mkdir(parents=True)
            path.write_bytes(self.original)
        self.record = self.root / "state/transactions" / (TX + ".json")

    def install_all(self):
        return installer.install_transaction(
            self.installer, self.local, self.remote,
            self.local.plan(), self.remote.native.plan(), self.record, TX)

    def assert_native_original(self):
        for native in (self.local, self.remote.native):
            self.assertEqual(native.path("/agibot/software/agent_cfg/skills.json").read_bytes(),
                             self.original)

    def test_coordinated_apply_and_full_rollback(self):
        record = self.install_all()
        self.assertEqual(record["state"], "installed")
        self.assertEqual(self.local.plan()["changes"], 0)
        self.assertEqual(self.remote.native.plan()["changes"], 0)
        self.assertEqual((self.release / installer.JAR).read_bytes(), self.new)
        self.assertTrue(self.installer.java and self.installer.navigation)
        with patch.object(installer, "STATE", self.root / "state"):
            installer.rollback_transaction(self.installer, self.local, self.remote, self.record)
        self.assert_original()
        self.assert_native_original()
        self.assertTrue(self.installer.java and self.installer.navigation)

    def test_lost_remote_apply_ack_rolls_back_both_nodes(self):
        self.remote.fail_after_apply = True
        with self.assertRaisesRegex(OSError, "lost remote"):
            self.install_all()
        self.assert_original()
        self.assert_native_original()
        self.assertIn("recover", self.remote.calls)
        self.assertTrue(self.installer.java and self.installer.navigation)
        self.assertEqual(json.loads(self.record.read_bytes())["state"], "restored_after_error")

    def test_native_reload_failure_restores_runtime_and_configs(self):
        self.remote.fail_reload_once = True
        with self.assertRaisesRegex(RuntimeError, "native restart"):
            self.install_all()
        self.assert_original()
        self.assert_native_original()
        self.assertTrue(self.installer.java and self.installer.navigation)

    def test_runtime_failure_restores_both_native_configs(self):
        self.installer.fail_access = True
        with self.assertRaises(RuntimeError):
            self.install_all()
        self.assert_original()
        self.assert_native_original()

    def test_remote_rollback_outage_does_not_prevent_local_recovery(self):
        self.remote.fail_reload_once = True
        self.remote.fail_rollback = True
        with self.assertRaisesRegex(RuntimeError, "自动恢复未完成"):
            self.install_all()
        self.assert_original()
        self.assertEqual(self.local.path("/agibot/software/agent_cfg/skills.json").read_bytes(),
                         self.original)
        self.assertTrue(self.installer.java and self.installer.navigation)
        record = json.loads(self.record.read_bytes())
        self.assertEqual(record["state"], "recovery_required")
        self.assertTrue(any("SOC2 unreachable" in value for value in record["recovery_errors"]))

    def test_journal_disk_failure_cannot_bypass_recovery(self):
        real_write = Path.write_bytes
        writes = [0]

        def write(path, data):
            if path == self.record:
                writes[0] += 1
                if writes[0] > 1:
                    raise OSError("simulated journal disk full")
            return real_write(path, data)

        with patch.object(Path, "write_bytes", write):
            with self.assertRaisesRegex(OSError, "journal disk full"):
                self.install_all()
        self.assert_original()
        self.assert_native_original()
        self.assertTrue(self.installer.java and self.installer.navigation)
        self.assertNotIn("apply", self.remote.calls)

    def test_manual_rollback_outage_restores_local_services_and_can_be_retried(self):
        self.install_all()
        self.remote.fail_rollback_only = True
        with patch.object(installer, "STATE", self.root / "state"):
            with self.assertRaisesRegex(RuntimeError, "回滚未全部完成"):
                installer.rollback_transaction(self.installer, self.local, self.remote, self.record)
        self.assert_original()
        self.assertTrue(self.installer.java and self.installer.navigation)
        self.assertEqual(self.local.path("/agibot/software/agent_cfg/skills.json").read_bytes(),
                         self.original)
        self.assertEqual(json.loads(self.record.read_bytes())["state"], "rollback_incomplete")
        self.remote.fail_rollback_only = False
        with patch.object(installer, "STATE", self.root / "state"):
            installer.rollback_transaction(self.installer, self.local, self.remote, self.record)
        self.assert_original()
        self.assert_native_original()
        self.assertEqual(json.loads(self.record.read_bytes())["state"], "rolled_back")

    def test_coordinated_install_keeps_stopped_services_stopped(self):
        self.installer.java = False
        self.installer.navigation = False
        self.install_all()
        self.assertFalse(self.installer.java or self.installer.navigation)

    def test_busy_robot_rejected_before_native_changes(self):
        self.installer.busy = True
        with self.assertRaisesRegex(RuntimeError, "busy"):
            self.install_all()
        self.assert_native_original()
        self.assertNotIn("apply", self.remote.calls)
        self.assertNotIn("java-stop", self.installer.events)

    def test_rollback_detects_changed_native_config_before_stop(self):
        self.install_all()
        stops = self.installer.events.count("java-stop")
        path = self.remote.native.path("/agibot/software/agent_cfg/skills.json")
        changed = encoded({"later": "customer change"})
        path.write_bytes(changed)
        with patch.object(installer, "STATE", self.root / "state"):
            with self.assertRaisesRegex(RuntimeError, "refusing rollback"):
                installer.rollback_transaction(self.installer, self.local, self.remote, self.record)
        self.assertEqual(self.installer.events.count("java-stop"), stops)
        self.assertEqual(path.read_bytes(), changed)


class SshSettingsTests(unittest.TestCase):
    def test_literal_config_never_executes_shell_expressions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.env"
            for content in (
                "SOC2_HOST=$(touch /tmp/wrong)\nSOC2_USER=run\n",
                "SOC2_HOST=`hostname`\nSOC2_USER=run\n",
                "SOC2_USER=run\n",
                "SOC2_HOST=10.0.1.42\nSOC2_USER=bad;command\n",
            ):
                path.write_text(content)
                with self.assertRaises(RuntimeError):
                    installer.ssh_settings(path)

    def test_explicit_host_key_and_identity_are_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key, known = root / "identity", root / "known_hosts"
            key.touch()
            known.touch()
            path = root / "runtime.env"
            path.write_text("\n".join((
                "SOC2_HOST=10.0.1.99", "SOC2_USER=robot",
                "SOC2_SSH_KEY=" + str(key), "SOC2_KNOWN_HOSTS_FILE=" + str(known))))
            options, destination = installer.ssh_settings(path)
            self.assertEqual(destination, "robot@10.0.1.99")
            self.assertIn("StrictHostKeyChecking=yes", options)
            self.assertIn("UserKnownHostsFile=" + str(known), options)
            self.assertIn(str(key), options)


class RemoteTransportTests(unittest.TestCase):
    def remote(self):
        value = object.__new__(installer.RemoteNative)
        value.directory = "/tmp/lingxi-tour-quiet.1234567890"
        value.options = []
        value.destination = "robot@configured-host"
        value.serial = 0
        return value

    def test_successful_apply_is_not_retried(self):
        remote = self.remote()
        success = subprocess.CompletedProcess([], 0, "native output", "")
        with patch.object(installer.subprocess, "run", return_value=success) as run:
            with patch.object(remote, "output", return_value='{"verified":true}') as output:
                result = remote.call("apply", TX, "fingerprint")
        self.assertTrue(result["verified"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(output.call_count, 1)
        self.assertIn("configured-host", run.call_args.args[0][-2])

    def test_lost_response_never_retries_native_apply(self):
        remote = self.remote()
        failure = subprocess.CompletedProcess([], 255, "", "connection closed")
        with patch.object(installer.subprocess, "run", return_value=failure) as run:
            with patch.object(remote, "ssh") as ssh:
                with self.assertRaisesRegex(RuntimeError, "connection closed"):
                    remote.call("apply", TX, "fingerprint")
        self.assertEqual(run.call_count, 1)
        ssh.assert_not_called()

    def test_helper_failure_is_not_misclassified_as_password_request(self):
        remote = self.remote()
        failure = subprocess.CompletedProcess([], 1, "", "Native tour configuration failed: invalid JSON")
        with patch.object(installer.subprocess, "run", return_value=failure) as run:
            with patch.object(remote, "ssh") as ssh:
                with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                    remote.call("apply", TX, "fingerprint")
        self.assertEqual(run.call_count, 1)
        ssh.assert_not_called()

    def test_password_requirement_without_terminal_is_explicit(self):
        remote = self.remote()
        failure = subprocess.CompletedProcess([], 1, "", "sudo: a password is required")
        with patch.object(installer.subprocess, "run", return_value=failure):
            with patch.object(installer.sys, "stdin") as stream:
                stream.isatty.return_value = False
                with self.assertRaisesRegex(RuntimeError, "交互终端"):
                    remote.call("plan")

    def test_authentication_only_failure_can_prompt_in_terminal(self):
        remote = self.remote()
        failure = subprocess.CompletedProcess([], 1, "", "sudo: a password is required")
        with patch.object(installer.subprocess, "run", return_value=failure):
            with patch.object(installer.sys, "stdin") as stream:
                stream.isatty.return_value = True
                with patch.object(remote, "ssh") as ssh:
                    with patch.object(remote, "output", return_value='{"matches":3}'):
                        self.assertEqual(remote.call("plan")["matches"], 3)
        self.assertEqual(ssh.call_count, 1)
        self.assertIs(ssh.call_args.kwargs["tty"], True)


if __name__ == "__main__":
    unittest.main()
