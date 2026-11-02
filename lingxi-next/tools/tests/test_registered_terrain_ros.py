"""Regrade actual registered-source ROS samples, not summary flags alone."""
import base64
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).parent)]
from verify_registered_terrain_ros import MODES, load, verify
from test_terrain_ros_evidence import alter_cell


def fixture(mode='pause'):
    return json.loads((ROOT / f'docs/evidence/registered-terrain-ros-{mode}-20261103.json').read_bytes())


class RegisteredRosEvidenceTests(unittest.TestCase):
    def test_all_four_actual_trials_bind_sources_raw_costmaps_and_registration(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                result = load(ROOT / f'docs/evidence/registered-terrain-ros-{mode}-20261103.json')
                self.assertEqual(mode, result['mode'])
                for name in ('passed', 'sourceBound', 'rawCostmapsRegraded', 'registeredSourceRegraded'):
                    self.assertTrue(result[name])
                self.assertFalse(result['navigationReady'])
                self.assertFalse(result['physicalStopAcknowledged'])

    def test_changed_original_receipt_or_partial_window_is_not_accepted(self):
        for field, change in (('acceptedReceipt', lambda value: value + .01),
                              ('originalReceipts', lambda value: value[1:]),
                              ('selectedReturns', lambda value: value - 1),
                              ('selectedScans', lambda value: value - 1)):
            report = fixture()
            report['registeredInput'][field] = change(report['registeredInput'][field])
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'original_receipt_registered_epoch'):
                verify(report)

    def test_generation_not_retired_or_failed_retirement_flags_are_rejected(self):
        for field in ('sourceRetired', 'finalGridAbsent', 'noAdditionalRaysAfterSubmission', 'finalGeneration'):
            report = fixture()
            report['registeredInput'][field] = (report['registeredInput']['generation'] if field == 'finalGeneration' else False)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'original_receipt_registered_epoch'):
                verify(report)

    def test_wrong_frame_registrar_and_observation_identity_are_rejected(self):
        for field, value in (('units', 'metres'), ('registrar', 'production'),
                             ('observationsSha256', 'a' * 64), ('epochSha256', 'not_a_digest')):
            report = fixture()
            report['registeredInput'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'original_receipt_registered_epoch'):
                verify(report)

    def test_source_or_late_reader_retaining_one_free_cell_is_rejected(self):
        for name in ('source', 'late'):
            report = fixture()
            alter_cell(report['withdrawnMaps'][name], 1., 0., 0)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'full_unknown_replacement'):
                verify(report)

    def test_summary_checks_cannot_hide_one_old_supported_cell_in_either_costmap(self):
        for name in ('global', 'local'):
            report = fixture()
            spec = report['actualNativeGrid']['projection']
            index = base64.b64decode(report['actualNativeGrid']['costsBase64']).index(0)
            x = spec['origin_x_metres'] + (index % spec['width'] + .5) * spec['resolution_metres']
            y = spec['origin_y_metres'] + (index // spec['width'] + .5) * spec['resolution_metres']
            alter_cell(report['withdrawnMaps'][name], x, y, 0)
            self.assertTrue(report['checks'][name + 'RevokedOldFree'])
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'old_supported_cells_not_revoked'):
                verify(report)

    def test_each_mode_requires_its_actual_final_localization_state(self):
        for mode in MODES:
            report = fixture(mode)
            report['registeredInput']['finalState'] = 'unseeded'
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'expected_localization_retirement'):
                verify(report)
        reseed = fixture('reseed')['registeredInput']
        self.assertEqual('tracking', reseed['finalState'])
        self.assertTrue(reseed['sourceRetired'])
        self.assertTrue(reseed['finalGridAbsent'])

    def test_missing_registration_source_and_source_drift_are_rejected(self):
        name = 'robot-runtime/python/registered_terrain.py'
        report = fixture()
        del report['sourcesSha256'][name]
        with self.assertRaisesRegex(ValueError, 'complete_terrain_ros_sources'):
            verify(report)
        report = fixture()
        report['sourcesSha256'][name] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'terrain_ros_source_drift'):
            verify(report)

    def test_no_promotion_to_physical_calibration_motion_or_stop_acceptance(self):
        for key in ('physicalScaleValidated', 'groundSupportValidated', 'navigationReady',
                    'physicalMotionSent', 'robotContacted', 'physicalStopAcknowledged'):
            report = fixture()
            report[key] = True
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'explicit_synthetic_only'):
                verify(report)


if __name__ == '__main__':
    unittest.main()
