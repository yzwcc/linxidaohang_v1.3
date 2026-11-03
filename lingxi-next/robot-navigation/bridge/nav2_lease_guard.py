"""Retain a Nav2 lease and retire its process group when the bridge pipe closes."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-slam'))
from lingxi_slam.activity_lease import OPERATION_ENV, inherited_lease_fd


def members():
    result = []
    with os.scandir('/proc') as entries:
        for entry in entries:
            if not entry.name.isdecimal() or int(entry.name) == os.getpid():
                continue
            try:
                fields = (Path(entry.path) / 'stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == os.getpgrp() and fields[0] not in ('Z', 'X'):
                    result.append(int(entry.name))
            except (FileNotFoundError, ProcessLookupError):
                continue
    return result


def signal_members(value):
    for pid in members():
        try:
            if os.getpgid(pid) == os.getpgrp():
                os.kill(pid, value)
        except ProcessLookupError:
            pass


def run(command):
    descriptor = inherited_lease_fd(OPERATION_ENV)
    if (sys.platform != 'linux' or os.geteuid() == 0 or os.getpgrp() != os.getpid()
            or descriptor is None or not command or not Path(command[0]).is_absolute()):
        raise ValueError('owned_linux_nav2_group_and_inherited_lease_required')
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    for value in (signal.SIGTERM, signal.SIGINT):
        signal.signal(value, stop)
    child = None
    try:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, pass_fds=(descriptor,))
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin.buffer, selectors.EVENT_READ)
            while not stopping and child.poll() is None:
                if selector.select(.02):
                    # This pipe carries lifetime only, never commands or robot data.
                    stopping = True
    finally:
        signal_members(signal.SIGTERM)
        deadline = time.monotonic() + .6
        while members():
            if time.monotonic() >= deadline:
                signal_members(signal.SIGKILL)
            if child is not None:
                child.poll()
            time.sleep(.01)
        if child is not None:
            child.wait()
        os.close(descriptor)


if __name__ == '__main__':
    run(sys.argv[1:])
