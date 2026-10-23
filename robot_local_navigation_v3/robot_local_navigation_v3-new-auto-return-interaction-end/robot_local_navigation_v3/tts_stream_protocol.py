from __future__ import annotations

import time
from collections.abc import Callable
from threading import RLock


class TtsStreamProtocol:
    def __init__(self, idle_ttl_sec: float = 60.0) -> None:
        self.idle_ttl_sec = float(idle_ttl_sec)
        self._streams: dict[str, tuple[int, float]] = {}
        self._closed_streams: dict[str, float] = {}
        self._active_stream_id: str | None = None
        self._generation = 0
        self._lock = RLock()

    def claim(self, stream_id: str, sequence: int, now: float) -> bool:
        stream_id = str(stream_id or "").strip()
        if not stream_id or isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("invalid TTS stream id or sequence")

        with self._lock:
            self._expire(now)
            if stream_id in self._closed_streams:
                raise ValueError("TTS stream is closed")
            current = self._streams.get(stream_id)
            if sequence == 0:
                if current is not None:
                    raise ValueError("TTS stream already started")
                self._retire_active(now)
                self._generation += 1
                self._active_stream_id = stream_id
                self._streams[stream_id] = (1, now)
                return True

            if (
                stream_id != self._active_stream_id
                or current is None
                or current[0] != sequence
            ):
                raise ValueError("TTS stream sequence is out of order or preempted")
            self._streams[stream_id] = (sequence + 1, now)
            return False

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def invalidate(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            self._expire(now)
            self._retire_active(now)
            self._generation += 1
            return self._generation

    def close(self, stream_id: str, now: float | None = None) -> None:
        stream_id = str(stream_id or "").strip()
        if not stream_id:
            return
        now = time.monotonic() if now is None else float(now)
        with self._lock:
            self._expire(now)
            self._streams.pop(stream_id, None)
            self._closed_streams[stream_id] = now
            if self._active_stream_id == stream_id:
                self._active_stream_id = None
                self._generation += 1

    def dispatch_if_current(
        self,
        generation: int,
        stream_id: str | None,
        submit: Callable[[], object],
        now: float | None = None,
    ) -> object | None:
        with self._lock:
            self._expire(time.monotonic() if now is None else float(now))
            if generation != self._generation or stream_id != self._active_stream_id:
                return None
            if stream_id is not None and stream_id not in self._streams:
                return None
            return submit()

    def _retire_active(self, now: float) -> None:
        if self._active_stream_id is None:
            return
        self._streams.pop(self._active_stream_id, None)
        self._closed_streams[self._active_stream_id] = now
        self._active_stream_id = None

    def _expire(self, now: float) -> None:
        expired_streams = [
            stream_id
            for stream_id, (_, updated_at) in self._streams.items()
            if now - updated_at > self.idle_ttl_sec
        ]
        for stream_id in expired_streams:
            self._streams.pop(stream_id, None)
            self._closed_streams[stream_id] = now
            if self._active_stream_id == stream_id:
                self._active_stream_id = None
                self._generation += 1

        expired_closed = [
            stream_id
            for stream_id, closed_at in self._closed_streams.items()
            if now - closed_at > self.idle_ttl_sec
        ]
        for stream_id in expired_closed:
            self._closed_streams.pop(stream_id, None)
