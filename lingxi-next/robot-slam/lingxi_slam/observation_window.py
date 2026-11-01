"""Bounded globally corrected history, not a fresh local obstacle sensor."""
import ctypes
from dataclasses import dataclass, replace

from .observation_archive import ObservationArchive, MAX_SCANS, MAX_POINTS

MAX_WINDOW_SCANS, MAX_WINDOW_POINTS, MAX_WINDOW_BYTES = 8, 262144, 16 * 1024**2


@dataclass(frozen=True)
class ObservationWindow:
    revision: int
    first_ordinal: int
    first_submap: int
    observations: tuple
    payload_sha256: str
    coordinate_frame: str = "lingxi_glim_map"
    coordinate_units: str = "native-raw"
    input_kind: str = "delayed_finalized_submaps"
    navigation_ready: bool = False


def checked_header(header):
    values = tuple(header)
    if len(values) != 7 or any(type(value) is not int or not 0 <= value < 2**53 for value in values):
        raise ValueError("bounded_observation_window_header_required")
    revision, ordinal, submap, maps, scans, points, size = values
    if revision == 0:
        if any(values):
            raise ValueError("empty_window_has_no_geometry_required")
        return values
    if (not 1 <= maps <= scans <= MAX_WINDOW_SCANS or not scans <= points <= MAX_WINDOW_POINTS
            or ordinal + scans > MAX_SCANS or submap + maps > MAX_SCANS
            or submap > ordinal or not 40 <= size <= MAX_WINDOW_BYTES or points > MAX_POINTS):
        raise ValueError("bounded_observation_window_header_required")
    return values


def decode_window(header, data):
    revision, ordinal, submap, maps, scans, points, size = checked_header(header)
    if not revision or type(data) is not bytes or len(data) != size:
        raise ValueError("complete_observation_window_payload_required")
    archive = ObservationArchive.from_bytes(data)
    if (archive.revision, archive.submaps, archive.scans, archive.points) != (revision, maps, scans, points):
        raise ValueError("coherent_observation_window_payload_required")
    # Exhaust validation before exposing any geometry, including trailing-byte checks.
    observations = tuple(replace(scan, ordinal=scan.ordinal + ordinal, submap_id=scan.submap_id + submap)
                         for scan in archive)
    return ObservationWindow(revision, ordinal, submap, observations, archive.sha256)


def read_window(call, *, after_revision=0):
    if type(after_revision) is not int or not 0 <= after_revision < 2**53:
        raise ValueError("bounded_observation_revision_required")
    header = (ctypes.c_uint64 * 7)()
    for _ in range(4):
        if call(header, None, 0, 0) != 0:
            raise RuntimeError("observation_window_query_failed")
        values = checked_header(header)
        if values[0] <= after_revision:
            return None
        buffer = (ctypes.c_ubyte * values[6])()
        result = call(header, buffer, len(buffer), values[0])
        if result == 1:
            continue
        if result != 0 or checked_header(header) != values:
            raise RuntimeError("coherent_observation_window_copy_required")
        return decode_window(values, bytes(buffer))
    raise RuntimeError("observation_window_changed_retry_later")
