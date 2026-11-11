"""Private registration process: native GICP cannot hold the sensor owner's GIL."""
import math
import multiprocessing
import threading

from .seeded_localization import RegistrationConfig, SeededMapRegistration, point_cloud, rigid_pose


def _serve(connection, points, config, factory):
    registrar = None
    try:
        registrar = factory(points, config)
        connection.send(("ready", None))
        while True:
            command = connection.recv()
            if command is None:
                break
            scan, seed = command
            connection.send(("estimate", registrar.register(scan, seed)))
        close = getattr(registrar, "close", None)
        if callable(close):
            close()
        registrar = None
        connection.send(("closed", None))
    except BaseException:
        # Never propagate exception text or input coordinates to a log or API.
        try:
            connection.send(("failed", None))
        except (OSError, EOFError):
            pass
    finally:
        if registrar is not None:
            try:
                close = getattr(registrar, "close", None)
                if callable(close):
                    close()
            except BaseException:
                pass
        connection.close()


class ProcessMapRegistration:
    """Synchronous bounded RPC on the registration lane; no raw sensor input here.

    Spawn avoids forking ROS/native threads. The outer supervisor still owns
    the process group, so abnormal parent exit retires this child as well.
    """

    def __init__(self, points, config=None, *, diagnostics=None, timeout=.65,
                 startup_timeout=8., factory=SeededMapRegistration):
        self.config = RegistrationConfig() if config is None else config
        for value in (timeout, startup_timeout):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 10:
                raise ValueError("bounded_registration_process_deadline_required")
        c = self.config
        points = point_cloud(points, c.minimum_points, c.maximum_map_points, c.voxel_raw)
        self.owner, self.diagnostics = threading.get_ident(), diagnostics
        self.timeout, self.closed = timeout, False
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_serve, args=(child, points, c, factory),
                                       name="lingxi-map-registration", daemon=True)
        try:
            self.process.start()
            child.close()
            self._receive("ready", startup_timeout)
        except BaseException:
            child.close()
            self.close()
            raise

    def _receive(self, expected, timeout):
        if not self.connection.poll(timeout):
            raise RuntimeError("registration_process_deadline")
        try:
            kind, value = self.connection.recv()
        except (OSError, EOFError):
            raise RuntimeError("registration_process_failed") from None
        if kind != expected:
            raise RuntimeError("registration_process_failed")
        return value

    def register(self, scan, seed):
        if self.closed or threading.get_ident() != self.owner:
            raise RuntimeError("active_single_registration_process_owner_required")
        c = self.config
        scan = point_cloud(scan, c.minimum_points, c.maximum_scan_points, c.voxel_raw)
        seed = rigid_pose(seed)
        try:
            self.connection.send((scan, seed))
            estimate = self._receive("estimate", self.timeout)
        except BaseException:
            self.close()
            raise
        if estimate.map_from_sensor is not None:
            estimate.map_from_sensor.flags.writeable = False
        if self.diagnostics is not None:
            self.diagnostics.record(estimate)
        return estimate

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.process.pid is not None and self.process.is_alive():
                try:
                    self.connection.send(None)
                    self._receive("closed", .25)
                except (RuntimeError, OSError, EOFError):
                    self.process.terminate()
                self.process.join(.25)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(.25)
                if self.process.is_alive():
                    raise RuntimeError("registration_process_cleanup_deadline")
            if self.process.pid is not None:
                self.process.join(.25)
        finally:
            self.connection.close()
