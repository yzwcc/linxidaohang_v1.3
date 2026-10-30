"""Opt-in real user-systemd notification smoke checks; no sensors or robot access."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0
                     and os.environ.get("LINGXI_SLAM_TEST_SYSTEMD") == "1",
                     "requires explicit ordinary-user systemd smoke environment")
class SystemdReadiness(unittest.TestCase):
    def setUp(self):
        self.name = "lingxi-slam-synthetic-" + uuid.uuid4().hex + ".service"

    def tearDown(self):
        for command in ("stop", "reset-failed"):
            subprocess.run(["/usr/bin/systemctl", "--user", command, self.name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)

    def start(self, code):
        return subprocess.run(["/usr/bin/systemd-run", "--user", "--quiet", "--collect", "--unit", self.name,
            "--property", "Type=notify", "--property", "NotifyAccess=main", "--property", "Restart=no",
            "--property", "KillMode=control-group", "--property", "TimeoutStartSec=3",
            "--property", "TimeoutStopSec=3", "--property", "UMask=0077",
            sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=15, check=False)

    def test_real_main_process_readiness_completes_start_and_explicit_stop(self):
        code = ("import sys,time; sys.path[:0]=" + repr([
            str(ROOT / "robot-runtime/python"), str(ROOT / "robot-slam")])
            + "; import managed_slam; managed_slam.notify_ready({'service':'synthetic','event':'listening'}); time.sleep(60)")
        result = self.start(code)
        self.assertEqual(0, result.returncode, result.stderr)
        result = subprocess.run(["/usr/bin/systemctl", "--user", "show", self.name,
            "--property=ActiveState", "--property=MainPID"], capture_output=True, text=True, timeout=5, check=True)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual("active", values["ActiveState"])
        pid = values["MainPID"]
        self.assertTrue(Path("/proc", pid).exists())
        subprocess.run(["/usr/bin/systemctl", "--user", "stop", self.name], timeout=10, check=True)
        self.assertFalse(Path("/proc", pid).exists())

    def test_no_notification_never_reports_service_ready(self):
        result = self.start("import time; time.sleep(60)")
        self.assertNotEqual(0, result.returncode)
        result = subprocess.run(["/usr/bin/systemctl", "--user", "is-active", self.name],
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertNotEqual("active", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
