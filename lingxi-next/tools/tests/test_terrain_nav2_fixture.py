"""Frozen synthetic/native boundary checks; these are not robot acceptance."""
import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import terrain_nav2_fixture as fixture
from run_nav2_offline import assess_native_costmaps, assess_trial
from export_terrain_nav2_evidence import collect


class GeometryTests(unittest.TestCase):
    def test_default_full_height_and_occlusion(self):
        self.assertEqual((.1, 1.4), (fixture.projection().minimum_z_metres, fixture.projection().maximum_z_metres))
        self.assertEqual((.8, 0., .6), fixture.cast((0., 0., .6), (2., 0., .6), fixture.obstacle('detour')))
        self.assertEqual((1., 0., 0.), fixture.cast((0., 0., .6), (2., 0., -.6), None))

    def test_acquisition_does_not_invent_missing_floor_or_through_wall_returns(self):
        hole = fixture.acquisition('unsupported-shortcut')
        self.assertFalse(any(abs(ray.endpoint_metres[2]) < 1e-8 and fixture.missing_floor('unsupported-shortcut', ray.endpoint_metres)
            for scan in hole.scans for ray in scan.returns))
        blocked = fixture.acquisition('blocked')
        for scan in blocked.scans:
            for ray in scan.returns:
                if abs(ray.endpoint_metres[1]) <= 3. and 0 <= ray.endpoint_metres[2] <= 1.6:
                    if ray.origin_metres[0] < .8:
                        self.assertLessEqual(ray.endpoint_metres[0], .8 + 1e-8)
                    else:
                        self.assertGreaterEqual(ray.endpoint_metres[0], 1.3 - 1e-8)

    def test_every_original_unknown_and_obstacle_cell_is_regraded(self):
        source = dict(width=3, height=1, resolution=.1, origin=(0., 0.), cells=(-1, 100, 0))
        sample = dict(nativeGeometry=dict(width=3, height=1, resolution=.10000000149, origin=[0., 0.]),
                      nativeCosts=base64.b64encode(bytes((255, 100, 0))).decode())
        self.assertTrue(all(assess_native_costmaps(source, [sample]).values()))
        for costs in ((0, 100, 0), (255, 0, 0), (99, 100, 0), (255, 99, 0)):
            changed = {**sample, 'nativeCosts': base64.b64encode(bytes(costs)).decode()}
            self.assertFalse(all(assess_native_costmaps(source, [changed]).values()))
        for changed in ([], [{}], [{**sample, 'nativeCosts': 'invalid'}],
                        [{**sample, 'nativeGeometry': {**sample['nativeGeometry'], 'width': 4}}]):
            self.assertFalse(all(assess_native_costmaps(source, changed).values()))

    def test_incomplete_native_scene_collection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'all_four'):
            collect({}, {})


@unittest.skipUnless(os.environ.get('LINGXI_TEST_GROUND_LIBRARY') and os.environ.get('LINGXI_TEST_OCTOMAP_LIBRARY'),
                     'requires actual pinned native terrain builds')
class NativeExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ground = Path(os.environ['LINGXI_TEST_GROUND_LIBRARY'])
        cls.octomap = Path(os.environ['LINGXI_TEST_OCTOMAP_LIBRARY'])
        cls.gsha, cls.osha = os.environ['LINGXI_TEST_GROUND_SHA256'], os.environ['LINGXI_TEST_OCTOMAP_SHA256']
        cls.provenance = fixture.native_provenance(cls.ground, cls.gsha, cls.ground.parent / 'build-report.json',
            cls.octomap, cls.osha, cls.octomap.parent / 'build-report.json')
        segmenter = fixture.PatchworkSegmenter(cls.ground, cls.gsha)
        projector = fixture.OctomapProjector(cls.octomap, cls.osha, cls.provenance['dependenciesSha256'])
        cls.value = fixture.build('unsupported-shortcut', segmenter, projector, cls.osha, cls.provenance)
        cls.data = fixture.acquisition('unsupported-shortcut')

    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(temporary) / 'frozen.json'
        self.enterContext(patch.object(fixture, 'acquisition', return_value=self.data))

    def load(self, value=None):
        self.path.write_text(json.dumps(self.value if value is None else value), encoding='utf-8')
        return fixture.load(self.path, 'unsupported-shortcut')

    def test_actual_native_export_keeps_hole_and_rejects_cross_hole_trajectory(self):
        source, evidence = self.load()
        self.assertGreater(evidence['cells']['free'], 0)
        state = dict(phase='finished', requiresStop=False, outputQuiescent=True)
        assessment = assess_trial('unsupported-shortcut', source, [[0., 0., 0.], [2., 0., 0.]],
            [[.1, 0., 0.]], [[[0., 0.], [2., 0.]]], state)
        self.assertFalse(assessment['checks']['fullEnvelopeSupported'])
        self.assertFalse(evidence['liveFreshnessProven'])
        self.assertFalse(evidence['physicalScaleValidated'])

    def test_tampered_missing_flags_geometry_counts_statistics_and_sources_are_rejected(self):
        changes = [lambda v: v.update(navigationReady=True),
            lambda v: v['sourcesSha256'].update({'tools/terrain_nav2_fixture.py': '0' * 64}),
            lambda v: v.update(observationsSha256='0' * 64),
            lambda v: v['support'].pop('offline_only'),
            lambda v: v['clearance'].pop('physical_scale_validated'),
            lambda v: v['support'].update(ground_returns=0),
            lambda v: v['clearance'].update(statistics=(0, *v['clearance']['statistics'][1:])),
            lambda v: v['clearance']['projection'].update(minimum_z_metres=.5),
            lambda v: v['clearance'].update(costs=base64.b64encode(bytes(3840)).decode()),
            lambda v: v['summary'].update(physicalScaleValidated=True),
            lambda v: v['summary'].update(processingSeconds=-1.),
            lambda v: v['nativeBuild'].update(groundAdapterSha256='0' * 64)]
        for change in changes:
            value = copy.deepcopy(self.value)
            change(value)
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError, KeyError)):
                self.load(value)

    def test_wrong_native_build_identity_and_duplicate_json_are_rejected(self):
        with self.assertRaises(ValueError):
            fixture.native_provenance(self.ground, '0' * 64, self.ground.parent / 'build-report.json',
                self.octomap, self.osha, self.octomap.parent / 'build-report.json')
        for raw in ('{"case":"open","case":"blocked"}', '{"x":NaN}'):
            self.path.write_text(raw, encoding='utf-8')
            with self.assertRaises(ValueError):
                fixture.load(self.path, 'unsupported-shortcut')


if __name__ == '__main__':
    unittest.main()
