"""Linux child supervision; health is not map, localization or motion readiness."""
from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid

from .process_protocol import decode
from .activity_lease import ActivityLease, LEASE_ENV


@dataclass(frozen=True)
class ProcessLimits:
    startup_seconds: float = 10.
    progress_seconds: float = 2.
    shutdown_seconds: float = 4.
    terminate_seconds: float = 1.
    kill_seconds: float = 1.
    tick_seconds: float = .05
    maximum_event_bytes: int = 4096
    maximum_events_per_tick: int = 32

    def __post_init__(self):
        for name, value in asdict(self).items():
            valid = (type(value) in (int, float) and math.isfinite(value) and value > 0
                     if name.endswith("_seconds") else type(value) is int and value > 0)
            if not valid:
                raise ValueError("finite_positive_process_limits_required")
        if (self.tick_seconds >= min(self.startup_seconds, self.progress_seconds, self.shutdown_seconds)
                or not 256 <= self.maximum_event_bytes <= 65536
                or not 1 <= self.maximum_events_per_tick <= 128):
            raise ValueError("consistent_bounded_process_limits_required")


class ProcessSupervisor:
    """Single-use local owner. Commands/environment are trusted composition, never HTTP input."""

    def __init__(self, command, *, cwd, environment, stderr_path, limits=None, clock=time.monotonic,
                 activity_lease=None):
        self.limits = ProcessLimits() if limits is None else limits
        if (sys.platform != "linux" or not isinstance(self.limits, ProcessLimits) or not callable(clock)
                or type(command) not in (list, tuple) or not 1 <= len(command) <= 64
                or any(type(value) is not str or not value or "\x00" in value or len(value) > 4096
                       for value in command)
                or type(environment) is not dict
                or any(type(key) is not str or not key or "=" in key or "\x00" in key
                       or type(value) is not str or "\x00" in value for key, value in environment.items())
                or set(environment) & {"LINGXI_SLAM_SESSION", LEASE_ENV, "LD_PRELOAD", "PYTHONINSPECT", "PYTHONSTARTUP"}
                or activity_lease is not None and not isinstance(activity_lease, ActivityLease)):
            raise ValueError("explicit_local_posix_process_configuration_required")
        executable = Path(command[0])
        self.cwd = Path(cwd).resolve(strict=True)
        self.stderr_path = Path(stderr_path)
        if (not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK)
                or not self.cwd.is_dir() or not self.stderr_path.is_absolute()
                or self.stderr_path.exists() or self.stderr_path.is_symlink()):
            raise ValueError("absolute_executable_working_directory_and_new_private_log_required")
        self.command, self.environment, self.clock = tuple(command), environment.copy(), clock
        self.activity_lease = activity_lease
        self.session = uuid.uuid4().hex
        self.condition, self.requested_stop = threading.Condition(), threading.Event()
        self.state, self.failure = "new", None
        self.child_failure = self.exit_code = None
        self.process = self.thread = None
        self.last_clock = self.started_at = self.progress_at = None
        self.sequence, self.counters = -1, (0, 0, 0)
        self.worker_state = None
        self.mapper = None
        self.events = self.retired_events = 0
        self.forced_termination = self.cleanup_complete = False

    def _now(self):
        now = self.clock()
        if (type(now) not in (int, float) or not math.isfinite(now)
                or self.last_clock is not None and now < self.last_clock):
            raise RuntimeError("monotonic_process_clock_required")
        self.last_clock = now
        return now

    def start(self):
        with self.condition:
            if self.state != "new":
                raise RuntimeError("process_owner_cannot_restart")
            descriptor = None
            try:
                self.started_at = self.progress_at = self._now()
                descriptor = os.open(self.stderr_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(descriptor, "wb", buffering=0) as private_log:
                    descriptor = None
                    inherited = () if self.activity_lease is None else (self.activity_lease.fileno(),)
                    self.process = subprocess.Popen(
                        self.command, cwd=self.cwd,
                        env={**self.environment, "LINGXI_SLAM_SESSION": self.session,
                             **({LEASE_ENV: str(inherited[0])} if inherited else {})},
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=private_log,
                        bufsize=0, close_fds=True, pass_fds=inherited, start_new_session=True, shell=False,
                    )
                self.state = "starting"
                self.thread = threading.Thread(target=self._monitor, name="lingxi-slam-process", daemon=True)
                self.thread.start()
            except BaseException:
                if descriptor is not None:
                    os.close(descriptor)
                self.state, self.failure = "failed", "process_start_failed"
                self._cleanup()
                self.condition.notify_all()
                raise
        return self

    def _fault(self, reason):
        with self.condition:
            if self.failure is None:
                self.failure = reason
                print(f"slam_process_fault:{reason}", file=sys.stderr, flush=True)
            self.state = "failed"
            self.condition.notify_all()

    def _event(self, payload, now):
        event = decode(payload, self.limits.maximum_event_bytes)
        if event["session"] != self.session or event["sequence"] != self.sequence + 1:
            raise ValueError("matching_session_and_contiguous_health_sequence_required")
        if not 0 <= now - event["emittedAtSeconds"] <= self.limits.progress_seconds:
            raise ValueError("current_local_monotonic_health_event_required")
        counters = tuple(event[key] for key in ("completedMessages", "processedScans", "estimatedPoses"))
        if any(value < previous for value, previous in zip(counters, self.counters)):
            raise ValueError("worker_health_counters_cannot_regress")
        self.sequence, self.events = event["sequence"], self.events + 1
        self.worker_state = event["workerState"]
        if event["workerState"] == "failed":
            self.child_failure = event["failure"]
            self._fault("child_reported_failure")
        elif self.state == "stopping":
            self.retired_events += 1
        elif event["workerState"] == "running":
            if self.state == "starting":
                self.state, self.progress_at = "running", now
            if counters[0] > self.counters[0]:
                self.progress_at = now
        elif self.state == "running":
            self._fault("unexpected_worker_state")
        self.counters = counters
        self.mapper = event.get("mapper")

    def _consume(self, buffer, data):
        buffer.extend(data)
        processed = 0
        while b"\n" in buffer:
            end = buffer.index(b"\n") + 1
            payload = bytes(buffer[:end])
            del buffer[:end]
            with self.condition:
                self._event(payload, self._now())
            processed += 1
            if processed > self.limits.maximum_events_per_tick:
                raise ValueError("bounded_health_event_rate_required")
        if len(buffer) >= self.limits.maximum_event_bytes:
            raise ValueError("bounded_health_event_buffer_required")

    def _drain_exited(self, buffer):
        # The leader is reaped: drain only currently available bytes, never await descendant EOF.
        for _ in range(self.limits.maximum_events_per_tick):
            try:
                data = os.read(self.process.stdout.fileno(), self.limits.maximum_event_bytes)
            except BlockingIOError:
                break
            if not data:
                break
            self._consume(buffer, data)
        else:
            raise ValueError("bounded_terminal_health_backlog_required")
        if buffer:
            raise ValueError("incomplete_terminal_health_event")

    def _monitor(self):
        process, buffer = self.process, bytearray()
        stopping_at, eof = None, False
        try:
            os.set_blocking(process.stdout.fileno(), False)
            os.set_blocking(process.stdin.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    with self.condition:
                        now = self._now()
                        if self.requested_stop.is_set() and self.state in ("starting", "running"):
                            self.state, stopping_at = "stopping", now
                            # One small atomic command on a private pipe, never a robot command.
                            try:
                                if os.write(process.stdin.fileno(), b"stop\n") != 5:
                                    self._fault("child_stop_request_failed")
                            except OSError:
                                self._fault("child_stop_request_failed")
                        if self.state == "starting" and now - self.started_at > self.limits.startup_seconds:
                            self._fault("process_startup_deadline_exceeded")
                        elif self.state == "running" and now - self.progress_at > self.limits.progress_seconds:
                            self._fault("process_progress_deadline_exceeded")
                        elif self.state == "stopping" and now - stopping_at > self.limits.shutdown_seconds:
                            self._fault("process_shutdown_deadline_exceeded")
                        if self.state == "failed":
                            break
                    if not eof:
                        for _, _ in selector.select(self.limits.tick_seconds):
                            data = os.read(process.stdout.fileno(), self.limits.maximum_event_bytes)
                            if not data:
                                eof = True
                                selector.unregister(process.stdout)
                                if buffer:
                                    raise ValueError("incomplete_worker_health_event")
                                break
                            self._consume(buffer, data)
                    else:
                        time.sleep(self.limits.tick_seconds)
                    code = process.poll()
                    if code is not None:
                        if not eof:
                            self._drain_exited(buffer)
                        with self.condition:
                            self.exit_code = code
                            if self.state == "stopping" and code == 0 and self.worker_state == "stopped":
                                self.state = "stopped"
                                break
                            self._fault("child_exited_without_confirmed_stop")
                            break
                    if eof and self.state != "stopping":
                        self._fault("child_health_pipe_closed")
                        break
        except ValueError:
            self._fault("invalid_child_health_protocol")
        except BaseException:
            self._fault("process_monitor_failed")
        finally:
            self._cleanup()
            with self.condition:
                if self.state not in ("stopped", "failed"):
                    self._fault("process_monitor_stopped_unexpectedly")
                self.condition.notify_all()

    def _signal_group(self, value):
        if self.process is None:
            return
        try:
            os.killpg(self.process.pid, value)
        except ProcessLookupError:
            pass

    def _cleanup(self):
        process = self.process
        if process is None:
            self.cleanup_complete = True
            self._release_activity()
            return
        try:
            if process.poll() is None:
                self.forced_termination = True
                self._signal_group(signal.SIGTERM)
                try:
                    process.wait(timeout=self.limits.terminate_seconds)
                except subprocess.TimeoutExpired:
                    self._signal_group(signal.SIGKILL)
                    process.wait(timeout=self.limits.kill_seconds)
            # Descendants may still own a pipe after the child leader exits.
            if self._group_has_live_members():
                self.forced_termination = True
                self._fault("child_descendants_outlived_owner")
                self._signal_group(signal.SIGKILL)
            deadline = time.monotonic() + self.limits.kill_seconds
            while self._group_has_live_members() and time.monotonic() < deadline:
                time.sleep(min(self.limits.tick_seconds, .01))
            self.exit_code = process.poll()
            for stream in (process.stdin, process.stdout):
                stream.close()
            self.cleanup_complete = self.exit_code is not None and not self._group_has_live_members()
            if not self.cleanup_complete:
                self._fault("process_cleanup_failed")
        except BaseException:
            self.cleanup_complete = False
            self._fault("process_cleanup_failed")
        if self.cleanup_complete:
            self._release_activity()

    def _release_activity(self):
        if self.activity_lease is not None:
            self.activity_lease.close()

    def _group_has_live_members(self):
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                try:
                    fields = (Path(entry.path) / "stat").read_text().rsplit(")", 1)[1].split()
                    if int(fields[2]) == self.process.pid and fields[0] not in ("Z", "X"):
                        return True
                except (FileNotFoundError, ProcessLookupError):
                    continue
        return False

    def snapshot(self):
        with self.condition:
            return {
                "state": self.state, "failure": self.failure, "childFailure": self.child_failure,
                "session": self.session, "workerState": self.worker_state,
                "completedMessages": self.counters[0], "processedScans": self.counters[1],
                "estimatedPoses": self.counters[2], "healthEvents": self.events,
                "retiredHealthEvents": self.retired_events, "exitCode": self.exit_code,
                "processAlive": self.process is not None and self.process.poll() is None,
                "monitorAlive": self.thread is not None and self.thread.is_alive(),
                "forcedTermination": self.forced_termination, "cleanupComplete": self.cleanup_complete,
                "mappingReady": False, "localizationReady": False, "navigationReady": False,
                **({"mapper": self.mapper.copy()} if self.mapper is not None else {}),
            }

    def stop(self):
        if self.thread is threading.current_thread():
            raise RuntimeError("external_owner_must_join_process_monitor")
        with self.condition:
            if self.state == "new":
                self.state, self.cleanup_complete = "stopped", True
                self._release_activity()
            self.requested_stop.set()
            thread = self.thread
        if thread is not None and thread.ident is not None:
            thread.join(sum((self.limits.shutdown_seconds, self.limits.terminate_seconds,
                             2 * self.limits.kill_seconds, 1.)))
            if thread.is_alive():
                raise RuntimeError("process_monitor_shutdown_not_joined")
        return self.snapshot()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()
