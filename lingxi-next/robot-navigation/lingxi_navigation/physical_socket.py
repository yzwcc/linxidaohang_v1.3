"""Private Unix JSON Lines server driven by the bridge's single ROS owner."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import selectors
import socket
import stat
import struct
import sys
import threading
import time

from .physical_bridge import BridgeError, PhysicalNavigationBridge


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate_json_key')
        value[key] = item
    return value


def _constant(_value):
    raise ValueError('finite_json_required')


@dataclass
class _Client:
    connection: object
    deadline: float
    buffer: bytearray = field(default_factory=bytearray)
    output: bytes = b''
    identifier: str | None = None
    pending: str | None = None


class PhysicalNavigationSocket:
    def __init__(self, endpoint, bridge):
        endpoint = Path(endpoint)
        if (sys.platform != 'linux' or os.geteuid() == 0 or type(bridge) is not PhysicalNavigationBridge
                or not endpoint.is_absolute() or str(endpoint) != os.path.normpath(str(endpoint))
                or len(str(endpoint).encode()) >= 100 or endpoint.parent.resolve(strict=True) != endpoint.parent):
            raise ValueError('private_user_linux_navigation_socket_required')
        parent = endpoint.parent.stat()
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError('owned_private_navigation_socket_directory_required')
        self.owner, self.endpoint, self.bridge = threading.get_ident(), endpoint, bridge
        self.selector, self.clients = selectors.DefaultSelector(), {}
        self.closed, self.identity = False, None
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.listener.bind(str(endpoint))
            self.identity = endpoint.lstat().st_ino
            endpoint.chmod(0o600)
            self.listener.listen(8)
            self.listener.setblocking(False)
            self.selector.register(self.listener, selectors.EVENT_READ)
        except BaseException:
            self._cleanup()
            raise

    def _owned(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('single_navigation_socket_owner_required')

    def _drop(self, client):
        self.clients.pop(client.connection, None)
        try:
            self.selector.unregister(client.connection)
        except KeyError:
            pass
        client.connection.close()

    def _respond(self, client, *, value=None, error=None):
        response = (dict(id=client.identifier, ok=False, code=error.code, status=error.status)
                    if error else dict(id=client.identifier, ok=True, value=value))
        client.output = (json.dumps(response, allow_nan=False, separators=(',', ':')) + '\n').encode()
        if len(client.output) > 64 * 1024:
            raise ValueError('bounded_navigation_response_required')
        client.pending, client.deadline = None, time.monotonic() + 1.
        self.selector.modify(client.connection, selectors.EVENT_WRITE, client)

    def _read(self, client):
        chunk = client.connection.recv(4097 - len(client.buffer))
        if not chunk:
            self._drop(client)
            return
        client.buffer.extend(chunk)
        if len(client.buffer) > 4096 or client.buffer.count(b'\n') > 1:
            self._drop(client)
            return
        if b'\n' not in client.buffer:
            return
        if not client.buffer.endswith(b'\n'):
            self._drop(client)
            return
        request = json.loads(client.buffer, object_pairs_hook=_object, parse_constant=_constant)
        if (type(request) is not dict or type(request.get('id')) is not str
                or not re.fullmatch('[a-f0-9]{32}', request['id'])):
            self._drop(client)
            return
        client.identifier = request['id']
        try:
            result = self.bridge.request(request)
            if result is None:
                client.pending = request['action']
                client.deadline = time.monotonic() + self.bridge.retirement_timeout + .5
            else:
                self._respond(client, value=result)
        except BridgeError as error:
            self._respond(client, error=error)

    def step(self, timeout=.01):
        self._owned()
        if self.closed:
            return
        self.bridge.tick()
        for key, mask in self.selector.select(min(max(timeout, 0.), .05)):
            if key.fileobj is self.listener:
                connection, _ = self.listener.accept()
                _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.geteuid() or len(self.clients) >= 8:
                    connection.close()
                    continue
                connection.setblocking(False)
                client = _Client(connection, time.monotonic() + 1.)
                self.clients[connection] = client
                self.selector.register(connection, selectors.EVENT_READ, client)
                continue
            client = key.data
            try:
                if mask & selectors.EVENT_WRITE:
                    sent = client.connection.send(client.output)
                    client.output = client.output[sent:]
                    if not client.output:
                        self._drop(client)
                else:
                    self._read(client)
            except (OSError, ValueError, UnicodeError):
                self._drop(client)
        for client in tuple(self.clients.values()):
            if client.pending:
                expected = 'paused' if client.pending == 'pause' else 'idle'
                if (self.bridge.phase == expected and self.bridge.output_quiescent
                        and self.bridge.retiring is None and not self.bridge.requires_stop):
                    self._respond(client, value=self.bridge.state())
                elif self.bridge.phase == 'failed' or time.monotonic() >= client.deadline:
                    self._respond(client, error=BridgeError('navigation_retirement_unconfirmed', 503))
            elif time.monotonic() >= client.deadline:
                self._drop(client)

    def _cleanup(self):
        for client in tuple(self.clients.values()):
            self._drop(client)
        self.listener.close()
        self.selector.close()
        if (self.identity is not None and not self.endpoint.is_symlink() and self.endpoint.is_socket()
                and self.endpoint.lstat().st_ino == self.identity):
            self.endpoint.unlink()

    def close(self):
        self._owned()
        if self.closed:
            return self.bridge.output_quiescent
        self.closed = True
        try:
            self.bridge.close()
            deadline = time.monotonic() + self.bridge.retirement_timeout + .1
            while not self.bridge.output_quiescent and time.monotonic() < deadline:
                self.bridge.tick()
                time.sleep(.005)
            return self.bridge.output_quiescent
        finally:
            self._cleanup()
