"""Synthetic lease lifetime processes. Never imports ROS or a native SLAM engine."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.activity_lease import ActivityBusy, ActivityGate, inherited_activity_fds
from lingxi_slam.process_protocol import HealthEmitter
from lingxi_slam.process_supervisor import ProcessLimits, ProcessSupervisor


def main():
    mode, directory = sys.argv[1], Path(sys.argv[2])
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
        unrelated = os.open(directory / "unrelated", os.O_RDWR | os.O_CREAT, 0o600)
        os.set_inheritable(unrelated, True)
        info = os.fstat(unrelated)
        owner = ProcessSupervisor(
            [sys.executable, "-u", __file__, "worker", str(directory)], cwd=directory,
            environment={"UNRELATED_FD": str(unrelated), "UNRELATED_INODE": str(info.st_ino)},
            stderr_path=directory / "child.log", activity_lease=lease,
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
    descriptor, = inherited_activity_fds()
    unrelated_closed = True
    if "UNRELATED_FD" in os.environ:
        try:
            unrelated_closed = os.fstat(int(os.environ["UNRELATED_FD"])).st_ino != int(os.environ["UNRELATED_INODE"])
        except OSError:
            pass
    (directory / ("worker.json" if mode == "worker" else "sleeper.json")).write_text(json.dumps({
        "pid": os.getpid(), "leaseFd": descriptor, "unrelatedClosed": unrelated_closed,
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
