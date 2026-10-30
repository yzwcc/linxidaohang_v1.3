"""One offline parent owns the recorded native worker and paired HTTP API."""
from dataclasses import asdict
import math
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "robot-slam"))
from lingxi_slam.process_supervisor import ProcessLimits, ProcessSupervisor
from localization_control import direct, strict_json
from mapping_service import private_json
from point_owner import group_alive


class ManagedLocalizationOwner:
    """Single-use ordinary-user Linux composition; commands never come from HTTP."""

    def __init__(self, native_command, api_command, *, native_environment, api_environment,
                 output, saved_maps, process_limits=None, api_seconds=5.):
        if (sys.platform != "linux" or os.geteuid() == 0
                or type(api_seconds) not in (int, float) or not math.isfinite(api_seconds)
                or not .1 <= api_seconds <= 30.
                or not callable(native_command)
                or type(api_command) not in (list, tuple) or not 1 <= len(api_command) <= 16
                or any(type(item) is not str or not item or "\x00" in item for item in api_command)
                or not Path(api_command[0]).is_absolute() or not os.access(api_command[0], os.X_OK)
                or type(api_environment) is not dict or any(
                    type(key) is not str or not key or "=" in key or "\x00" in key
                    or type(value) is not str or "\x00" in value
                    for key, value in api_environment.items())
                or set(api_environment) & {
                    "LINGXI_LOCALIZATION_PARENT_STDIN", "LINGXI_LOCALIZATION_SOCKET",
                    "LINGXI_MAPPING_PARENT_STDIN", "LINGXI_MAPPING_SOCKET", "LINGXI_SAVED_SLAM_DIR",
                    "LINGXI_DATA_DIR", "LINGXI_SLAM_POINT_FD", "NODE_OPTIONS", "LD_PRELOAD"}):
            raise ValueError("explicit_ordinary_linux_localization_owner_required")
        self.output, self.saved_maps = Path(output), Path(saved_maps)
        direct(self.output.parent, directory=True)
        direct(self.saved_maps, directory=True)
        if (not self.output.is_absolute() or self.output.exists() or self.output.is_symlink()
                or self.output.resolve() != self.output
                or self.output == self.saved_maps or self.output.is_relative_to(self.saved_maps)
                or self.saved_maps.is_relative_to(self.output)
                or len(os.fsencode(self.output / "native/localization.sock")) >= 100):
            raise ValueError("new_separate_private_localization_owner_output_required")
        self.output.mkdir(mode=0o700)
        self.native_output = self.output / "native"
        self.native_output.mkdir(mode=0o700)
        self.endpoint = self.native_output / "localization.sock"
        self.native = ProcessSupervisor(
            native_command(self.native_output), cwd=self.native_output,
            environment=native_environment, stderr_path=self.native_output / "private-child.log",
            limits=process_limits or ProcessLimits(
                startup_seconds=30., progress_seconds=2., shutdown_seconds=5.))
        self.api_command, self.api_environment = tuple(api_command), api_environment.copy()
        self.api_seconds, self.api, self.started = api_seconds, None, False

    def _available(self, stop):
        if stop.is_set():
            raise InterruptedError("localization_owner_stop_requested")
        if self.native.snapshot()["state"] not in ("starting", "running"):
            raise RuntimeError("localization_native_unavailable")
        if self.api is not None and self.api.poll() is not None:
            raise RuntimeError("localization_api_exited")

    def _native_ready(self, binding, stop):
        deadline = time.monotonic() + self.native.limits.startup_seconds
        while time.monotonic() < deadline:
            self._available(stop)
            try:
                info = self.endpoint.lstat()
                if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077):
                    raise ValueError("private_owned_localization_socket_required")
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(.25)
                    client.connect(str(self.endpoint))
                    client.sendall(b'{"action":"state"}\n')
                    with client.makefile("rb") as stream:
                        line = stream.readline(8193)
                    if len(line) > 8192 or not line.endswith(b"\n"):
                        raise ValueError("bounded_localization_startup_response_required")
                response = strict_json(line)
                state = response["value"]
                if (response.get("ok") is not True or state["binding"] != asdict(binding)
                        or state["localizationReady"] is not False or state["navigationReady"] is not False
                        or state["workerClosed"] is not False or state["cleanupComplete"] is not False):
                    raise ValueError("same_session_localization_startup_required")
                if state["workerReady"] is True and self.native.snapshot()["state"] == "running":
                    return
            except (FileNotFoundError, ConnectionRefusedError, TimeoutError, BlockingIOError):
                pass
            stop.wait(.025)
        raise TimeoutError("localization_socket_startup_deadline")

    def _api_start(self, stop):
        descriptor = os.open(self.output / "private-api.log",
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as log:
            self.api = subprocess.Popen(
                self.api_command, cwd=self.output,
                env={**self.api_environment, "LINGXI_LOCALIZATION_PARENT_STDIN": "1",
                     "LINGXI_LOCALIZATION_SOCKET": str(self.endpoint),
                     "LINGXI_SAVED_SLAM_DIR": str(self.saved_maps),
                     "LINGXI_DATA_DIR": str(self.output / "data")},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                start_new_session=True, close_fds=True)
        data = bytearray()
        os.set_blocking(self.api.stdout.fileno(), False)
        deadline = time.monotonic() + self.api_seconds
        with selectors.DefaultSelector() as selector:
            selector.register(self.api.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                self._available(stop)
                if not selector.select(.025):
                    continue
                block = os.read(self.api.stdout.fileno(), 4097)
                if not block:
                    raise RuntimeError("localization_api_status_closed")
                data.extend(block)
                if len(data) > 4096:
                    raise ValueError("bounded_localization_api_status_required")
                if b"\n" not in data:
                    continue
                line, _, remaining = data.partition(b"\n")
                value = strict_json(line)
                if (remaining or type(value) is not dict or type(value.get("port")) is not int
                        or not 1 <= value["port"] <= 65535 or value != {
                            "service": "lingxi-next", "event": "listening", "port": value["port"]}):
                    raise ValueError("exact_localization_api_listening_status_required")
                return value
        raise TimeoutError("localization_api_startup_deadline")

    def _api_stop(self):
        child = self.api
        if child is None:
            return None
        forced = False
        try:
            child.stdin.close()
        except OSError:
            forced = True
        try:
            child.wait(timeout=self.api_seconds)
        except subprocess.TimeoutExpired:
            forced = True
            for value in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, value)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=1.)
                    break
                except subprocess.TimeoutExpired:
                    continue
        if group_alive(child.pid):
            forced = True
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 1.
        while group_alive(child.pid) and time.monotonic() < deadline:
            time.sleep(.025)
        child.stdout.close()
        return {"exitCode": child.poll(), "forcedTermination": forced,
                "processAlive": child.poll() is None, "groupAlive": group_alive(child.pid)}

    def run(self, binding, expected_messages, *, seconds, stop=None,
            on_listening=None, verify_complete=None, verify_inputs=None):
        if (self.started or binding.session != self.native.session or binding.input_mode != "recorded"
                or type(expected_messages) is not int or expected_messages < 1
                or type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 120
                or stop is not None and not isinstance(stop, threading.Event)
                or on_listening is not None and not callable(on_listening)
                or not callable(verify_complete) or not callable(verify_inputs)):
            raise ValueError("single_use_bounded_recorded_localization_owner_required")
        self.started = True
        stop = stop or threading.Event()
        report = {"schemaVersion": 1, "result": "failed", "reason": None, "failure": None,
                  "failurePhase": None,
                  "inputComplete": False, "inputsRechecked": False,
                  "robotContacted": False, "motionCommandsSent": False,
                  "localizationReady": False, "navigationReady": False,
                  "mapProfileAssociationVerified": False}
        def fail(kind, phase):
            if report["failure"] is None:
                report.update(failure=kind, failurePhase=phase)

        phase = "native_start"
        try:
            if stop.is_set():
                raise InterruptedError("localization_owner_stop_requested")
            self.native.start()
            phase = "native_ready"
            self._native_ready(binding, stop)
            phase = "api_start"
            status = self._api_start(stop)
            if on_listening is not None:
                on_listening(status)
            deadline = time.monotonic() + seconds
            phase = "recorded_input"
            with selectors.DefaultSelector() as selector:
                selector.register(self.api.stdout, selectors.EVENT_READ)
                while True:
                    self._available(stop)
                    if selector.select(0):
                        raise RuntimeError("unexpected_localization_api_status")
                    completed = self.native.snapshot()["completedMessages"]
                    if completed > expected_messages:
                        raise ValueError("localization_input_counter_exceeded")
                    if completed == expected_messages:
                        report["reason"] = "recorded_input_complete"
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("localization_recorded_input_deadline")
                    stop.wait(.025)
        except InterruptedError:
            report.update(result="interrupted", reason="operator_stop")
        except Exception as error:
            fail(type(error).__name__, phase)
        finally:
            # Revoke HTTP admission before joining native shutdown.
            try:
                report["apiProcess"] = self._api_stop()
            except Exception:
                report["apiProcess"] = None
                fail("api_cleanup_failed", "shutdown")
            try:
                report["nativeProcess"] = self.native.stop()
            except Exception:
                report["nativeProcess"] = None
                fail("native_cleanup_failed", "shutdown")
        native, api = report["nativeProcess"], report["apiProcess"]
        clean = (native is not None and native["cleanupComplete"]
                 and native["state"] == "stopped" and native["exitCode"] in (None, 0)
                 and not any(native[key] for key in ("forcedTermination", "processAlive", "monitorAlive"))
                 and (api is None or api["exitCode"] == 0
                      and not any(api[key] for key in ("forcedTermination", "processAlive", "groupAlive"))))
        if not clean:
            fail("localization_unclean_shutdown", "shutdown")
        try:
            verify_inputs()
            report["inputsRechecked"] = True
        except Exception as error:
            fail(type(error).__name__, "input_verification")
        if report["reason"] == "recorded_input_complete" and not report["failure"]:
            try:
                if native["exitCode"] != 0 or native["completedMessages"] != expected_messages:
                    raise RuntimeError("localization_native_completion_unconfirmed")
                verify_complete()
                report.update(result="completed", inputComplete=True)
            except Exception as error:
                fail(type(error).__name__, "completion_verification")
        if report["failure"]:
            report["result"] = "failed"
        private_json(self.output / "owner-report.json", report)
        return report
