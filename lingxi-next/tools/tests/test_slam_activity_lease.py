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
from lingxi_slam.activity_lease import ActivityBusy, ActivityGate, OperationGate, LEASE_ENV, OPERATION_ENV, inherited_activity_fds
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

    def spawn(self, mode, *args, **kwargs):
        process = subprocess.Popen([sys.executable, "-u", str(CHILD), mode, str(self.root), *args],
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
        self.assertEqual(2, len(worker['leaseFds']))
        controller.kill()
        controller.communicate(timeout=5)
        with self.assertRaises(ActivityBusy):
            self.acquire()
        with self.assertRaises(ActivityBusy):
            OperationGate(self.root, shared=True).acquire()
        (self.root / "release").touch()
        self.wait_available()
        OperationGate(self.root).acquire().close()

    def test_independent_shared_processes_coexist_and_exclusive_requests_are_not_queued(self):
        first = self.spawn('operation-contender', 'shared')
        second = self.spawn('operation-contender', 'shared')
        for process in (first, second):
            process.stdin.write('go\n')
            process.stdin.flush()
            self.assertEqual('held', process.stdout.readline().strip())
        with self.assertRaises(ActivityBusy):
            OperationGate(self.root).acquire()
        rejected = self.spawn('operation-contender', 'exclusive')
        rejected.stdin.write('go\n')
        rejected.stdin.flush()
        self.assertEqual('busy', rejected.stdout.readline().strip())
        rejected.communicate(timeout=5)
        first.communicate('release\n', timeout=5)
        with self.assertRaises(ActivityBusy):
            OperationGate(self.root).acquire()
        second.communicate('release\n', timeout=5)
        OperationGate(self.root).acquire().close()

    def test_exclusive_blocks_shared_and_second_exclusive_until_final_descriptor_closes(self):
        held = OperationGate(self.root).acquire()
        self.leases.append(held)
        duplicate = os.dup(held.fileno())
        held.close()
        try:
            for shared in (False, True):
                with self.assertRaises(ActivityBusy):
                    OperationGate(self.root, shared=shared).acquire()
        finally:
            os.close(duplicate)
        OperationGate(self.root, shared=True).acquire().close()

    def test_nav2_owner_death_retires_workloads_before_unlock_and_guard_exits(self):
        controller = self.spawn('nav-owner')
        value = json.loads(controller.stdout.readline())
        self.orphans.append(value['pid'])
        worker = self.wait_file('nav-worker.json')
        descendant = self.wait_file('descendant.json')
        guard = self.process_identity(value['pid'])
        identities = {pid: self.process_identity(pid) for pid in
                      (worker['pid'], worker['descendant'], descendant['pid'])}
        with self.assertRaises(ActivityBusy):
            OperationGate(self.root).acquire()
        controller.kill()
        controller.communicate(timeout=5)
        deadline = time.monotonic() + 5
        while True:
            try:
                OperationGate(self.root).acquire().close()
                break
            except ActivityBusy:
                if time.monotonic() >= deadline:
                    self.fail('nav2_orphan_retirement_deadline')
                time.sleep(.01)
        for pid, original in identities.items():
            self.assertFalse(self.same_live_process(pid, original), f'nav2_workload_still_live:{pid}')
        # The guard releases its FD only after workloads retire, just before exit.
        deadline = time.monotonic() + 1
        while self.same_live_process(value['pid'], guard) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.same_live_process(value['pid'], guard), 'nav2_guard_exit_deadline')

    @classmethod
    def same_live_process(cls, pid, original):
        try:
            state, started = cls.process_identity(pid)
            return started == original[1] and state not in ('Z', 'X')
        except (FileNotFoundError, ProcessLookupError):
            return False

    @staticmethod
    def process_identity(pid):
        fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[0], fields[19]

    def test_independent_reload_subprocess_retains_lease_after_intermediate_exit(self):
        lease = self.acquire()
        operation = OperationGate(self.root).acquire()
        self.leases.append(operation)
        relay = self.spawn("relay", env={**os.environ, LEASE_ENV: str(lease.fileno()),
                           OPERATION_ENV: str(operation.fileno())}, pass_fds=(lease.fileno(), operation.fileno()))
        output, errors = relay.communicate(timeout=5)
        self.assertEqual(0, relay.returncode, errors)
        self.orphans.append(int(output))
        self.assertEqual(2, len(self.wait_file('sleeper.json')['leaseFds']))
        lease.close()
        operation.close()
        with self.assertRaises(ActivityBusy):
            self.acquire()
        with self.assertRaises(ActivityBusy):
            OperationGate(self.root, shared=True).acquire()
        (self.root / "release").touch()
        self.wait_available()
        OperationGate(self.root).acquire().close()

    def test_supervisor_start_failure_and_unused_owner_release_the_lease(self):
        for mode in ("unused", "spawn-failure", "thread-failure", "normal"):
            with self.subTest(mode=mode):
                operation = OperationGate(self.root).acquire()
                self.leases.append(operation)
                owner = ProcessSupervisor([sys.executable, "-u", str(ROOT / "tools/tests/fixtures/slam_process_child.py"),
                                           "normal"], cwd=self.root,
                                          environment={"PYTHONPATH": str(ROOT / "robot-slam")},
                                          stderr_path=self.root / (mode + ".log"), activity_lease=self.acquire(),
                                          operation_lease=operation)
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
                        with self.assertRaises(ActivityBusy):
                            OperationGate(self.root, shared=True).acquire()
                finally:
                    state = owner.stop()
                self.assertTrue(state["cleanupComplete"])
                self.assertFalse(state["processAlive"])
                self.assertFalse(state["monitorAlive"])
                self.wait_available()
                OperationGate(self.root).acquire().close()

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
        with self.assertRaises(ValueError):
            ProcessSupervisor([sys.executable, '-c', 'pass'], cwd=self.root,
                              environment={OPERATION_ENV: str(lease.fileno())}, stderr_path=self.root / 'op-reserved.log')
        with patch.dict(os.environ, {LEASE_ENV: str(lease.fileno()), OPERATION_ENV: str(lease.fileno())}), \
                self.assertRaises(ValueError):
            inherited_activity_fds()

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
