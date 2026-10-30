"""Bounded local owner of the mapping service, API and adopted native children."""
import ctypes
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time

from point_owner import group_alive


@dataclass(frozen=True)
class MappingOwnerLimits:
    startup_seconds: float = 20.
    service_stop_seconds: float = 110.
    api_stop_seconds: float = 5.
    signal_seconds: float = 2.
    tick_seconds: float = .05

    def __post_init__(self):
        if (any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                for value in vars(self).values())
                or self.tick_seconds >= min(self.startup_seconds, self.api_stop_seconds,
                                            self.service_stop_seconds, self.signal_seconds)):
            raise ValueError("finite_positive_mapping_owner_deadlines_required")


def direct_children():
    children = {}
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                fields = (Path(entry.path) / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) == os.getpid():
                    children[int(entry.name)] = fields[0]
            except (FileNotFoundError, ProcessLookupError):
                pass
    return children


class ManagedMappingOwner:
    """Run in a dedicated ordinary-user process, with no pre-existing children."""

    service_name = "lingxi-mapping"
    continuous = False

    def __init__(self, service_command, api_command, *, service_environment, api_environment,
                 output, limits=None, maps=None):
        self.limits = limits or MappingOwnerLimits()
        if (sys.platform != "linux" or os.geteuid() == 0 or not isinstance(self.limits, MappingOwnerLimits)
                or any(type(command) not in (list, tuple) or not 1 <= len(command) <= 60
                       or any(type(item) is not str or not item or "\x00" in item for item in command)
                       or not Path(command[0]).is_absolute() or not os.access(command[0], os.X_OK)
                       for command in (service_command, api_command))
                or any(type(env) is not dict or any(
                    type(key) is not str or not key or "=" in key or "\x00" in key
                    or type(value) is not str or "\x00" in value for key, value in env.items())
                    for env in (service_environment, api_environment))
                or set(api_environment) & {
                    "LINGXI_MAPPING_PARENT_STDIN", "LINGXI_MAPPING_SOCKET", "LINGXI_SAVED_SLAM_DIR",
                    "LINGXI_DATA_DIR", "LINGXI_SLAM_POINT_FD", "LD_PRELOAD", "NODE_OPTIONS"}
                or set(service_environment) & {"LD_PRELOAD", "PYTHONSTARTUP", "PYTHONINSPECT"}):
            raise ValueError("explicit_ordinary_linux_mapping_owner_required")
        self.output = Path(output)
        self.maps = Path(maps) if maps is not None else self.output / "maps"
        if (not self.output.is_absolute() or self.output.exists() or self.output.is_symlink()
                or self.output.parent.resolve(strict=True) != self.output.parent
                or len(os.fsencode(self.maps / ("a" * 32 + ".points.sock"))) >= 100):
            raise ValueError("new_direct_private_mapping_owner_directory_required")
        if maps is not None:
            info = self.maps.lstat()
            if (not self.maps.is_absolute() or self.maps.resolve(strict=True) != self.maps
                    or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                    or self.maps.is_relative_to(self.output) or self.output.is_relative_to(self.maps)):
                raise ValueError("separate_existing_private_mapping_catalog_required")
        self.output.mkdir(mode=0o700)
        if maps is None:
            self.maps.mkdir(mode=0o700)
        self.endpoint = self.output / "control.sock"
        self.service_command, self.api_command = tuple(service_command), tuple(api_command)
        self.service_environment, self.api_environment = service_environment.copy(), api_environment.copy()
        self.service = self.api = None
        self.started = False

    def _spawn(self, command, environment, label):
        with (self.output / ("private-" + label + ".log")).open("xb") as log:
            os.chmod(log.name, 0o600)
            process = subprocess.Popen(
                command, cwd=self.output, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=log, close_fds=True, start_new_session=True)
        os.set_blocking(process.stdout.fileno(), False)
        return process

    def _available(self, stop):
        if stop.is_set():
            raise InterruptedError("mapping_runtime_stop_requested")
        for child in (self.service, self.api):
            if child is not None and child.poll() is not None:
                raise RuntimeError("mapping_owned_process_exited")

    def _service_arguments(self):
        return ["--maps", str(self.maps), "--socket", str(self.endpoint), "--parent-stdin"]

    def _api_configuration(self):
        return {**self.api_environment, "LINGXI_MAPPING_PARENT_STDIN": "1",
                "LINGXI_MAPPING_SOCKET": str(self.endpoint), "LINGXI_SAVED_SLAM_DIR": str(self.maps),
                "LINGXI_DATA_DIR": str(self.output / "data")}

    def _start(self, stop):
        self.service = self._spawn([*self.service_command, *self._service_arguments()],
                                   self.service_environment, "service")
        self._ready(self.service, {"service": self.service_name, "event": "listening"}, stop)
        self.api = self._spawn(self.api_command, self._api_configuration(), "api")
        return self._ready(self.api, {"service": "lingxi-next", "event": "listening"}, stop)

    def _ready(self, child, expected, stop):
        data = bytearray()
        deadline = time.monotonic() + self.limits.startup_seconds
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                self._available(stop)
                if not selector.select(self.limits.tick_seconds):
                    continue
                block = os.read(child.stdout.fileno(), 4097)
                if not block:
                    raise RuntimeError("mapping_status_closed")
                data.extend(block)
                if len(data) > 4096:
                    raise ValueError("bounded_mapping_status_required")
                if b"\n" not in data:
                    continue
                line, _, remaining = data.partition(b"\n")
                value = json.loads(line)
                if remaining or type(value) is not dict:
                    raise ValueError("one_mapping_listening_status_required")
                if child is self.api:
                    if type(value.get("port")) is not int or not 1 <= value["port"] <= 65535:
                        raise ValueError("mapping_api_network_port_required")
                    expected = {**expected, "port": value["port"]}
                if value != expected:
                    raise ValueError("exact_mapping_listening_status_required")
                return value
        raise RuntimeError("mapping_process_startup_deadline")

    @staticmethod
    def _signal(child, value):
        try:
            os.killpg(child.pid, value)
        except ProcessLookupError:
            pass

    def _finish(self, child, seconds):
        if child is None:
            return None
        forced = False
        try:
            child.stdin.close()  # EOF independently handles abrupt owner death.
        except OSError:
            forced = True
        try:
            child.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            forced = True
            self._signal(child, signal.SIGTERM)
            try:
                child.wait(timeout=self.limits.signal_seconds)
            except subprocess.TimeoutExpired:
                self._signal(child, signal.SIGKILL)
                child.wait(timeout=self.limits.signal_seconds)
        if group_alive(child.pid):
            forced = True
            self._signal(child, signal.SIGKILL)
        deadline = time.monotonic() + self.limits.signal_seconds
        while group_alive(child.pid) and time.monotonic() < deadline:
            time.sleep(self.limits.tick_seconds)
        child.stdout.close()
        return {"exitCode": child.poll(), "forcedTermination": forced,
                "processAlive": child.poll() is None, "groupAlive": group_alive(child.pid)}

    def _adopted(self):
        # A native worker owns a separate process group. Subreaping also covers
        # service failure before it could report that worker's PID.
        excluded = {child.pid for child in (self.api, self.service) if child is not None}
        deadline = time.monotonic() + self.limits.service_stop_seconds
        killed, seen = False, set()
        while True:
            children = {pid: state for pid, state in direct_children().items() if pid not in excluded}
            if not children:
                return {"count": len(seen), "forcedTermination": killed, "cleanupComplete": True}
            seen.update(children)
            for pid, state in children.items():
                if state in ("Z", "X"):
                    try:
                        os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        pass
                elif time.monotonic() >= deadline:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if time.monotonic() >= deadline:
                if killed:
                    return {"count": len(seen), "forcedTermination": True, "cleanupComplete": False}
                killed = True
                deadline = time.monotonic() + self.limits.signal_seconds
            time.sleep(self.limits.tick_seconds)

    def run(self, *, stop=None, seconds=300., on_listening=None):
        if (self.started or not (seconds is None and self.continuous or type(seconds) in (int, float)
                and math.isfinite(seconds) and 0 < seconds <= 3600)
                or stop is not None and not isinstance(stop, threading.Event)
                or on_listening is not None and not callable(on_listening) or direct_children()):
            raise ValueError("dedicated_single_use_bounded_mapping_owner_required")
        self.started = True
        stop = stop or threading.Event()
        library = ctypes.CDLL(None, use_errno=True)
        prior = ctypes.c_int()
        if library.prctl(37, ctypes.byref(prior), 0, 0, 0) or library.prctl(36, 1, 0, 0, 0):
            raise OSError(ctypes.get_errno(), "mapping_child_subreaper_required")
        report = {"schemaVersion": 1, "result": "failed", "failure": None,
                  "mappingReady": False, "localizationReady": False, "navigationReady": False}
        try:
            self._available(stop)
            status = self._start(stop)
            if on_listening is not None:
                on_listening(status)
            deadline = None if seconds is None else time.monotonic() + seconds
            with selectors.DefaultSelector() as selector:
                for child in (self.api, self.service):
                    selector.register(child.stdout, selectors.EVENT_READ)
                while not stop.wait(self.limits.tick_seconds):
                    self._available(stop)
                    if selector.select(0):
                        raise RuntimeError("unexpected_mapping_process_status")
                    if deadline is not None and time.monotonic() >= deadline:
                        break
            report["result"] = "stopped"
        except InterruptedError:
            report["result"] = "stopped"
        except Exception as error:
            report["failure"] = type(error).__name__
        finally:
            # Preserve point draining while the service revokes saving and joins
            # native finalization, including its independent reload subprocess.
            for label, child, budget in (
                ("serviceProcess", self.service, self.limits.service_stop_seconds),
                ("apiProcess", self.api, self.limits.api_stop_seconds),
            ):
                try:
                    if label == "apiProcess":
                        report["adoptedChildren"] = self._adopted()
                    report[label] = self._finish(child, budget)
                except Exception:
                    report[label] = None
                    report["failure"] = "mapping_process_cleanup_failed"
            report["remainingChildren"] = len(direct_children())
            if not report["remainingChildren"]:
                library.prctl(36, prior.value, 0, 0, 0)
        if (report["failure"] or report["remainingChildren"]
                or not report.get("adoptedChildren", {}).get("cleanupComplete")
                or report.get("adoptedChildren", {}).get("forcedTermination")
                or any(value is not None and (value["exitCode"] != 0 or value["forcedTermination"]
                       or value["processAlive"] or value["groupAlive"])
                       for value in (report["serviceProcess"], report["apiProcess"]))):
            report["result"] = "failed"
        descriptor = os.open(self.output / "owner-report.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        return report
