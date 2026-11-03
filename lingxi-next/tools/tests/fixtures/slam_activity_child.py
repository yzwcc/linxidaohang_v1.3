"""Synthetic lease lifetime processes. Never imports ROS or a native SLAM engine."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.activity_lease import ActivityBusy, ActivityGate, OperationGate, inherited_activity_fds
from lingxi_slam.process_protocol import HealthEmitter
from lingxi_slam.process_supervisor import ProcessLimits, ProcessSupervisor


def main():
    mode, directory = sys.argv[1], Path(sys.argv[2])
    if mode == 'nav-worker':
        child = subprocess.Popen([sys.executable, '-u', __file__, 'nav-descendant', str(directory)],
                                 stdin=subprocess.DEVNULL)
        (directory / 'nav-worker.json').write_text(json.dumps({'pid': os.getpid(), 'descendant': child.pid}))
        while True:
            time.sleep(.02)
    if mode == 'nav-descendant':
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (directory / 'descendant.json').write_text(json.dumps({'pid': os.getpid()}))
        while True:
            time.sleep(.02)
    if mode == 'nav-owner':
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT / 'robot-navigation'))
        from lingxi_navigation.nav2_producer import Nav2Process
        lease = OperationGate(directory, shared=True).acquire()
        actual_popen = subprocess.Popen
        def spawn(command, **kwargs):
            assert command[2].endswith('nav2_lease_guard.py')
            assert command[-3:-1] == [sys.executable, 'launch']
            return actual_popen([*command[:3], sys.executable, '-u', __file__, 'nav-worker', str(directory)], **kwargs)
        with patch.dict(os.environ, {'ROS_DISTRO': 'humble'}), \
                patch('lingxi_navigation.nav2_producer.prepare_producer'), \
                patch('lingxi_navigation.nav2_producer.shutil.which', return_value=sys.executable), \
                patch('lingxi_navigation.nav2_producer.subprocess.Popen', side_effect=spawn):
            owner = Nav2Process(None, None, directory, operation_lease=lease)
        print(json.dumps({'pid': owner.child.pid}), flush=True)
        sys.stdin.readline()
        owner.stop()
        while not owner.quiescent():
            time.sleep(.01)
        lease.close()
        return
    if mode == 'operation-contender':
        sys.stdin.readline()
        try:
            lease = OperationGate(directory, shared=sys.argv[3] == 'shared').acquire()
        except ActivityBusy:
            print('busy', flush=True)
            return
        print('held', flush=True)
        sys.stdin.readline()
        lease.close()
        return
    if mode == "contender":
        sys.stdin.readline()
        try:
            lease = ActivityGate(directory / "activity.lock").acquire()
        except ActivityBusy:
            print("busy", flush=True)
            return
        print("held", flush=True)
        sys.stdin.readline()
        lease.close()
        return
    if mode == "owner":
        lease = ActivityGate(directory / "activity.lock").acquire()
        operation_lease = OperationGate(directory).acquire()
        unrelated = os.open(directory / "unrelated", os.O_RDWR | os.O_CREAT, 0o600)
        os.set_inheritable(unrelated, True)
        info = os.fstat(unrelated)
        owner = ProcessSupervisor(
            [sys.executable, "-u", __file__, "worker", str(directory)], cwd=directory,
            environment={"UNRELATED_FD": str(unrelated), "UNRELATED_INODE": str(info.st_ino)},
            stderr_path=directory / "child.log", activity_lease=lease, operation_lease=operation_lease,
            limits=ProcessLimits(startup_seconds=5., progress_seconds=2.))
        try:
            owner.start()
            deadline = time.monotonic() + 5
            while not (directory / "worker.json").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("synthetic_worker_start_deadline")
                time.sleep(.01)
            print(json.dumps({"pid": owner.process.pid}), flush=True)
            sys.stdin.readline()
        finally:
            owner.stop()
            os.close(unrelated)
        return
    if mode == "relay":
        child = subprocess.Popen([sys.executable, "-u", __file__, "sleeper", str(directory)],
                                 env=os.environ.copy(), pass_fds=inherited_activity_fds(),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        print(child.pid, flush=True)
        return
    descriptors = inherited_activity_fds()
    descriptor = descriptors[0]
    unrelated_closed = True
    if "UNRELATED_FD" in os.environ:
        try:
            unrelated_closed = os.fstat(int(os.environ["UNRELATED_FD"])).st_ino != int(os.environ["UNRELATED_INODE"])
        except OSError:
            pass
    (directory / ("worker.json" if mode == "worker" else "sleeper.json")).write_text(json.dumps({
        "pid": os.getpid(), "leaseFd": descriptor, "leaseFds": descriptors, "unrelatedClosed": unrelated_closed,
    }))
    emitter = HealthEmitter(sys.stdout.buffer, os.environ["LINGXI_SLAM_SESSION"]) if mode == "worker" else None
    count, deadline = 0, time.monotonic() + 20
    while not (directory / "release").exists() and time.monotonic() < deadline:
        count += 1
        if emitter is not None:
            try:
                emitter.emit({"state": "running", "failure": None,
                              "counts": {"completedMessages": count}, "stream": None})
            except BrokenPipeError:
                emitter = None
        time.sleep(.025)


if __name__ == "__main__":
    main()
