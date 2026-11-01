"""Regrade real isolated ROS samples; summary flags cannot hide retained free cells."""
import base64
import copy
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from verify_terrain_ros_evidence import load, verify


def fixture(mode='expiry'):
    return json.loads((ROOT / f'docs/evidence/terrain-ros-{mode}-20261101.json').read_bytes())


def alter_cell(sample, x, y, value):
    raw = bytearray(base64.b64decode(sample['cellsBase64']))
    ix = math.floor((x - sample['origin'][0]) / sample['resolution'])
    iy = math.floor((y - sample['origin'][1]) / sample['resolution'])
    assert 0 <= ix < sample['width'] and 0 <= iy < sample['height']
    raw[iy * sample['width'] + ix] = value
    sample['cellsBase64'] = base64.b64encode(raw).decode()


class TerrainRosEvidenceTests(unittest.TestCase):
    def test_all_three_preserved_actual_trials_are_source_bound(self):
        for mode in ('expiry', 'death', 'revoke'):
            with self.subTest(mode=mode):
                result = load(ROOT / f'docs/evidence/terrain-ros-{mode}-20261101.json')
                self.assertEqual(mode, result['mode'])
                self.assertTrue(result['passed'])
                self.assertTrue(result['sourceBound'])
                self.assertTrue(result['rawCostmapsRegraded'])
                self.assertFalse(result['navigationReady'])
                self.assertFalse(result['physicalStopAcknowledged'])

    def test_passed_flags_cannot_hide_one_retained_supported_cell(self):
        for name in ('global', 'local'):
            with self.subTest(consumer=name):
                report = fixture()
                grid = report['actualNativeGrid']
                index = base64.b64decode(grid['costsBase64']).index(0)
                spec = grid['projection']
                x = spec['origin_x_metres'] + (index % spec['width'] + .5) * spec['resolution_metres']
                y = spec['origin_y_metres'] + (index // spec['width'] + .5) * spec['resolution_metres']
                alter_cell(report['withdrawnMaps'][name], x, y, 0)
                self.assertTrue(report['passed'])
                self.assertTrue(report['checks'][name + 'RevokedOldFree'])
                with self.assertRaisesRegex(ValueError, 'old_supported_cells_not_revoked'):
                    verify(report)

    def test_retained_free_source_or_late_subscriber_is_rejected(self):
        for name in ('source', 'late'):
            with self.subTest(source=name):
                report = fixture()
                alter_cell(report['withdrawnMaps'][name], 1., 0., 0)
                with self.assertRaisesRegex(ValueError, 'full_unknown_replacement_required'):
                    verify(report)

    def test_source_map_geometry_cannot_move_even_if_all_cells_are_unknown(self):
        for table, name in (('initialMaps', 'source'), ('withdrawnMaps', 'source'), ('withdrawnMaps', 'late')):
            with self.subTest(table=table, name=name):
                report = fixture()
                report[table][name]['origin'][0] += .1
                with self.assertRaisesRegex(ValueError, 'unchanged_native_source_geometry'):
                    verify(report)

    def test_fake_native_bytes_or_source_publication_are_rejected(self):
        for table, key in (('actualNativeGrid', 'costsBase64'), ('initialMaps', 'source')):
            report = fixture()
            sample = report[table] if table == 'actualNativeGrid' else report[table][key]
            name = key if table == 'actualNativeGrid' else 'cellsBase64'
            raw = bytearray(base64.b64decode(sample[name]))
            raw[raw.index(0)] = 255
            sample[name] = base64.b64encode(raw).decode()
            with self.subTest(table=table), self.assertRaisesRegex(ValueError, 'native_terrain_bytes|native_source_map'):
                verify(report)

    def test_source_drift_or_missing_preparation_source_is_rejected(self):
        for table in ('sourcesSha256', 'preparationSources'):
            report = fixture()
            name = next(iter(report[table]))
            report[table][name] = '0' * 64
            with self.subTest(table=table), self.assertRaisesRegex(ValueError, 'source_drift'):
                verify(report)
            del report[table][name]
            with self.subTest(missing=table), self.assertRaisesRegex(ValueError, 'complete_.*sources'):
                verify(report)

    def test_no_upgrade_to_physical_or_metric_acceptance(self):
        for key in ('physicalScaleValidated', 'groundSupportValidated', 'navigationReady',
                    'physicalMotionSent', 'robotContacted', 'physicalStopAcknowledged'):
            report = fixture()
            report[key] = True
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'explicit_synthetic_only'):
                verify(report)

    def test_consumer_status_pending_publications_and_commands_are_regraded(self):
        changes = (dict(controllerStatus=4), dict(expiredPlannerStatus=4),
            dict(nativePendingPublications=0), dict(withdrawalSeconds=1.1),
            dict(postConsumptionCommands=[[.01, 0., 0.]]))
        for change in changes:
            report = copy.deepcopy(fixture())
            report.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify(report)


if __name__ == '__main__':
    unittest.main()
