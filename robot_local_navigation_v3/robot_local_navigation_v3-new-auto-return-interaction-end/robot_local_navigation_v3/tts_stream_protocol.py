from __future__ import annotations

class TtsStreamProtocol:
    def __init__(self, idle_ttl_sec: float = 60.0) -> None:
        self.idle_ttl_sec = float(idle_ttl_sec)
        self._streams: dict[str, tuple[int, float]] = {}

    def claim(self, stream_id: str, sequence: int, now: float) -> bool:
        stream_id = str(stream_id or "").strip()
        if not stream_id or isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("invalid TTS stream id or sequence")

        self._expire(now)
        current = self._streams.get(stream_id)
        if sequence == 0:
            if current is not None:
                raise ValueError("TTS stream already started")
            self._streams[stream_id] = (1, now)
            return True

        if current is None or current[0] != sequence:
            raise ValueError("TTS stream sequence is out of order")
        self._streams[stream_id] = (sequence + 1, now)
        return False

    def close(self, stream_id: str) -> None:
        self._streams.pop(str(stream_id or "").strip(), None)

    def _expire(self, now: float) -> None:
        expired = [
            stream_id
            for stream_id, (_, updated_at) in self._streams.items()
            if now - updated_at > self.idle_ttl_sec
        ]
        for stream_id in expired:
            self._streams.pop(stream_id, None)
