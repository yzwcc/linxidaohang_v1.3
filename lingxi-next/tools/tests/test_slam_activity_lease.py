"""Linux resource admission and inherited lifetime tests, with synthetic local processes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.activity_lease import ActivityBusy, ActivityGate, LEASE_ENV, inherited_activity_fds
from lingxi_slam.process_supervisor import ProcessSupervisor

LINUX_USER = sys.platform == "linux" and os.geteuid() != 0
CHILD = ROOT / "tools/tests/fixtures/slam_activity_child.py"


@unittest.skipUnless(LINUX_USER, "requires ordinary-user Linux file locks")
class ActivityLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lxlease-")
        self.root = Path(self.temporary.name)
        self.gate = ActivityGate(self.root / "activity.lock")
        self.children, self.leases, self.orphans = [], [], []

    def tearDown(self):
        (self.root / "release").touch()
        for process in self.children:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        for pid in self.orphans:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for lease in self.leases:
            lease.close()
        self.temporary.cleanup()

    def acquire(self):
        lease = self.gate.acquire()
        self.leases.append(lease)
        return lease

    def spawn(self, mode, **kwargs):
        process = subprocess.Popen([sys.executable, "-u", str(CHILD), mode, str(self.root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, **kwargs)
        self.children.append(process)
        return process

    def wait_available(self):
        deadline = time.monotonic() + 5
        while True:
            try:
                self.acquire().close()
                return
            except ActivityBusy:
                if time.monotonic() >= deadline:
                    self.fail("activity_lease_release_deadline")
                time.sleep(.01)

    def wait_file(self, name):
        deadline = time.monotonic() + 5
        while not (self.root / name).exists():
            if time.monotonic() >= deadline:
                self.fail("synthetic_process_start_deadline")
            time.sleep(.01)
        return json.loads((self.root / name).read_text())

    def test_independent_gate_is_busy_until_final_descriptor_closes_and_inode_is_stable(self):
        lease = self.acquire()
        inode = self.gate.path.stat().st_ino
        inherited = os.dup(lease.fileno())
        try:
            lease.close()
            lease.close()
            with self.assertRaises(ActivityBusy):
                ActivityGate(self.gate.path).acquire()
            with self.assertRaises(ValueError):
                lease.fileno()
        finally:
            os.close(inherited)
        self.acquire().close()
        self.assertEqual(inode, self.gate.path.stat().st_ino)
        self.assertEqual(0o600, self.gate.path.stat().st_mode & 0o777)

    def test_two_concurrent_processes_admit_exactly_one_until_it_exits(self):
        first, second = self.spawn("contender"), self.spawn("contender")
        for process in (first, second):
            process.stdin.write("go\n")
            process.stdin.flush()
        results = [process.stdout.readline().strip() for process in (first, second)]
        self.assertCountEqual(["busy", "held"], results)
        with self.assertRaises(ActivityBusy):
            self.acquire()
        winner = (first, second)[results.index("held")]
        winner.communicate("release\n", timeout=5)
        self.wait_available()

    def test_supervisor_child_keeps_lease_after_owner_death_without_other_inheritable_fds(self):
        controller = self.spawn("owner")
        value = json.loads(controller.stdout.readline())
        self.orphans.append(value["pid"])
        worker = self.wait_file("worker.json")
        self.assertTrue(worker["unrelatedClosed"])
        self.assertGreaterEqual(worker["leaseFd"], 3)
        controller.kill()
        controller.communicate(timeout=5)
        with self.assertRaises(ActivityBusy):
            self.acquire()
        (self.root / "release").touch()
        self.wait_available()

    def test_independent_reload_subprocess_retains_lease_after_intermediate_exit(self):
        lease = self.acquire()
        relay = self.spawn("relay", env={**os.environ, LEASE_ENV: str(lease.fileno())},
                           pass_fds=(lease.fileno(),))
        output, errors = relay.communicate(timeout=5)
        self.assertEqual(0, relay.returncode, errors)
        self.orphans.append(int(output))
        self.wait_file("sleeper.json")
        lease.close()
        with self.assertRaises(ActivityBusy):
            self.acquire()
        (self.root / "release").touch()
        self.wait_available()

    def test_supervisor_start_failure_and_unused_owner_release_the_lease(self):
        for mode in ("unused", "spawn-failure", "thread-failure", "normal"):
            with self.subTest(mode=mode):
                owner = ProcessSupervisor([sys.executable, "-u", str(ROOT / "tools/tests/fixtures/slam_process_child.py"),
                                           "normal"], cwd=self.root,
                                          environment={"PYTHONPATH": str(ROOT / "robot-slam")},
                                          stderr_path=self.root / (mode + ".log"), activity_lease=self.acquire())
                try:
                    if mode == "spawn-failure":
                        with patch("lingxi_slam.process_supervisor.subprocess.Popen", side_effect=OSError("synthetic")):
                            with self.assertRaises(OSError):
                                owner.start()
                    elif mode == "thread-failure":
                        with patch("lingxi_slam.process_supervisor.threading.Thread.start", side_effect=RuntimeError("synthetic")):
                            with self.assertRaises(RuntimeError):
                                owner.start()
                    elif mode == "normal":
                        owner.start()
                        deadline = time.monotonic() + 5
                        while owner.snapshot()["state"] == "starting" and time.monotonic() < deadline:
                            time.sleep(.01)
                        self.assertEqual("running", owner.snapshot()["state"])
                        with self.assertRaises(ActivityBusy):
                            self.acquire()
                finally:
                    state = owner.stop()
                self.assertTrue(state["cleanupComplete"])
                self.assertFalse(state["processAlive"])
                self.assertFalse(state["monitorAlive"])
                self.wait_available()

    def test_unconfirmed_process_cleanup_keeps_parent_lease(self):
        owner = ProcessSupervisor([sys.executable, "-c", "pass"], cwd=self.root,
                                  environment={}, stderr_path=self.root / "unclean.log",
                                  activity_lease=self.acquire())
        process = unittest.mock.Mock()
        process.poll.return_value = 0
        owner.process = process
        with patch.object(owner, "_group_has_live_members", side_effect=OSError("synthetic cleanup unavailable")):
            owner._cleanup()
        self.assertFalse(owner.snapshot()["cleanupComplete"])
        with self.assertRaises(ActivityBusy):
            self.acquire()
        with patch.object(owner, "_group_has_live_members", return_value=False):
            owner._cleanup()
        self.wait_available()

    def test_invalid_descriptor_setting_and_reserved_environment_are_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual((), inherited_activity_fds())
        for value in ("", "-1", "2", "03", "true", "2147483648"):
            with self.subTest(value=value), patch.dict(os.environ, {LEASE_ENV: value}), self.assertRaises(ValueError):
                inherited_activity_fds()
        lease = self.acquire()
        with patch.dict(os.environ, {LEASE_ENV: str(lease.fileno())}):
            self.assertEqual((lease.fileno(),), inherited_activity_fds())
        with self.assertRaises(ValueError):
            ProcessSupervisor([sys.executable, "-c", "pass"], cwd=self.root,
                              environment={LEASE_ENV: str(lease.fileno())}, stderr_path=self.root / "reserved.log")

    def test_catalog_and_lock_must_remain_private(self):
        self.acquire().close()
        os.chmod(self.gate.path, 0o644)
        with self.assertRaises(ValueError):
            self.acquire()
        os.chmod(self.gate.path, 0o600)
        os.chmod(self.root, 0o755)
        try:
            with self.assertRaises(ValueError):
                self.acquire()
        finally:
            os.chmod(self.root, 0o700)


if __name__ == "__main__":
    unittest.main()
