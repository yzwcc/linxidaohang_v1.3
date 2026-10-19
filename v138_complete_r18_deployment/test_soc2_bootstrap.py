"""Run the actual bootstrap shell with isolated accounts and SSH boundary peers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPT = Path(os.environ.get("SOC2_SETUP_SCRIPT", HERE / "setup_soc2_access.sh"))

PEER = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ["QA_ROOT"])
with (root / "events.jsonl").open("a") as stream:
    stream.write(json.dumps([name, args]) + "\n")
if name in {"chown", "install"}:
    if name == "install":
        for arg in args:
            if arg.startswith("/"): pathlib.Path(arg).mkdir(parents=True, exist_ok=True)
    raise SystemExit(0)
if name == "getent":
    print("root:x:0:0:root:" + str(root / "root-home") + ":/bin/bash")
    raise SystemExit(0)
if name == "runuser":
    command = args[args.index("--") + 1:]
    raise SystemExit(subprocess.run(command).returncode)
known = root / "root-home/.ssh/known_hosts"
strict = None
for arg in args:
    if arg.startswith("UserKnownHostsFile="): known = pathlib.Path(arg.split("=", 1)[1])
    if arg.startswith("StrictHostKeyChecking="): strict = arg.split("=", 1)[1]
public = (root / "server-key.pub").read_text().split()
line = "10.0.1.42 " + public[0] + " " + public[1] + "\n"
if known.exists() and known.read_text().strip():
    found = subprocess.run(["ssh-keygen", "-F", "10.0.1.42", "-f", str(known)],
                           capture_output=True, text=True).stdout
    if line.strip() not in found:
        print("REMOTE HOST IDENTIFICATION HAS CHANGED", file=sys.stderr)
        raise SystemExit(255)
elif strict == "accept-new":
    known.parent.mkdir(parents=True, exist_ok=True)
    known.write_text(line)
else:
    print("Host key verification failed", file=sys.stderr)
    raise SystemExit(255)
if name != "ssh-copy-id":
    if "-i" not in args or args[args.index("-i") + 1] != os.environ["QA_KEY"]:
        print("wrong private key", file=sys.stderr)
        raise SystemExit(255)
'''


@unittest.skipUnless(os.name == "posix" and os.geteuid() == 0, "isolated Linux root")
class Soc2BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lingxi-soc2-bootstrap-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config"
        self.config.mkdir()
        self.key = self.config / "soc2-ed25519"
        self.known = self.root / "state/ssh/soc2-known-hosts"
        self.known.parent.mkdir(parents=True)
        self.known.touch()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("ssh-copy-id", "ssh", "scp", "chown", "install", "getent", "runuser"):
            path = self.bin / name
            path.write_text(PEER)
            path.chmod(0o755)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f",
                        str(self.root / "server-key")], check=True)
        self.script = self.root / "setup.sh"
        text = SCRIPT.read_text(encoding="utf-8")
        text = text.replace('CONFIG_DIR="/etc/sparkrobot/lingxi-nav"',
                            'CONFIG_DIR="${QA_CONFIG:?}"')
        self.script.write_text(text)
        self.env = os.environ | {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "QA_ROOT": str(self.root), "QA_CONFIG": str(self.config),
            "QA_KEY": str(self.key),
            "SPARKROBOT_RUNTIME_ENV": str(self.config / "runtime.env"),
            "SOC2_KNOWN_HOSTS_FILE": str(self.known),
        }
        self.runtime()

    def runtime(self, custom_key=None):
        if custom_key:
            self.key = custom_key
            self.env["QA_KEY"] = str(custom_key)
        (self.config / "runtime.env").write_text(
            "SOC2_HOST=10.0.1.42\nSOC2_USER=run\n"
            + ("SOC2_SSH_KEY='" + str(self.key) + "'\n" if custom_key else "")
            + "SOC2_KNOWN_HOSTS_FILE='" + str(self.known) + "'\n")

    def invoke(self):
        return subprocess.run(["bash", str(self.script)], env=self.env, capture_output=True,
                              text=True, timeout=15)

    def events(self):
        return [json.loads(line) for line in (self.root / "events.jsonl").read_text().splitlines()]

    def test_fresh_bootstrap_enrolls_the_runtime_known_hosts_and_checks_as_run(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        found = subprocess.run(["ssh-keygen", "-F", "10.0.1.42", "-f", str(self.known)],
                               capture_output=True, text=True)
        self.assertEqual(found.returncode, 0, "runtime known_hosts was never enrolled")
        self.assertFalse((self.root / "root-home/.ssh/known_hosts").exists(),
                         "bootstrap must not enroll a separate root-only database")
        self.assertTrue(any(name == "runuser" and args[:2] == ["-u", "run"]
                            and "StrictHostKeyChecking=yes" in args
                            for name, args in self.events()), "runtime account was not verified")
        for name, args in self.events():
            if name in {"ssh-copy-id", "scp", "ssh"}:
                self.assertIn("UserKnownHostsFile=" + str(self.known), args)

    def test_custom_runtime_key_and_paths_with_spaces_are_shared(self):
        custom = self.root / "custom key/id_ed25519"
        custom.parent.mkdir()
        self.runtime(custom)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(custom.is_file())
        for name, args in self.events():
            if name in {"scp", "ssh"}:
                self.assertEqual(args[args.index("-i") + 1], str(custom))

    def test_changed_host_key_is_rejected_before_remote_write(self):
        self.known.write_text("10.0.1.42 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEwrong\n")
        original = self.known.read_bytes()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.known.read_bytes(), original)
        self.assertFalse(any(name == "scp" for name, _ in self.events()))

    def test_repeat_setup_preserves_the_enrolled_fingerprint(self):
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.known.read_bytes()
        second = self.invoke()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.known.read_bytes(), before)

    def test_runtime_account_validation_failure_cannot_report_success(self):
        path = self.bin / "runuser"
        path.write_text("#!/bin/bash\necho 'runtime key unreadable' >&2\nexit 1\n")
        path.chmod(0o755)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SOC2 语音运维通道已配置", result.stdout)


if __name__ == "__main__":
    unittest.main()
