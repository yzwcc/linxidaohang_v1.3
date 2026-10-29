"""Linux-only bounded visual pipe writer; no native calls or robot control."""
import os
import select
import sys
import threading


MAXIMUM_BYTES = 192 + 8000 * 24


class LatestPointPipeWriter:
    """Own a dedicated FD: one partial write plus one replaceable latest record.

    The native owner offers encoded bytes without waiting for the consumer.
    Closing discards pending bytes; it is not a delivery acknowledgement.
    """

    def __init__(self, fd):
        if sys.platform != "linux" or type(fd) is not int or fd < 3:
            raise ValueError("dedicated_owned_linux_point_fd_required")
        os.set_blocking(fd, False)
        self.fd = fd
        self.condition = threading.Condition()
        self.pending = None
        self.closed = self.failed = False
        self.sent = self.replaced = 0
        self.thread = threading.Thread(target=self._run, name="slam-point-pipe", daemon=True)
        try:
            self.thread.start()
        except BaseException:
            os.close(fd)
            raise

    def offer(self, payload):
        if type(payload) is not bytes or not 216 <= len(payload) <= MAXIMUM_BYTES:
            raise ValueError("bounded_encoded_point_bytes_required")
        with self.condition:
            if self.closed or self.failed:
                raise RuntimeError("active_point_pipe_writer_required")
            if self.pending is not None:
                self.replaced += 1
            self.pending = payload
            self.condition.notify()

    def _run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or self.pending is not None)
                    if self.closed:
                        return
                    payload, self.pending = self.pending, None
                offset = 0
                while offset < len(payload):
                    with self.condition:
                        if self.closed:
                            return
                    if not select.select([], [self.fd], [], .05)[1]:
                        continue
                    try:
                        written = os.write(self.fd, memoryview(payload)[offset:])
                    except BlockingIOError:
                        continue
                    if written <= 0:
                        raise OSError("point_pipe_write_failed")
                    offset += written
                with self.condition:
                    self.sent += 1
                    self.condition.notify_all()
        except (OSError, ValueError):
            with self.condition:
                self.failed = True
                self.pending = None
                self.condition.notify_all()
        finally:
            os.close(self.fd)

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.condition.notify_all()
        self.thread.join(1)
        if self.thread.is_alive():
            raise RuntimeError("point_pipe_writer_shutdown_required")
