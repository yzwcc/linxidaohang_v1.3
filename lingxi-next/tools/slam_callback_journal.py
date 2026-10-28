"""Bounded, private CDR callback journal; no pickle, ROS, network or actuation."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import struct
import threading


MAGIC = b"LXCDR\x00\x01\n"
RECORD = struct.Struct(">BQQI32s")
KINDS = {"cloud": 1, "imu": 2}
MAXIMUM_RECORDS = 20_000
MAXIMUM_PAYLOAD = 8 * 1024 * 1024
MAXIMUM_TOTAL = 512 * 1024 * 1024
MAXIMUM_ELAPSED_NS = 65_000_000_000


@dataclass(frozen=True)
class Entry:
    sequence: int
    kind: str
    elapsed_ns: int
    payload: bytes


def validate(sequence, elapsed_ns, size, previous, total):
    if (type(sequence) is not int or not 0 <= sequence < MAXIMUM_RECORDS
            or type(elapsed_ns) is not int or not previous <= elapsed_ns <= MAXIMUM_ELAPSED_NS
            or type(size) is not int or not 0 < size <= MAXIMUM_PAYLOAD
            or total + RECORD.size + size > MAXIMUM_TOTAL):
        raise ValueError("bounded_ordered_callback_record_required")


class Writer:
    def __init__(self, path):
        descriptor = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.stream = os.fdopen(descriptor, "wb")
        self.owner = threading.get_ident()
        self.digest = hashlib.sha256(MAGIC)
        self.count, self.previous, self.total = 0, 0, len(MAGIC)
        self.counts = dict.fromkeys(KINDS, 0)
        self.stream.write(MAGIC)

    def _active(self):
        if self.stream.closed or threading.get_ident() != self.owner:
            raise RuntimeError("one_active_callback_journal_owner_required")

    def append(self, kind, elapsed_ns, payload):
        self._active()
        if kind not in KINDS or type(payload) is not bytes:
            raise ValueError("explicit_sensor_kind_and_cdr_bytes_required")
        validate(self.count, elapsed_ns, len(payload), self.previous, self.total)
        header = RECORD.pack(KINDS[kind], self.count, elapsed_ns, len(payload),
                             hashlib.sha256(payload).digest())
        self.stream.write(header)
        self.stream.write(payload)
        self.digest.update(header)
        self.digest.update(payload)
        self.total += len(header) + len(payload)
        self.previous = elapsed_ns
        self.count += 1
        self.counts[kind] += 1

    def finish(self):
        self._active()
        payload = self.digest.digest()
        self.stream.write(RECORD.pack(0, self.count, self.previous, len(payload),
                                      hashlib.sha256(payload).digest()))
        self.stream.write(payload)
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        return {"records": self.count, "counts": self.counts.copy(),
                "bytes": self.total + RECORD.size + len(payload)}

    def abort(self):
        if not self.stream.closed:
            self.stream.close()


def records(path):
    """Exhaust the iterator to verify the commit footer and absence of trailing data."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAXIMUM_TOTAL + RECORD.size + 32:
        raise ValueError("bounded_direct_callback_journal_required")
    with path.open("rb") as stream:
        magic = stream.read(len(MAGIC))
        if magic != MAGIC:
            raise ValueError("versioned_callback_journal_required")
        digest = hashlib.sha256(magic)
        count, previous, total = 0, 0, len(MAGIC)
        while True:
            header = stream.read(RECORD.size)
            if len(header) != RECORD.size:
                raise ValueError("complete_callback_journal_footer_required")
            kind, sequence, elapsed, size, checksum = RECORD.unpack(header)
            if kind == 0:
                payload = stream.read(32)
                if (sequence != count or elapsed != previous or size != 32
                        or payload != digest.digest() or hashlib.sha256(payload).digest() != checksum
                        or stream.read(1)):
                    raise ValueError("matching_callback_journal_footer_required")
                return
            if kind not in KINDS.values() or sequence != count:
                raise ValueError("contiguous_sensor_callback_records_required")
            validate(sequence, elapsed, size, previous, total)
            payload = stream.read(size)
            if len(payload) != size or hashlib.sha256(payload).digest() != checksum:
                raise ValueError("complete_unchanged_callback_payload_required")
            digest.update(header)
            digest.update(payload)
            yield Entry(sequence, "cloud" if kind == 1 else "imu", elapsed, payload)
            count, previous, total = count + 1, elapsed, total + len(header) + size


def inspect(path):
    counts = dict.fromkeys(KINDS, 0)
    for entry in records(path):
        counts[entry.kind] += 1
    return {"records": sum(counts.values()), "counts": counts, "bytes": Path(path).stat().st_size}
