"""Private management socket and point-worker integration for saved-map tracking."""
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import sys
import threading
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "robot-slam"), str(ROOT / "tools")]
from lingxi_slam.localization_session import (
    LocalizationBinding, LocalizationControlError, LocalizationSession, dispatch)
from lingxi_slam.seeded_localization import RegistrationConfig, SeededMapRegistration
from lingxi_slam.map_provenance import sensor_provenance as checked_provenance
from run_glim_mapping_trial import inventory_map


def digest(data):
    return hashlib.sha256(data).hexdigest()


def direct(path, *, directory=False):
    path = Path(path)
    info = path.lstat()
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError("direct_absolute_localization_input_required")
    return info


def load_map(directory, map_id, receipt_sha256, profile, profile_sha256, *, session,
             input_mode, config=None, sensor_provenance=None):
    """Load owned point bytes from an integrity-checked map, never a client path.

    Versions 2/3 bind current sensor provenance; v3 selects full optimized maps.
    Version 1 remains diagnostic-only; no version proves physical calibration.
    """
    config = RegistrationConfig() if config is None else config
    if not isinstance(config, RegistrationConfig):
        raise ValueError("registration_config_required")
    # Validate all identities before opening files; points identity comes from the receipt.
    LocalizationBinding(session, input_mode, map_id, receipt_sha256, "0" * 64, profile_sha256)
    directory, profile = Path(directory), Path(profile)
    direct(directory, directory=True)
    receipt_file, points_file = directory / "map-ready.json", directory / "private-saved-points.f64"
    if direct(receipt_file).st_size > 16384 or direct(profile).st_size > 65536:
        raise ValueError("bounded_localization_receipt_and_profile_required")
    receipt_bytes, profile_bytes = receipt_file.read_bytes(), profile.read_bytes()
    if digest(receipt_bytes) != receipt_sha256 or digest(profile_bytes) != profile_sha256:
        raise ValueError("expected_localization_map_and_profile_required")
    receipt = strict_json(receipt_bytes)
    if type(receipt) is not dict:
        raise ValueError("complete_saved_localization_map_required")
    counts, inventory = receipt.get("counts"), receipt.get("mapInventory")
    if (type(counts) is not dict or type(inventory) is not dict
            or any(type(counts.get(key)) is not int or counts[key] < 1
                   for key in ("points", "savedFrames", "finalizedFrames", "submaps"))
            or type(receipt.get("schemaVersion")) is not int or receipt["schemaVersion"] not in (1, 2, 3)
            or receipt.get("inputMode") not in ("recorded", "live")
            or input_mode not in ("recorded", "live")
            or map_id != "slam-" + str(receipt.get("session"))
            or receipt.get("freshProcessReloadPassed") is not True
            or any(receipt.get(key) is not False for key in (
                "navigationReady", "localizationReady", "geometricAccuracyValidated"))
            or counts["savedFrames"] != counts["finalizedFrames"]
            or counts["savedFrames"] != inventory.get("num_all_frames")
            or counts["submaps"] != inventory.get("num_submaps")
            or not config.minimum_points <= counts["points"] <= config.maximum_map_points
            or direct(points_file).st_size != counts["points"] * 24):
        raise ValueError("complete_saved_localization_map_required")
    if (receipt["schemaVersion"] == 3 and receipt.get("mappingEngine") != "glim-full-async-v1"
            or receipt["schemaVersion"] != 3 and "mappingEngine" in receipt):
        raise ValueError("explicit_versioned_localization_mapping_engine_required")
    if receipt["schemaVersion"] in (2, 3):
        recorded = checked_provenance(receipt.get("sensorProvenance"))
        expected = checked_provenance(sensor_provenance)
        if recorded != expected or recorded["profileSha256"] != profile_sha256:
            raise ValueError("matching_saved_map_sensor_provenance_required")
    elif "sensorProvenance" in receipt:
        raise ValueError("versioned_saved_map_sensor_provenance_required")
    # Bound the tree before using the existing native-map inventory validator.
    map_root = directory / "map"
    direct(map_root, directory=True)
    entries, total = 0, 0
    pending = [(map_root, 0)]
    while pending:
        folder, depth = pending.pop()
        if depth > 4:
            raise ValueError("bounded_saved_localization_map_required")
        with os.scandir(folder) as children:
            for child in children:
                entries += 1
                info = child.stat(follow_symlinks=False)
                if entries > 4096 or stat.S_ISLNK(info.st_mode):
                    raise ValueError("bounded_direct_localization_map_required")
                if stat.S_ISDIR(info.st_mode):
                    pending.append((Path(child.path), depth + 1))
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if total > 64 * 1024 * 1024:
                        raise ValueError("bounded_saved_localization_map_required")
                else:
                    raise ValueError("direct_saved_localization_map_required")
    inspect_map = inventory_map
    if receipt["schemaVersion"] == 3:
        from refine_saved_map import inventory as full_inventory
        inspect_map = full_inventory
    if inspect_map(map_root) != inventory:
        raise ValueError("unchanged_saved_localization_map_required")
    point_bytes = points_file.read_bytes()
    binding = LocalizationBinding(
        session, input_mode, map_id, receipt_sha256, receipt.get("savedPointsSha256"), profile_sha256)
    if len(point_bytes) != counts["points"] * 24 or digest(point_bytes) != binding.points_sha256:
        raise ValueError("unchanged_saved_localization_points_required")
    points = np.frombuffer(point_bytes, dtype="<f8").reshape(-1, 3).copy()
    if not np.isfinite(points).all():
        raise ValueError("finite_saved_localization_points_required")
    if (receipt_file.read_bytes() != receipt_bytes or profile.read_bytes() != profile_bytes
            or inspect_map(map_root) != inventory):
        raise ValueError("localization_inputs_changed_during_load")
    points.flags.writeable = False
    return binding, points


class LocalizedNative:
    """Compose localization on the existing point worker; no new native thread."""

    def __init__(self, session, native_factory):
        if not isinstance(session, LocalizationSession) or not callable(native_factory):
            raise ValueError("localization_session_and_point_native_factory_required")
        self.session, self.closed, self.failed = session, False, False
        self.owner = threading.get_ident()
        session.open_worker()
        try:
            self.native = native_factory(self.accept)
        except BaseException:
            session.close_worker(failed=True)
            raise

    def accept(self, packet):
        self.session.observe(packet, session=self.session.binding.session,
                             input_mode=self.session.binding.input_mode, received_at=time.monotonic())

    def _call(self, method, *args):
        if self.closed or self.failed or threading.get_ident() != self.owner:
            raise RuntimeError("active_localization_native_required")
        try:
            self.session.tick()
            return getattr(self.native, method)(*args)
        except BaseException:
            self.failed = True
            raise

    def imu(self, *args):
        return self._call("imu", *args)

    def frame(self, *args):
        return self._call("frame", *args)

    def close(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError("same_thread_localization_native_close_required")
        if self.closed:
            return
        self.closed = True
        try:
            return self.native.close()
        except BaseException:
            self.failed = True
            raise
        finally:
            self.session.close_worker(failed=self.failed)


def strict_json(payload):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate_localization_request_field")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("finite_localization_json_required")
    return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)


class LocalizationRequest(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            _, uid, _ = struct.unpack(
                "3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                return
            self.request.settimeout(.25)
            line = self.rfile.readline(4097)
            if len(line) > 4096 or not line.endswith(b"\n"):
                raise ValueError("bounded_localization_request_required")
            result = {"ok": True, "value": dispatch(self.server.session, strict_json(line))}
        except LocalizationControlError as error:
            result = {"ok": False, "code": error.code, "status": error.status}
        except (ValueError, TypeError, UnicodeError):
            result = {"ok": False, "code": "invalid_localization_request", "status": 400}
        except Exception:
            result = {"ok": False, "code": "localization_control_unavailable", "status": 503}
        try:
            self.wfile.write((json.dumps(result, allow_nan=False, separators=(",", ":")) + "\n").encode())
        except OSError:
            pass


class LocalizationControlServer(getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):
    """One bounded request at a time; native calls never execute in this thread."""

    def __init__(self, endpoint, session):
        endpoint = Path(endpoint)
        parent = direct(endpoint.parent, directory=True)
        if (sys.platform != "linux" or os.geteuid() == 0 or not isinstance(session, LocalizationSession)
                or endpoint.exists() or endpoint.is_symlink() or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o077 or len(os.fsencode(endpoint)) >= 100):
            raise ValueError("new_private_same_user_localization_socket_required")
        self.session, self.endpoint, self.thread, self.identity = session, endpoint, None, None
        super().__init__(str(endpoint), LocalizationRequest, bind_and_activate=False)
        try:
            self.server_bind()
            self.identity = endpoint.lstat().st_ino
            os.chmod(endpoint, 0o600)
            self.server_activate()
        except BaseException:
            self.server_close()
            raise

    def start(self):
        if self.thread is not None:
            raise RuntimeError("localization_control_cannot_restart")
        self.thread = threading.Thread(
            target=lambda: self.serve_forever(poll_interval=.05), name="localization-control", daemon=True)
        self.thread.start()
        return self

    def server_close(self):
        # Revoke immediately, even if native cleanup must wait for the process supervisor.
        self.session.stop_admission()
        if self.thread is not None and self.thread.is_alive():
            self.shutdown()
            self.thread.join(1.)
        super().server_close()
        if (self.identity is not None and not self.endpoint.is_symlink()
                and self.endpoint.is_socket() and self.endpoint.lstat().st_ino == self.identity):
            self.endpoint.unlink()


def make_session(binding, points, *, base_metres_per_map_unit=None, registration_diagnostics=None):
    return LocalizationSession(binding, lambda: SeededMapRegistration(points, diagnostics=registration_diagnostics),
                               base_metres_per_map_unit=base_metres_per_map_unit)
