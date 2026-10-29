"""Isolated synthetic byte producer; stdout is a dedicated test point pipe."""
import json
from pathlib import Path
import struct
import sys


def main():
    fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if sys.stdin.readline().strip() != "send-isolated-synthetic":
        return 1
    payload = bytearray.fromhex(fixture["hex"])
    # Exercise live-protocol reception solely in an isolated test process.
    payload[10] = 1
    for frame_id in (1, 2, 3):
        struct.pack_into("<Q", payload, 24, frame_id)
        struct.pack_into("<d", payload, 32, 1000 + frame_id)
        for offset in range(0, len(payload), 7):
            sys.stdout.buffer.write(payload[offset:offset + 7])
            sys.stdout.buffer.flush()
    return 0 if sys.stdin.readline().strip() == "close" else 1


if __name__ == "__main__":
    raise SystemExit(main())
