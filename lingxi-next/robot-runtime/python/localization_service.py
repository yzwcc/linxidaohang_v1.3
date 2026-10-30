"""On-demand saved-map localization ownership; no HTTP, mapping, or movement."""
import argparse
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import socketserver
import stat
import struct
import sys
import threading
import uuid

from controlled_localization import verified_configuration
from localization_control import direct, digest, strict_json
from mapping_platform import MappingPlatform
from mapping_service import ControlError, PUBLIC_FAILURES
from lingxi_slam.localization_session import LocalizationBinding, LOCALIZATION_SOURCE_FRAME
from lingxi_slam.map_provenance import sensor_provenance
from lingxi_slam.process_supervisor import ProcessLimits, ProcessSupervisor
from lingxi_slam.activity_lease import ActivityBusy, ActivityGate


def selection(map_id, receipt_sha256):
    if (type(map_id) is not str or re.fullmatch(r"slam-[0-9a-f]{32}", map_id) is None
            or type(receipt_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", receipt_sha256) is None):
        raise ControlError("invalid_localization_map_selection", 400)
    return {"mapId": map_id, "receiptSha256": receipt_sha256}


@dataclass(frozen=True)
class SelectedMap:
    directory: Path
    binding: LocalizationBinding


class SavedMapResolver:
    """Resolve identities, never client paths. Full point/map loading stays in the child."""

    def __init__(self, maps, profile_sha256, input_mode, provenance):
        self.maps = Path(maps)
        direct(self.maps, directory=True)
        self.profile_sha256, self.input_mode = profile_sha256, input_mode
        self.provenance = sensor_provenance(provenance)
        if self.provenance["profileSha256"] != profile_sha256:
            raise ValueError("same_effective_localization_profile_required")
        LocalizationBinding("0" * 32, input_mode, "slam-" + "0" * 32,
                            "0" * 64, "0" * 64, profile_sha256)

    def __call__(self, map_id, receipt_sha256):
        selection(map_id, receipt_sha256)
        matches = []
        with os.scandir(self.maps) as entries:
            for index, entry in enumerate(entries):
                if index >= 2048:
                    raise ValueError("bounded_localization_catalog_required")
                if not entry.is_dir(follow_symlinks=False):
                    continue
                folder = Path(entry.path)
                direct(folder, directory=True)
                file = folder / "map-ready.json"
                try:
                    info = direct(file)
                except FileNotFoundError:
                    continue
                if info.st_size > 16384:
                    continue
                with file.open("rb") as stream:
                    payload = stream.read(16385)
                if len(payload) > 16384:
                    continue
                if digest(payload) != receipt_sha256:
                    continue
                receipt = strict_json(payload)
                if (type(receipt) is not dict or receipt.get("schemaVersion") != 2
                        or map_id != "slam-" + str(receipt.get("session"))
                        or receipt.get("inputMode") not in ("recorded", "live")
                        or receipt.get("freshProcessReloadPassed") is not True
                        or sensor_provenance(receipt.get("sensorProvenance")) != self.provenance):
                    raise ValueError("current_profile_bound_saved_map_required")
                binding = LocalizationBinding("0" * 32, self.input_mode, map_id, receipt_sha256,
                                              receipt.get("savedPointsSha256"), self.profile_sha256)
                matches.append(SelectedMap(folder, binding))
        if len(matches) != 1:
            raise ValueError("one_unambiguous_saved_map_selection_required")
        return matches[0]


class LocalizationManager:
    """Single native slot. Replacements require observed cleanup, never a timeout guess."""

    def __init__(self, runs, resolve_map, command_factory, environment, *, input_mode,
                 owner_factory=ProcessSupervisor, exchange=None, activity_gate=None):
        self.runs = Path(runs)
        parent = direct(self.runs, directory=True)
        if (sys.platform != "linux" or os.geteuid() == 0 or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o077 or not callable(resolve_map)
                or not callable(command_factory) or not callable(owner_factory)
                or input_mode not in ("recorded", "live")
                or len(os.fsencode(self.runs / ("a" * 32) / "localization.sock")) >= 100):
            raise ValueError("explicit_localization_service_configuration_required")
        self.resolve_map, self.command_factory = resolve_map, command_factory
        self.environment, self.owner_factory = environment.copy(), owner_factory
        self.input_mode, self.exchange = input_mode, exchange or self._exchange
        self.instance = uuid.uuid4().hex
        self.activity_gate = activity_gate
        self.lock, self.closed, self.generation, self.current = threading.RLock(), False, 0, None

    def runtime(self):
        with self.lock:
            current = self.current
            return {"backend": "glim", "inputMode": self.input_mode, "serviceId": self.instance,
                    "generation": self.generation,
                    "runId": current["id"] if current else None,
                    "phase": current["phase"] if current else "idle",
                    "selectedMap": current["selection"].copy() if current else None,
                    "cleanupComplete": current["cleanup"] if current else True,
                    "closed": self.closed, "localizationReady": False, "navigationReady": False,
                    **({"failureCode": current["failure"]} if current and current["failure"] else {})}

    def _generation(self, expected, service_id):
        if type(expected) is not int or expected < 0 or expected >= 2**53 - 1:
            raise ControlError("invalid_localization_runtime_generation", 400)
        if self.closed:
            raise ControlError("localization_service_closed", 503)
        if type(service_id) is not str or re.fullmatch(r"[0-9a-f]{32}", service_id) is None:
            raise ControlError("invalid_localization_service_identity", 400)
        if service_id != self.instance or expected != self.generation:
            raise ControlError("localization_runtime_changed")

    def start(self, map_id, receipt_sha256, expected_generation, service_id):
        requested = selection(map_id, receipt_sha256)
        with self.lock:
            self._generation(expected_generation, service_id)
            if self.current and (not self.current["cleanup"] or self.current["thread"].is_alive()):
                raise ControlError("localization_runtime_busy")
            current = {"id": uuid.uuid4().hex, "selection": requested, "phase": "starting",
                       "cleanup": False, "failure": None, "owner": None, "binding": None,
                       "stop": threading.Event(), "lease": None}
            current["thread"] = threading.Thread(target=self._run, args=(current,),
                                                  name="localization-session-owner", daemon=True)
            try:
                lease = self.activity_gate.acquire() if self.activity_gate is not None else None
            except ActivityBusy:
                raise ControlError("slam_activity_busy") from None
            current["lease"] = lease
            self.generation += 1
            self.current = current
            try:
                current["thread"].start()
            except BaseException:
                if lease is not None:
                    lease.close()
                current.update(phase="failed", failure="localization_process_failed", cleanup=True)
                raise
            return self.runtime()

    def shutdown(self, run_id, expected_generation, service_id):
        with self.lock:
            self._generation(expected_generation, service_id)
            if not self.current or type(run_id) is not str or run_id != self.current["id"]:
                raise ControlError("localization_runtime_changed")
            self.generation += 1
            current = self.current
            current["stop"].set()
            if not current["cleanup"] and not current["failure"]:
                current["phase"] = "stopping"
            return self.runtime()

    def _run(self, current):
        owner, failure, cleanup = None, None, True
        try:
            try:
                chosen = self.resolve_map(current["selection"]["mapId"], current["selection"]["receiptSha256"])
                if (not isinstance(chosen, SelectedMap) or chosen.binding.input_mode != self.input_mode
                        or chosen.binding.map_id != current["selection"]["mapId"]
                        or chosen.binding.receipt_sha256 != current["selection"]["receiptSha256"]):
                    raise ValueError("same_selected_map_required")
            except Exception:
                failure = "map_selection_unavailable"
                return
            if current["stop"].is_set():
                return
            output = self.runs / current["id"]
            output.mkdir(mode=0o700)
            owner = self.owner_factory(
                self.command_factory(chosen, output), cwd=output, environment=self.environment,
                stderr_path=output / "private-child.log",
                limits=ProcessLimits(startup_seconds=30., progress_seconds=2., shutdown_seconds=5.),
                **({"activity_lease": current["lease"]} if current["lease"] is not None else {}))
            with self.lock:
                current.update(owner=owner, binding=replace(chosen.binding, session=owner.session), output=output)
            if current["stop"].is_set():
                return
            owner.start()
            while not current["stop"].is_set():
                state = owner.snapshot()
                if state["state"] in ("failed", "stopped"):
                    failure = state.get("failure") if state.get("failure") in PUBLIC_FAILURES else "localization_process_failed"
                    break
                with self.lock:
                    if current["phase"] == "starting" and state["state"] == "running":
                        current["phase"] = "running"
                current["stop"].wait(.025)
        except Exception:
            failure = "localization_process_failed"
        finally:
            with self.lock:
                current.update(failure=failure, phase="failed" if failure else "stopping")
            if owner is not None:
                try:
                    terminal = owner.stop()
                    cleanup = terminal["cleanupComplete"] is True and not any(
                        terminal[key] for key in ("processAlive", "monitorAlive"))
                    if (not cleanup or terminal["forcedTermination"] or terminal["state"] != "stopped"
                            or terminal["exitCode"] not in (None, 0)):
                        failure = failure or "localization_process_failed"
                except Exception:
                    cleanup, failure = False, failure or "localization_process_failed"
            if cleanup and current["lease"] is not None:
                current["lease"].close()
            with self.lock:
                current.update(cleanup=cleanup, failure=failure, phase="failed" if failure else "stopped")

    @staticmethod
    def _exchange(endpoint, value):
        info = endpoint.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError("owned_private_localization_endpoint_required")
        payload = json.dumps(value, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(payload) > 4096:
            raise ControlError("invalid_localization_request", 400)
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(.5)
            client.connect(str(endpoint))
            client.sendall(payload)
            with client.makefile("rb") as stream:
                result = stream.readline(8193)
        if len(result) > 8192 or not result.endswith(b"\n"):
            raise ValueError("bounded_localization_response_required")
        return strict_json(result)

    def proxy(self, value):
        with self.lock:
            current = self.current
            if (self.closed or current is None or current["phase"] != "running"
                    or current["stop"].is_set() or current["owner"].snapshot()["state"] != "running"):
                raise ControlError("localization_worker_unavailable", 503)
            generation, binding = self.generation, asdict(current["binding"])
            if value.get("action") != "state" and value.get("binding") != binding:
                raise ControlError("localization_binding_changed")
            endpoint = current["output"] / "localization.sock"
        try:
            response = self.exchange(endpoint, value)
            if response.get("ok") is not True:
                if (response.get("ok") is False and response.get("status") in (400, 409, 503)
                        and type(response.get("code")) is str
                        and re.fullmatch(r"[a-z_]{1,100}", response["code"])):
                    raise ControlError(response["code"], response["status"])
                raise ValueError("valid_localization_response_required")
            result = response["value"]
            if (result["binding"] != binding or result["sourceFrame"] != LOCALIZATION_SOURCE_FRAME
                    or any(result[name] is not False for name in ("localizationReady", "navigationReady"))):
                raise ValueError("bound_localization_response_required")
        except ControlError:
            raise
        except Exception:
            raise ControlError("localization_worker_unavailable", 503) from None
        with self.lock:
            if (self.current is not current or generation != self.generation or self.closed
                    or current["stop"].is_set() or current["phase"] != "running"
                    or current["owner"].snapshot()["state"] != "running"):
                raise ControlError("localization_runtime_changed")
        return result

    def close(self):
        with self.lock:
            self.closed = True
            current = self.current
            if current:
                current["stop"].set()
                if not current["cleanup"] and not current["failure"]:
                    current["phase"] = "stopping"
        if current:
            if current["thread"].ident is not None:
                current["thread"].join(40.)
            if current["thread"].is_alive() or not current["cleanup"]:
                raise RuntimeError("localization_service_cleanup_unconfirmed")


def dispatch(manager, value):
    if type(value) is not dict or type(value.get("action")) is not str:
        raise ControlError("invalid_localization_service_request", 400)
    action = value["action"]
    if action == "runtime" and set(value) == {"action"}:
        return manager.runtime()
    if action == "start" and set(value) == {"action", "serviceId", "mapId", "receiptSha256", "expectedGeneration"}:
        return manager.start(value["mapId"], value["receiptSha256"], value["expectedGeneration"], value["serviceId"])
    if action == "shutdown" and set(value) == {"action", "serviceId", "runId", "expectedGeneration"}:
        return manager.shutdown(value["runId"], value["expectedGeneration"], value["serviceId"])
    fields = {"action"} if action == "state" else {"action", "binding", "expectedGeneration"}
    if action == "seed":
        fields |= {"sourceFrame", "referenceFrame", "mapFromSensor"}
    if action in ("state", "seed", "pause", "stop") and set(value) == fields:
        return manager.proxy(value)
    raise ControlError("invalid_localization_service_request", 400)


class ServiceRequest(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                return
            self.request.settimeout(1.)
            line = self.rfile.readline(4097)
            if len(line) > 4096 or not line.endswith(b"\n"):
                raise ControlError("invalid_localization_service_request", 400)
            result = {"ok": True, "value": dispatch(self.server.manager, strict_json(line))}
        except ControlError as error:
            result = {"ok": False, "code": error.code, "status": error.status}
        except (ValueError, TypeError, UnicodeError):
            result = {"ok": False, "code": "invalid_localization_service_request", "status": 400}
        except Exception:
            result = {"ok": False, "code": "localization_service_unavailable", "status": 503}
        try:
            self.wfile.write((json.dumps(result, allow_nan=False, separators=(",", ":")) + "\n").encode())
        except OSError:
            pass


class LocalizationServiceServer(getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):
    def __init__(self, endpoint, manager):
        endpoint = Path(endpoint)
        parent = direct(endpoint.parent, directory=True)
        if (sys.platform != "linux" or os.geteuid() == 0 or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o077 or endpoint.exists() or endpoint.is_symlink()
                or len(os.fsencode(endpoint)) >= 100):
            raise ValueError("new_private_localization_service_socket_required")
        self.manager, self.endpoint, self.identity = manager, endpoint, None
        super().__init__(str(endpoint), ServiceRequest, bind_and_activate=False)
        try:
            self.server_bind()
            self.identity = endpoint.lstat().st_ino
            os.chmod(endpoint, 0o600)
            self.server_activate()
        except BaseException:
            self.server_close()
            raise

    def server_close(self):
        try:
            self.manager.close()
        finally:
            super().server_close()
            if (self.identity is not None and not self.endpoint.is_symlink()
                    and self.endpoint.is_socket() and self.endpoint.lstat().st_ino == self.identity):
                self.endpoint.unlink()


def serve(endpoint, manager, *, parent_input=None, status=None):
    with LocalizationServiceServer(endpoint, manager) as server:
        stopping = threading.Event()
        parent_thread = None

        def stop(*_):
            if not stopping.is_set():
                stopping.set()
                with manager.lock:
                    manager.closed = True
                    if manager.current:
                        manager.current["stop"].set()
                threading.Thread(target=server.shutdown, daemon=True).start()

        previous = {value: signal.signal(value, stop) for value in (signal.SIGINT, signal.SIGTERM)}
        if parent_input is not None:
            def watch():
                try:
                    # Do not hold Python's stdin buffer lock across signal-driven shutdown.
                    with selectors.DefaultSelector() as selector:
                        selector.register(parent_input, selectors.EVENT_READ)
                        while not stopping.is_set():
                            if selector.select(.1):
                                break
                finally:
                    stop()
            parent_thread = threading.Thread(target=watch, name="localization-service-parent", daemon=True)
            parent_thread.start()
        try:
            if status is not None:
                status({"service": "lingxi-localization", "event": "listening"})
            server.serve_forever(poll_interval=.05)
        finally:
            stop()
            if parent_thread is not None:
                parent_thread.join(1.)
                if parent_thread.is_alive():
                    raise RuntimeError("localization_parent_watcher_shutdown_unconfirmed")
            for value, handler in previous.items():
                signal.signal(value, handler)


def create_manager(config_file, maps, runs):
    config, environment = verified_configuration(config_file, allow_live=True)
    platform = MappingPlatform(config)
    direct(maps, directory=True)
    direct(runs, directory=True)
    if maps == runs or maps in runs.parents or runs in maps.parents:
        raise ValueError("separate_saved_maps_and_localization_runs_required")
    profile_sha256 = digest(platform.profile.read_bytes())
    resolver = SavedMapResolver(maps, profile_sha256, config["inputMode"], platform.sensor_provenance())

    def command(chosen, output):
        return [sys.executable, "-B", str(Path(__file__).with_name("controlled_localization.py")), "child",
                "--config", str(config_file), "--saved-map", str(chosen.directory),
                "--map-id", chosen.binding.map_id, "--receipt-sha256", chosen.binding.receipt_sha256,
                "--profile-sha256", profile_sha256, "--output", str(output)]

    return LocalizationManager(runs, resolver, command, environment, input_mode=config["inputMode"],
                               activity_gate=ActivityGate(maps / ".slam-activity.lock"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "maps", "runs", "socket"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--parent-stdin", action="store_true")
    args = parser.parse_args()
    manager = create_manager(args.config, args.maps, args.runs)
    serve(args.socket, manager, parent_input=sys.stdin.buffer if args.parent_stdin else None,
          status=lambda value: print(json.dumps(value), flush=True))


if __name__ == "__main__":
    main()
