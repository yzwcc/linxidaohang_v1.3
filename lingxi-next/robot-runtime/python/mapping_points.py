"""One private point connection for one mapping child; health stays on stdout."""
import os
from pathlib import Path
import socket
import struct
import sys
import time


class MappingPointChannel:
    def __init__(self, endpoint, cancelled):
        self.endpoint, self.cancelled = Path(endpoint), cancelled
        self.listener = None
        if (sys.platform != "linux" or not callable(cancelled)
                or not self.endpoint.is_absolute() or self.endpoint.exists() or self.endpoint.is_symlink()
                or self.endpoint.parent.resolve(strict=True) != self.endpoint.parent
                or self.endpoint.parent.stat().st_mode & 0o077
                or self.endpoint.parent.stat().st_uid != os.geteuid()
                or len(os.fsencode(self.endpoint)) >= 100):
            raise ValueError("new_private_mapping_point_socket_required")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.endpoint))
            os.chmod(self.endpoint, 0o600)
            listener.listen(1)
            listener.settimeout(.05)
            self.listener = listener
        except BaseException:
            listener.close()
            if self.endpoint.is_socket():
                self.endpoint.unlink()
            raise

    def output(self, session, input_mode):
        from lingxi_slam.point_data import PointPipeOutput
        if self.listener is None:
            raise RuntimeError("one_mapping_point_consumer_required")
        deadline = time.monotonic() + 1.5
        while not self.cancelled() and time.monotonic() < deadline:
            try:
                connection, _ = self.listener.accept()
                break
            except socket.timeout:
                continue
        else:
            raise RuntimeError("mapping_point_consumer_connection_deadline")
        descriptor = None
        try:
            _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                raise RuntimeError("same_user_mapping_point_consumer_required")
            descriptor = connection.detach()
            result = PointPipeOutput(session, input_mode, descriptor)
            descriptor = None
            return result
        finally:
            connection.close()
            if descriptor is not None:
                os.close(descriptor)
            self.close()

    def close(self):
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        if self.endpoint.exists() and self.endpoint.is_socket():
            self.endpoint.unlink()
