"""Packing and immutable digest reuse must preserve the previous exact contracts."""
import ctypes
import base64
from copy import deepcopy
from dataclasses import asdict, fields, FrozenInstanceError, replace
import hashlib
import io
from pathlib import Path
import struct
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.ground import GroundDeclaration, PatchworkSegmenter
from lingxi_navigation.occupancy import (ClearanceProjection, DeclaredObservationBatch, LidarReturn,
    ObservationBatch, OctomapProjector, ScanObservation, canonical)
from lingxi_navigation.terrain import TerrainBudget
from lingxi_navigation.terrain_protocol import decode_observations, encode_observations, frame, read_message


def scan_digest(scan):
    digest = hashlib.sha256(canonical(dict(ordinal=scan.ordinal, stamp=scan.stamp,
        sourceSha256=scan.source_sha256, returns=len(scan.returns))))
    for ray in scan.returns:
        digest.update(struct.pack('<6d', *(ray.origin_metres + ray.endpoint_metres)))
    return digest.hexdigest()


def batch_digest(data):
    metadata = {field.name: getattr(data, field.name) for field in fields(data) if field.name != 'scans'}
    return hashlib.sha256(canonical({**metadata, 'scans': [scan_digest(scan) for scan in data.scans]})).hexdigest()


def scans():
    rays = (LidarReturn((-0., .125, .6), (1, -.25, .0125)),
            LidarReturn((.01, -.125, .61), (1.125, 0., -.0125)))
    return tuple(ScanObservation(i, 1000. + .1 * i, 'f' * 64, rays) for i in range(2))


def batches():
    yield ObservationBatch('slam-' + 'a' * 32, 'b' * 64, 1, 'c' * 64,
                           scans(), 'isolated_synthetic_rays')
    yield DeclaredObservationBatch('slam-' + 'a' * 32, 'b' * 64, 1, 'c' * 64,
                                   'd' * 64, 'e' * 64, scans())


class ImmutableDigestTests(unittest.TestCase):
    def test_cached_digests_are_byte_identical_to_original_algorithm(self):
        for data in batches():
            self.assertEqual(batch_digest(data), data.sha256)
            for scan in data.scans:
                self.assertEqual(scan_digest(scan), scan.sha256)

    def test_repeated_reads_do_not_hash_immutable_returns_again(self):
        for data in batches():
            for scan in data.scans:
                expected = scan_digest(scan)
                with patch('lingxi_navigation.occupancy.hashlib.sha256', wraps=hashlib.sha256) as digest:
                    self.assertEqual(expected, scan.sha256)
                    self.assertEqual(expected, scan.sha256)
                    self.assertEqual(1, digest.call_count)
            expected = batch_digest(data)
            with patch('lingxi_navigation.occupancy.hashlib.sha256', wraps=hashlib.sha256) as digest:
                self.assertEqual(expected, data.sha256)
                self.assertEqual(expected, data.sha256)
                self.assertEqual(1, digest.call_count)

    def test_replace_recomputes_changed_scan_and_batch_without_copying_cached_digest(self):
        for data in batches():
            before = data.sha256
            scan = data.scans[0]
            for changed in (replace(scan, stamp=scan.stamp - .01), replace(scan, source_sha256='0' * 64),
                    replace(scan, returns=(replace(scan.returns[0], endpoint_metres=(2., 0., 0.)),))):
                revised = replace(data, scans=(changed, data.scans[1]))
                self.assertNotEqual(scan.sha256, changed.sha256)
                self.assertNotEqual(before, revised.sha256)
                self.assertEqual(batch_digest(revised), revised.sha256)
            revised = replace(data, revision=2)
            self.assertNotEqual(before, revised.sha256)
            self.assertEqual(batch_digest(revised), revised.sha256)
            with self.assertRaises(FrozenInstanceError):
                data.sha256 = '0' * 64

    def test_cached_digest_is_not_a_dataclass_or_protocol_field(self):
        data = list(batches())[1]
        before, wire = asdict(data), encode_observations(data, TerrainBudget())
        self.assertEqual(batch_digest(data), data.sha256)
        self.assertEqual(before, asdict(data))
        self.assertEqual(wire, encode_observations(data, TerrainBudget()))
        self.assertNotIn('sha256', asdict(data))


class NativePackingTests(unittest.TestCase):
    def test_all_origins_endpoints_and_scan_boundaries_reach_octomap_unchanged(self):
        data = list(batches())[1]
        projector = OctomapProjector.__new__(OctomapProjector)
        projector.owner = threading.get_ident()
        captured = []
        def project(rays, count, ends, scans, resolution, maximum_range, ox, oy, zmin, zmax,
                    width, height, budget, output, size, stats):
            captured.append((ctypes.string_at(rays, count * 6 * ctypes.sizeof(ctypes.c_double)),
                             tuple(ends), count, scans, zmin, zmax, budget))
            output[:] = [255] * size
            stats[:] = (count, scans, 1, 0, 0, size, 1)
            return 0
        projector.native = SimpleNamespace(lingxi_octomap_project=project)
        result = projector.project_declared(data, ClearanceProjection(8, 8, .1, 0., 0.))
        legacy = (ctypes.c_double * 24)()
        for index, ray in enumerate(ray for scan in data.scans for ray in scan.returns):
            legacy[index * 6:index * 6 + 6] = ray.origin_metres + ray.endpoint_metres
        self.assertEqual([(bytes(legacy), (2, 4), 4, 2, .1, 1.4, 4000000)], captured)
        self.assertEqual(bytes([255] * 64), result.costs)
        self.assertFalse(result.navigation_ready)

    def test_ground_double_buffer_matches_legacy_for_small_and_maximum_scans(self):
        segmenter = PatchworkSegmenter.__new__(PatchworkSegmenter)
        segmenter.owner, segmenter.sha256 = threading.get_ident(), 'a' * 64
        captured = []
        def segment(xyz, count, height, minimum_range, maximum_range, labels, size, counts):
            captured.append(ctypes.string_at(xyz, count * 3 * ctypes.sizeof(ctypes.c_double)))
            labels[:] = [255] * size
            counts[:] = (0, 0, size)
            return 0
        segmenter.native = SimpleNamespace(lingxi_ground_segment=segment)
        for count in (3, 8192):
            points = tuple((-.0 if index == 0 else (index % 20) * .1, -.125, -.6) for index in range(count))
            legacy = (ctypes.c_double * (count * 3))(*(value for point in points for value in point))
            result = segmenter.segment(points, GroundDeclaration(.6, 0.))
            self.assertEqual(bytes(legacy), captured[-1])
            self.assertEqual(bytes([255] * count), result.labels)
            self.assertEqual(hashlib.sha256(canonical(points)).hexdigest(), result.input_sha256)


class PackedProtocolTests(unittest.TestCase):
    def test_little_endian_exact_doubles_origins_order_and_identity_roundtrip(self):
        data, budget = list(batches())[1], TerrainBudget()
        encoded = encode_observations(data, budget, packed=True)
        for original, packed in zip(data.scans, encoded['scans']):
            expected = b''.join(struct.pack('<6d', *(ray.origin_metres + ray.endpoint_metres)) for ray in original.returns)
            self.assertEqual(expected, base64.b64decode(packed['returns_f64le'], validate=True))
            self.assertNotIn('returns', packed)
        restored = decode_observations(read_message(io.BytesIO(frame(encoded))), budget)
        self.assertEqual(data, restored)
        self.assertEqual(data.sha256, restored.sha256)
        for old, new in zip(data.scans, restored.scans):
            for a, b in zip(old.returns, new.returns):
                self.assertEqual(struct.pack('<6d', *(a.origin_metres + a.endpoint_metres)),
                                 struct.pack('<6d', *(b.origin_metres + b.endpoint_metres)))

    def test_packed_and_legacy_streams_have_the_same_identity(self):
        data, budget = list(batches())[1], TerrainBudget()
        old = decode_observations(read_message(io.BytesIO(frame(encode_observations(data, budget)))), budget)
        new = decode_observations(read_message(io.BytesIO(frame(encode_observations(data, budget, packed=True)))), budget)
        self.assertEqual(old, new)
        self.assertEqual(old.sha256, new.sha256)

    def test_bad_encoding_lengths_values_and_ambiguous_schemas_are_rejected(self):
        data, budget = list(batches())[1], TerrainBudget()
        valid = encode_observations(data, budget, packed=True)
        bad = [dict(returns=[]), dict(returns_f64le=None), dict(returns_f64le=''),
               dict(returns_f64le='!' * 128), dict(returns_f64le='A' * (8192 * 64 + 64)),
               dict(returns_f64le='A' * 63), dict(returns_f64le='A' * 63 + '='),
               dict(returns_f64le=base64.b64encode(struct.pack('<6d', 0., 0., 0., float('nan'), 1., 0.)).decode()),
               dict(returns_f64le=base64.b64encode(struct.pack('<6d', 0., 0., 0., float('inf'), 1., 0.)).decode()),
               dict(returns_f64le=base64.b64encode(struct.pack('<6d', 0., 0., 0., 0., 0., 0.)).decode())]
        for changes in bad:
            value = deepcopy(valid)
            value['scans'][0].update(changes)
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                decode_observations(value, budget)
        for packed in (1, None, 'yes'):
            with self.assertRaises(ValueError):
                encode_observations(data, budget, packed=packed)

    def test_budget_is_checked_before_building_overbudget_ray_objects(self):
        data = list(batches())[1]
        encoded = encode_observations(data, TerrainBudget(), packed=True)
        encoded['scans'][0]['returns_f64le'] *= 4
        with patch('lingxi_navigation.terrain_protocol.LidarReturn', side_effect=AssertionError('must not construct')):
            with self.assertRaisesRegex(ValueError, 'window_budget'):
                decode_observations(encoded, TerrainBudget(maximum_rays=3))

    def test_maximum_scan_roundtrip_retains_every_ray_without_truncation(self):
        data = list(batches())[1]
        source = data.scans[0]
        data = replace(data, scans=(replace(source, returns=(source.returns[0],) * 8192),))
        encoded = encode_observations(data, TerrainBudget(), packed=True)
        restored = decode_observations(read_message(io.BytesIO(frame(encoded))), TerrainBudget())
        self.assertEqual(8192, len(restored.scans[0].returns))
        self.assertEqual(data.sha256, restored.sha256)


if __name__ == '__main__':
    unittest.main()
