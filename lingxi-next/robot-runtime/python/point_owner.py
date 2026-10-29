"""Local ownership of one sensor child and one inherited-socket API, never motion."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import selectors
import signal
import socket
import struct
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.process_supervisor import ProcessLimits, ProcessSupervisor


@dataclass(frozen=True)
class OwnerLimits:
    point_connect_seconds: float = 30.
    api_start_seconds: float = 15.
    api_stop_seconds: float = 5.
    signal_seconds: float = 1.
    tick_seconds: float = .05

    def __post_init__(self):
        if (any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                for value in vars(self).values())
                or self.tick_seconds >= min(self.point_connect_seconds, self.api_start_seconds, self.api_stop_seconds)):
            raise ValueError("finite_positive_owner_deadlines_required")


def group_alive(pid):
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                fields = (Path(entry.path) / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == pid and fields[0] not in ("Z", "X"):
                    return True
            except (FileNotFoundError, ProcessLookupError):
                pass
    return False


class PointRuntimeOwner:
    """Commands are trusted local composition, not client-provided configuration."""

    def __init__(self, native_command, api_command, *, native_environment, api_environment,
                 output, limits=None, process_limits=None):
        self.limits = OwnerLimits() if limits is None else limits
        if (sys.platform != "linux" or os.geteuid() == 0 or not isinstance(self.limits, OwnerLimits)
                or any(type(command) not in (list, tuple) or not 1 <= len(command) <= 60
                       or any(type(item) is not str or not item or "\x00" in item for item in command)
                       or not Path(command[0]).is_absolute() or not os.access(command[0], os.X_OK)
                       for command in (native_command, api_command))
                or any(type(env) is not dict or any(
                    type(key) is not str or not key or "=" in key or "\x00" in key
                    or type(value) is not str or "\x00" in value for key, value in env.items())
                    for env in (native_environment, api_environment))
                or set(api_environment) & {
                    "LINGXI_SLAM_POINT_FD", "LINGXI_SLAM_SESSION", "LINGXI_SLAM_INPUT_MODE",
                    "LINGXI_POINT_PARENT_STDIN", "LD_PRELOAD", "NODE_OPTIONS"}
                or set(native_environment) & {"LINGXI_SLAM_SESSION", "LD_PRELOAD"}):
            raise ValueError("ordinary_linux_owner_and_explicit_local_commands_required")
        self.output = Path(output)
        if (not self.output.is_absolute() or self.output.exists() or self.output.is_symlink()
                or self.output.parent.resolve(strict=True) != self.output.parent
                or len(os.fsencode(self.output / "points.sock")) >= 100):
            raise ValueError("new_direct_private_owner_directory_required")
        self.output.mkdir(mode=0o700)
        self.endpoint = self.output / "points.sock"
        self.native = ProcessSupervisor(
            [*native_command, "--point-socket", str(self.endpoint)], cwd=self.output,
            environment=native_environment, stderr_path=self.output / "private-native.log",
            limits=process_limits or ProcessLimits(startup_seconds=30., shutdown_seconds=6.))
        self.api_command, self.api_environment = tuple(api_command), api_environment.copy()
        self.api = self.listener = self.connection = None
        self.api_closed = None
        self.started = self.api_forced = False
        self.stdout_buffer = bytearray()

    def _available(self, stop):
        if stop.is_set():
            raise InterruptedError("point_runtime_stop_requested")
        if self.native.snapshot()["state"] == "failed":
            raise RuntimeError("sensor_process_failed")
        if self.api is not None and self.api.poll() is not None:
            raise RuntimeError("point_api_exited")

    def _connect(self, stop):
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.endpoint))
        os.chmod(self.endpoint, 0o600)
        self.listener.listen(1)
        self.listener.settimeout(self.limits.tick_seconds)
        self.native.start()
        deadline = time.monotonic() + self.limits.point_connect_seconds
        while time.monotonic() < deadline:
            self._available(stop)
            try:
                self.connection, _ = self.listener.accept()
                break
            except socket.timeout:
                continue
        if self.connection is None:
            raise RuntimeError("sensor_point_connection_deadline")
        pid, uid, _ = struct.unpack("3i", self.connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        if pid != self.native.process.pid or uid != os.geteuid():
            raise RuntimeError("owned_sensor_point_peer_required")
        self.listener.close()
        self.listener = None
        self.endpoint.unlink()

    def _start_api(self):
        fd = self.connection.fileno()
        with (self.output / "private-api.log").open("xb") as log:
            os.chmod(log.name, 0o600)
            self.api = subprocess.Popen(
                self.api_command, cwd=self.output, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=log, close_fds=True, pass_fds=(fd,), start_new_session=True,
                env={**self.api_environment, "LINGXI_SLAM_POINT_FD": str(fd),
                     "LINGXI_SLAM_SESSION": self.native.session, "LINGXI_SLAM_INPUT_MODE": "live",
                     "LINGXI_POINT_PARENT_STDIN": "1"})
        self.connection.close()
        self.connection = None
        os.set_blocking(self.api.stdout.fileno(), False)

    def _read_api(self):
        try:
            data = os.read(self.api.stdout.fileno(), 4097)
        except BlockingIOError:
            return None
        if not data:
            raise RuntimeError("point_api_stdout_closed")
        self.stdout_buffer.extend(data)
        if len(self.stdout_buffer) > 4096:
            raise ValueError("bounded_point_api_status_required")
        if b"\n" not in self.stdout_buffer:
            return None
        line, _, remaining = self.stdout_buffer.partition(b"\n")
        self.stdout_buffer.clear()
        if remaining:
            raise ValueError("one_point_api_listening_status_required")
        status = json.loads(line)
        if (type(status) is not dict or set(status) != {"service", "event", "port"}
                or status["service"] != "lingxi-next" or status["event"] != "listening"
                or type(status["port"]) is not int or not 1 <= status["port"] <= 65535):
            raise ValueError("exact_point_api_listening_status_required")
        return status

    def _ready(self, stop):
        deadline = time.monotonic() + self.limits.api_start_seconds
        with selectors.DefaultSelector() as selector:
            selector.register(self.api.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                self._available(stop)
                if selector.select(self.limits.tick_seconds):
                    status = self._read_api()
                    if status is not None:
                        return status
        raise RuntimeError("point_api_startup_deadline")

    def _signal_api(self, signum):
        try:
            os.killpg(self.api.pid, signum)
        except ProcessLookupError:
            pass

    def _close_api(self):
        if self.api is None:
            return None
        if self.api.stdin is not None and not self.api.stdin.closed:
            try:
                self.api.stdin.close()  # EOF also closes the API after abrupt parent death.
            except OSError:
                self.api_forced = True
        if self.api.poll() is None:
            try:
                self.api.wait(timeout=self.limits.api_stop_seconds)
            except subprocess.TimeoutExpired:
                self.api_forced = True
                self._signal_api(signal.SIGTERM)
                try:
                    self.api.wait(timeout=self.limits.signal_seconds)
                except subprocess.TimeoutExpired:
                    self._signal_api(signal.SIGKILL)
                    self.api.wait(timeout=self.limits.signal_seconds)
        if group_alive(self.api.pid):
            self.api_forced = True
            self._signal_api(signal.SIGKILL)
        deadline = time.monotonic() + self.limits.signal_seconds
        while group_alive(self.api.pid) and time.monotonic() < deadline:
            time.sleep(self.limits.tick_seconds)
        self.api.stdout.close()
        return {"exitCode": self.api.poll(), "forcedTermination": self.api_forced,
                "processAlive": self.api.poll() is None, "groupAlive": group_alive(self.api.pid)}

    def run(self, *, stop=None, seconds=240., on_listening=None):
        if (self.started or type(seconds) not in (int, float) or not math.isfinite(seconds)
                or not 0 < seconds <= 240 or stop is not None and not isinstance(stop, threading.Event)
                or on_listening is not None and not callable(on_listening)):
            raise ValueError("single_use_bounded_point_runtime_required")
        self.started = True
        stop = threading.Event() if stop is None else stop
        report = {"schemaVersion": 1, "result": "failed", "failure": None,
                  "mappingReady": False, "localizationReady": False, "navigationReady": False}
        try:
            self._available(stop)
            self._connect(stop)
            self._start_api()
            status = self._ready(stop)
            if on_listening is not None:
                on_listening(status)
            deadline = time.monotonic() + seconds
            with selectors.DefaultSelector() as selector:
                selector.register(self.api.stdout, selectors.EVENT_READ)
                while not stop.wait(self.limits.tick_seconds):
                    self._available(stop)
                    if selector.select(0):
                        raise RuntimeError("unexpected_point_api_status")
                    if time.monotonic() >= deadline:
                        break
            report["result"] = "stopped"
        except InterruptedError:
            report["result"] = "stopped"
        except Exception as error:
            report["failure"] = type(error).__name__
        finally:
            try:
                report["sensorProcess"] = self.native.stop()
            except Exception:
                report["sensorProcess"] = self.native.snapshot()
                report["failure"] = "sensor_cleanup_failed"
            try:
                self.api_closed = self._close_api()
                report["apiProcess"] = self.api_closed
            except Exception:
                report["apiProcess"] = None
                report["failure"] = "api_cleanup_failed"
            for resource in (self.connection, self.listener):
                try:
                    if resource is not None:
                        resource.close()
                except Exception:
                    report["failure"] = "point_socket_cleanup_failed"
            try:
                if self.endpoint.exists():
                    self.endpoint.unlink()
            except Exception:
                report["failure"] = "point_socket_cleanup_failed"
        sensor, api = report["sensorProcess"], report["apiProcess"]
        if (report["failure"] or sensor["state"] != "stopped" or sensor["forcedTermination"]
                or not sensor["cleanupComplete"]
                or api is not None and (api["exitCode"] != 0 or api["forcedTermination"]
                                       or api["processAlive"] or api["groupAlive"])):
            report["result"] = "failed"
        descriptor = os.open(self.output / "owner-report.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        return report
