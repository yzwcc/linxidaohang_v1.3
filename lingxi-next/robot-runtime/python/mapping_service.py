"""Private local mapping control; native children use the existing process owner."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import struct
import sys
import threading
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "tools")]
from lingxi_slam.process_protocol import session_id
from lingxi_slam.process_supervisor import ProcessLimits, ProcessSupervisor
from mapping_platform import configuration


class ControlError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


class MappingManager:
    """One slot, explicit session ownership and no native calls in request handlers."""

    def __init__(self, maps, command_factory, environment, *, owner_factory=ProcessSupervisor,
                 points_enabled=False, input_mode="recorded"):
        self.maps = Path(maps)
        if (not self.maps.is_absolute() or self.maps.resolve(strict=True) != self.maps
                or not self.maps.is_dir() or not callable(command_factory) or type(points_enabled) is not bool
                or input_mode not in ("recorded", "live")):
            raise ValueError("direct_existing_mapping_catalog_required")
        self.command_factory, self.environment, self.owner_factory = command_factory, environment, owner_factory
        self.lock, self.operation = threading.Lock(), threading.Lock()
        self.current, self.closed = None, False
        self.points_enabled = points_enabled
        self.input_mode = input_mode

    def state(self):
        with self.lock:
            current = self.current
            if current is None:
                return {"backend": "glim", "inputMode": self.input_mode, "phase": "idle",
                        "session": None, "mapping": False, "cleanupComplete": True}
            state = current["owner"].snapshot()
            phase = current["phase"]
            if state["state"] == "failed":
                phase = "failed"
            elif phase == "requested" and state["state"] == "running":
                phase = "running"
            return {
                "backend": "glim", "inputMode": self.input_mode, "session": state["session"],
                "phase": phase,
                "mapping": phase in ("requested", "running", "saving", "stopping")
                or not state["cleanupComplete"] and state["state"] in ("stopped", "failed"),
                "cleanupComplete": state["cleanupComplete"],
                "completedMessages": state["completedMessages"],
                "processedScans": state["processedScans"], "estimatedPoses": state["estimatedPoses"],
                **({"pointStream": {"socket": current["output"].name + ".points.sock"}}
                   if self.points_enabled and phase in ("requested", "running") else {}),
            }

    def start(self):
        if not self.operation.acquire(blocking=False):
            raise ControlError("mapping_busy")
        try:
            with self.lock:
                if self.closed:
                    raise ControlError("mapping_service_closed", 503)
                if self.current is not None:
                    previous = self.current["owner"].snapshot()
                    if not previous["cleanupComplete"]:
                        raise ControlError("mapping_busy")
                identity = uuid.uuid4().hex
                output = self.maps / identity
                owner = self.owner_factory(
                    self.command_factory(output), cwd=self.maps, environment=self.environment,
                    stderr_path=self.maps / (identity + ".private.log"),
                    limits=ProcessLimits(startup_seconds=30., progress_seconds=2., shutdown_seconds=100.))
                self.current = {"owner": owner, "output": output, "phase": "requested",
                                "result": None, "decision": None}
            try:
                owner.start()
            except BaseException:
                with self.lock:
                    self.current["phase"] = "failed"
                raise ControlError("mapping_start_failed", 503) from None
            return self.state()
        finally:
            self.operation.release()

    def finish(self, session, *, save, name=None):
        try:
            session_id(session)
        except ValueError:
            raise ControlError("invalid_mapping_session", 400) from None
        if (type(save) is not bool or save and (
                type(name) is not str or not 1 <= len(name.strip()) <= 64)):
            raise ControlError("invalid_mapping_save", 400)
        if not self.operation.acquire(blocking=False):
            raise ControlError("mapping_busy")
        finishing = None
        try:
            with self.lock:
                current = self.current
                if current is None or current["owner"].session != session:
                    raise ControlError("mapping_session_changed")
                decision = ("save", name.strip()) if save else ("discard", None)
                if current["result"] is not None:
                    if current["decision"] != decision:
                        raise ControlError("mapping_already_finished")
                    return current["result"]
                owner = current["owner"]
                if save and owner.snapshot()["state"] != "running":
                    raise ControlError("mapping_not_running")
                current["phase"] = "saving" if save else "stopping"
                current["decision"] = decision
                finishing = current
            if save:
                private_json(self.maps / (current["output"].name + ".save-request.json"), {
                    "session": session, "name": name.strip(),
                })
            terminal = owner.stop()
            if (terminal["state"] != "stopped" or terminal["exitCode"] != 0
                    or terminal["forcedTermination"] or not terminal["cleanupComplete"]
                    or terminal["processAlive"] or terminal["monitorAlive"]):
                raise ControlError("mapping_stop_unconfirmed", 503)
            result = {"session": session, "saved": False, "confirmed": True}
            receipt_file = current["output"] / "map-ready.json"
            if save:
                if receipt_file.is_symlink() or not receipt_file.is_file() or receipt_file.stat().st_size > 16384:
                    raise ControlError("mapping_save_unconfirmed", 503)
                receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
                if (type(receipt) is not dict
                        or receipt.get("session") != session or receipt.get("name") != name.strip()
                        or receipt.get("inputMode") != self.input_mode
                        or receipt.get("freshProcessReloadPassed") is not True
                        or any(receipt.get(key) is not False for key in
                               ("navigationReady", "localizationReady", "geometricAccuracyValidated"))):
                    raise ControlError("mapping_save_unconfirmed", 503)
                result.update(saved=True, mapId="slam-" + session)
            elif receipt_file.exists():
                raise ControlError("discard_created_ready_map", 503)
            with self.lock:
                current["phase"], current["result"] = "idle", result
            return result
        except Exception as error:
            if finishing is not None:
                self._revoke(finishing["owner"])
                finishing["owner"].stop()
                ready = finishing["output"] / "map-ready.json"
                if ready.exists() and not ready.is_symlink():
                    # Preserve uncertain output without advertising it in the ready catalog.
                    ready.rename(finishing["output"] / "map-ready.unconfirmed")
                with self.lock:
                    finishing["phase"] = "failed"
            if isinstance(error, ControlError):
                raise
            raise ControlError("mapping_finish_unconfirmed", 503) from None
        finally:
            self.operation.release()

    @staticmethod
    def _revoke(owner):
        process = owner.process
        if process is not None and process.poll() is None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass

    def close(self):
        with self.lock:
            self.closed = True
            owner = None if self.current is None else self.current["owner"]
        if owner is not None:
            # Parent/control loss revokes saving in the child, even during independent reload.
            self._revoke(owner)
            with self.operation:
                return owner.stop()


def dispatch(manager, value):
    if type(value) is not dict or type(value.get("action")) is not str:
        raise ControlError("invalid_mapping_request", 400)
    action = value["action"]
    expected = {"action", "session", "name"} if action == "save" else (
        {"action", "session"} if action == "discard" else {"action"})
    if set(value) != expected:
        raise ControlError("invalid_mapping_request", 400)
    if action == "state":
        return manager.state()
    if action == "start":
        return manager.start()
    if action in ("save", "discard"):
        try:
            session_id(value["session"])
        except ValueError:
            raise ControlError("invalid_mapping_session", 400) from None
        return manager.finish(value["session"], save=action == "save", name=value.get("name"))
    raise ControlError("invalid_mapping_request", 400)


# Keep manager-only tests importable on Windows; serving is explicitly Linux-only.
class MappingSocketServer(socketserver.ThreadingMixIn,
                          getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):
    daemon_threads = True
    block_on_close = False

    def __init__(self, endpoint, manager):
        endpoint = Path(endpoint)
        if (sys.platform != "linux" or os.geteuid() == 0 or not endpoint.is_absolute()
                or endpoint.exists() or endpoint.is_symlink()
                or endpoint.parent.resolve(strict=True) != endpoint.parent
                or endpoint.parent.stat().st_mode & 0o077 or len(os.fsencode(endpoint)) >= 100):
            raise ValueError("new_private_ordinary_user_unix_mapping_socket_required")
        self.manager, self.endpoint = manager, endpoint
        super().__init__(str(endpoint), MappingRequest)
        os.chmod(endpoint, 0o600)

    def server_close(self):
        try:
            self.manager.close()
        finally:
            super().server_close()
            if self.endpoint.exists() and self.endpoint.is_socket():
                self.endpoint.unlink()


class MappingRequest(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            _, uid, _ = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                return
            self.request.settimeout(2.)
            line = self.rfile.readline(2049)
            if len(line) > 2048 or not line.endswith(b"\n"):
                raise ControlError("invalid_mapping_request", 400)
            result = {"ok": True, "value": dispatch(self.server.manager, json.loads(line))}
        except ControlError as error:
            result = {"ok": False, "code": error.code, "status": error.status}
        except Exception:
            result = {"ok": False, "code": "mapping_service_error", "status": 503}
        try:
            self.wfile.write((json.dumps(result, separators=(",", ":"), allow_nan=False) + "\n").encode())
        except OSError:
            # Request loss is not a new command, retry or a reason to undo a completed save.
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "maps", "socket"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    config, environment = configuration(args.config)
    manager = MappingManager(args.maps, lambda output: [
        sys.executable, "-B", str(ROOT / "robot-runtime/python/controlled_mapping.py"),
        "--config", str(args.config), "--output", str(output),
    ], environment, points_enabled="pointAdapter" in config, input_mode=config["inputMode"])
    with MappingSocketServer(args.socket, manager) as server:
        def stop(*_):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=.1)


if __name__ == "__main__":
    main()
