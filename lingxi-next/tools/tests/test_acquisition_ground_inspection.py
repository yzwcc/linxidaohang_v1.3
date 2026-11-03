"""Bounded numeric acquisition imports and source-bound read-only evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'robot-navigation'), str(ROOT / 'robot-slam')]
import audit_historical_evidence as historical
AVAILABLE = (all(importlib.util.find_spec(name) is not None for name in ('numpy', 'scipy', 'ruamel'))
             and importlib.util.find_spec('ruamel.yaml') is not None)
if AVAILABLE:
    import numpy as np
    import inspect_acquisition_ground as tool


@unittest.skipUnless(AVAILABLE, 'requires numerical commissioning dependencies')
class Samples(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.fields = dict(origins=np.zeros((3, 3), dtype=np.float64),
            endpoints=np.ones((3, 3), dtype=np.float64), world_lidar_pose=np.eye(4),
            frame_id=np.array(1), algorithm_stamp=np.array(1.), source_sha256=np.array('a' * 64))
        self.sequence = 0

    def write(self, changes=None):
        self.sequence += 1
        path = self.folder / (str(self.sequence) + '.npz')
        np.savez_compressed(path, **dict(self.fields, **(changes or {})))
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_only_complete_pinned_numeric_samples_load(self):
        origins, endpoints, frame, stamp = tool.sample(*self.write())
        self.assertEqual((3, 3), origins.shape)
        self.assertEqual((3, 3), endpoints.shape)
        self.assertEqual((1, 1.), (frame, stamp))

    def test_root_target_architecture_or_nonisolated_host_is_rejected_before_io(self):
        guards = (patch.object(tool.sys, 'platform', 'win32'),
                  patch.object(tool.platform, 'machine', return_value='aarch64'),
                  patch.object(tool.os, 'geteuid', return_value=0, create=True),
                  patch.object(tool.sys, 'prefix', tool.sys.base_prefix))
        for guard in guards:
            with guard, self.assertRaisesRegex(ValueError, 'isolated_ordinary_user_linux_x86'):
                tool.run(None)
        with patch.object(tool.platform, 'machine', return_value='aarch64'):
            with self.assertRaisesRegex(ValueError, 'isolated_ordinary_user_linux_x86'):
                tool.worker(None)

    def test_missing_hash_corruption_or_extra_field_is_rejected(self):
        path, digest = self.write()
        with self.assertRaises(ValueError):
            tool.sample(path, 'b' * 64)
        with self.assertRaises(ValueError):
            tool.sample(*self.write(dict(extra=np.array(1))))
        path.write_bytes(b'incomplete')
        with self.assertRaises(ValueError):
            tool.sample(path, digest)

    def test_wrong_pose_dtype_shape_size_nonfinite_or_metadata_is_rejected(self):
        for change in (dict(origins=np.zeros((2, 3))), dict(endpoints=np.ones((3, 2))),
                dict(origins=np.zeros((8193, 3)), endpoints=np.ones((8193, 3))),
                dict(origins=np.zeros((3, 3), dtype=np.float32)), dict(world_lidar_pose=np.zeros((4, 4))),
                dict(endpoints=np.full((3, 3), np.nan)), dict(frame_id=np.array(True)),
                dict(algorithm_stamp=np.array(np.inf)), dict(source_sha256=np.array('bad')),
                dict(source_sha256=np.array('a' * 64, dtype=object))):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                tool.sample(*self.write(change))

    def test_committed_actual_repeat_is_frozen_history_not_current_source_acceptance(self):
        report = json.loads((ROOT / 'docs/evidence/acquisition-ground-inspection-20261103.json').read_bytes())
        self.assertEqual(tool.RESULT, report['result'])
        self.assertEqual(tool.PURPOSE, report['purpose'])
        self.assertEqual(tool.BOUNDARIES, report['boundaries'])
        self.assertTrue(report['identicalRepeat'])
        self.assertEqual([0, 0], report['workerExitCodes'])
        verified = historical.verify('docs/evidence/acquisition-ground-inspection-20261103.json')
        self.assertTrue(verified['historicalSourcesVerified'])
        self.assertEqual(len(tool.SOURCES), verified['recordedSourceCount'])
        self.assertFalse(verified['currentSourceBindingsMatch'])
        self.assertIn('robot-navigation/lingxi_navigation/ground.py', verified['currentSourceDrift'])
        self.assertIn('robot-navigation/lingxi_navigation/occupancy.py', verified['currentSourceDrift'])
        self.assertEqual(['first', 'latest'], [row['sample'] for row in report['samples']])
        for row in report['samples']:
            self.assertEqual(0, row['nativeGroundInDeclaredFloorBand'])
            self.assertGreater(row['nativeGroundAboveSensor'], 0)
            self.assertTrue(row['planeHint']['solverConverged'])
            self.assertFalse(row['planeHint']['geometricallyConsistent'])
            for key in ('physicalFloorIdentified', 'physicalScaleValidated', 'groundSupportValidated',
                        'configurationApplied', 'navigationReady'):
                self.assertIs(row[key], False)
            self.assertTrue(row['requiresPhysicalMeasurement'])


if __name__ == '__main__':
    unittest.main()
