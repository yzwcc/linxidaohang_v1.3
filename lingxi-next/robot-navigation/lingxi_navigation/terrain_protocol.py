"""Bounded private stdio protocol; never an HTTP or arbitrary-pickle endpoint."""
from array import array
import base64
from itertools import chain
import json
import struct
import sys

from .occupancy import DeclaredObservationBatch, LidarReturn, ScanObservation, canonical

MAXIMUM_MESSAGE_BYTES = 32 * 1024**2


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_terrain_protocol_key')
        result[key] = value
    return result


def decode(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAXIMUM_MESSAGE_BYTES:
        raise ValueError('bounded_terrain_message_required')
    def reject(_):
        raise ValueError('finite_terrain_protocol_json_required')
    value = json.loads(raw, object_pairs_hook=strict_object, parse_constant=reject)
    if type(value) is not dict:
        raise ValueError('terrain_protocol_object_required')
    return value


def frame(value):
    raw = canonical(value)
    if not 1 <= len(raw) <= MAXIMUM_MESSAGE_BYTES:
        raise ValueError('bounded_terrain_message_required')
    return struct.pack('!I', len(raw)) + raw


def read_message(stream):
    def exact(size):
        result = bytearray()
        while len(result) < size:
            chunk = stream.read(size - len(result))
            if not chunk:
                raise EOFError('terrain_pipe_closed')
            result.extend(chunk)
        return bytes(result)
    size, = struct.unpack('!I', exact(4))
    if not 1 <= size <= MAXIMUM_MESSAGE_BYTES:
        raise ValueError('bounded_terrain_message_required')
    return decode(exact(size))


def encode_observations(data, budget, *, packed=False):
    if type(data) is not DeclaredObservationBatch:
        raise ValueError('declared_terrain_batch_required_not_metric_admission')
    if type(packed) is not bool:
        raise ValueError('explicit_terrain_encoding_required')
    if (len(data.scans) > budget.maximum_scans
            or sum(len(scan.returns) for scan in data.scans) > budget.maximum_rays
            or data.scans[-1].stamp - data.scans[0].stamp > budget.maximum_span_seconds):
        raise ValueError('terrain_window_budget_exceeded')
    fields = {name: getattr(data, name) for name in data.__dataclass_fields__
              if name not in ('scans', 'frame', 'units')}
    scans = []
    for scan in data.scans:
        item = dict(ordinal=scan.ordinal, stamp=scan.stamp, source_sha256=scan.source_sha256)
        if packed:
            values = array('d', chain.from_iterable(ray.origin_metres + ray.endpoint_metres for ray in scan.returns))
            if sys.byteorder != 'little':
                values.byteswap()
            item['returns_f64le'] = base64.b64encode(values).decode('ascii')
        else:
            item['returns'] = [ray.origin_metres + ray.endpoint_metres for ray in scan.returns]
        scans.append(item)
    return dict(**fields, scans=scans)


def decode_observations(value, budget):
    if (type(value) is not dict or set(value) != {'map_id', 'receipt_sha256', 'revision', 'binding_sha256',
            'geometry_declaration_sha256', 'selection_sha256', 'scans'}
            or type(value['scans']) is not list or not 1 <= len(value['scans']) <= budget.maximum_scans):
        raise ValueError('bounded_complete_terrain_batch_required')
    scans, count = [], 0
    for scan in value['scans']:
        if type(scan) is not dict:
            raise ValueError('bounded_complete_terrain_scan_required')
        if set(scan) == {'ordinal', 'stamp', 'source_sha256', 'returns_f64le'}:
            encoded = scan['returns_f64le']
            if type(encoded) is not str or not 64 <= len(encoded) <= 8192 * 64 or len(encoded) % 64:
                raise ValueError('bounded_complete_packed_terrain_scan_required')
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) != len(encoded) // 64 * 48:
                raise ValueError('complete_packed_terrain_returns_required')
            size, returns, ray_type = len(raw) // 48, struct.iter_unpack('<6d', raw), tuple
        elif (set(scan) == {'ordinal', 'stamp', 'source_sha256', 'returns'}
                and type(scan['returns']) is list and 1 <= len(scan['returns']) <= 8192):
            size, returns, ray_type = len(scan['returns']), scan['returns'], list
        else:
            raise ValueError('bounded_complete_terrain_scan_required')
        count += size
        if count > budget.maximum_rays:
            raise ValueError('terrain_window_budget_exceeded')
        rays = []
        for ray in returns:
            if type(ray) is not ray_type or len(ray) != 6:
                raise ValueError('per_return_origin_and_endpoint_required')
            rays.append(LidarReturn(tuple(ray[:3]), tuple(ray[3:])))
        scans.append(ScanObservation(scan['ordinal'], scan['stamp'], scan['source_sha256'], tuple(rays)))
    result = DeclaredObservationBatch(**{key: item for key, item in value.items() if key != 'scans'}, scans=tuple(scans))
    if scans[-1].stamp - scans[0].stamp > budget.maximum_span_seconds:
        raise ValueError('terrain_window_budget_exceeded')
    return result
