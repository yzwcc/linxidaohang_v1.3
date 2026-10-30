"""Shared admission for native mapping/localization, retained by the actual child."""
import os
from pathlib import Path
import stat
import sys
import threading


LEASE_ENV = "LINGXI_SLAM_LEASE_FD"


class ActivityBusy(Exception):
    pass


def _private_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ValueError("owned_private_single_link_activity_lock_required")


class ActivityLease:
    def __init__(self, descriptor):
        self._descriptor = descriptor
        self._lock = threading.Lock()

    def fileno(self):
        with self._lock:
            if self._descriptor is None:
                raise ValueError("active_slam_activity_lease_required")
            return self._descriptor

    def close(self):
        with self._lock:
            if self._descriptor is not None:
                # Never LOCK_UN: inherited descriptors must keep the lease after owner death.
                os.close(self._descriptor)
                self._descriptor = None


class ActivityGate:
    """Stable lock inode in a private catalog. Never delete or replace the lock file."""

    def __init__(self, path):
        self.path = Path(path)
        if (sys.platform != "linux" or os.geteuid() == 0 or not self.path.is_absolute()
                or self.path.parent.resolve(strict=True) != self.path.parent):
            raise ValueError("direct_ordinary_user_activity_lock_required")
        self.parent = self.path.parent.stat()
        self._parent()

    def _parent(self):
        info = self.path.parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or (info.st_dev, info.st_ino) != (self.parent.st_dev, self.parent.st_ino)):
            raise ValueError("stable_private_activity_lock_directory_required")

    def acquire(self):
        import fcntl
        self._parent()
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            _private_file(info)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ActivityBusy("slam_activity_busy") from None
            self._parent()
            current = self.path.lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("stable_activity_lock_inode_required")
            lease = ActivityLease(descriptor)
            descriptor = None
            return lease
        finally:
            if descriptor is not None:
                os.close(descriptor)


def inherited_activity_fds():
    """Preserve the supervisor's lease across the independent map-reload subprocess."""
    value = os.environ.get(LEASE_ENV)
    if value is None:
        return ()
    if not value.isascii() or not value.isdecimal() or value != str(int(value)) or not 3 <= int(value) <= 2**31 - 1:
        raise ValueError("valid_inherited_activity_descriptor_required")
    descriptor = int(value)
    _private_file(os.fstat(descriptor))
    return (descriptor,)
