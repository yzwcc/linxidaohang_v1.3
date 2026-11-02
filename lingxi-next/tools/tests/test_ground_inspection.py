"""Synthetic geometry tests: a clean fitted plane is not a navigation permit."""
from dataclasses import replace
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'scipy'))
if AVAILABLE:
    from lingxi_navigation.ground import GroundDeclaration, GroundLabels
    from lingxi_navigation.ground_inspection import inspect_ground_labels
    from lingxi_navigation.occupancy import canonical


@unittest.skipUnless(AVAILABLE, 'requires numerical commissioning dependencies')
class Inspection(unittest.TestCase):
    def setUp(self):
        self.declaration = GroundDeclaration(.6, 0.)
        self.points = tuple((i / 5., j / 5., -.9) for i in range(-10, 11) for j in range(-10, 11))

    def labels(self, points, codes=None):
        return GroundLabels(bytes(len(points)) if codes is None else codes,
            hashlib.sha256(canonical(points)).hexdigest(), self.declaration.sha256, 'a' * 64)

    def inspect(self, points=None, labels=None):
        points = self.points if points is None else points
        return inspect_ground_labels(points, self.labels(points) if labels is None else labels,
            self.declaration, anchor_z_metres=.6)

    def test_flat_lower_plane_exposes_height_conflict_without_configuring_it(self):
        value = self.inspect()
        self.assertEqual(0, value['nativeGroundInDeclaredFloorBand'])
        self.assertFalse(value['declaredFloorBandHasNativeGroundReturns'])
        self.assertTrue(value['planeHint']['geometricallyConsistent'])
        self.assertAlmostEqual(.9, value['planeHint']['verticalOffsetDeclaredMetres'])
        self.assertFalse(value['physicalFloorIdentified'])
        self.assertFalse(value['configurationApplied'])
        self.assertFalse(value['navigationReady'])
        self.assertTrue(value['requiresPhysicalMeasurement'])

    def test_ceiling_labels_are_counted_but_never_fitted_into_the_lower_plane(self):
        points = self.points + tuple((x, y, 4.8) for x, y, z in self.points)
        value = self.inspect(points)
        self.assertEqual(len(self.points), value['nativeGroundAboveSensor'])
        self.assertAlmostEqual(.9, value['planeHint']['verticalOffsetDeclaredMetres'])
        self.assertIn('native_ground_labels_include_above_sensor_returns', value['issues'])

    def test_declared_band_points_alone_are_not_ground_support_acceptance(self):
        points = tuple((x, y, -.6) for x, y, z in self.points)
        value = self.inspect(points)
        self.assertTrue(value['declaredFloorBandHasNativeGroundReturns'])
        self.assertFalse(value['physicalFloorIdentified'])
        self.assertFalse(value['navigationReady'])

    def test_converged_solver_on_multiple_surfaces_is_still_inconclusive(self):
        points = self.points + tuple((x, y, -1.3) for x, y, z in self.points)
        value = self.inspect(points)
        self.assertTrue(value['planeHint']['solverConverged'])
        self.assertFalse(value['planeHint']['geometricallyConsistent'])
        self.assertIn('below_sensor_plane_hint_inconclusive', value['issues'])

    def test_line_geometry_and_sparse_or_absent_lower_labels_supply_no_plane(self):
        candidates = [tuple((i / 10., 0., -.9) for i in range(100)), self.points[:20],
                      tuple((x, y, 4.) for x, y, z in self.points)]
        for points in candidates:
            with self.subTest(count=len(points)):
                self.assertIsNone(self.inspect(points)['planeHint'])
        self.assertIsNone(self.inspect(labels=self.labels(self.points, bytes([254] * len(self.points))))['planeHint'])

    def test_source_or_declaration_changes_and_nonfinite_inputs_are_rejected(self):
        wrong = (replace(self.labels(self.points), input_sha256='b' * 64),
                 replace(self.labels(self.points), declaration_sha256='b' * 64))
        for labels in wrong:
            with self.assertRaises(ValueError):
                self.inspect(labels=labels)
        for points in (([1., 2., 3.],) * 3, ((1., 2., float('nan')),) * 3, self.points * 20):
            with self.assertRaises(ValueError):
                inspect_ground_labels(points, self.labels(self.points), self.declaration, anchor_z_metres=.6)

    def test_origin_height_mismatch_is_reported_without_moving_the_declared_frame(self):
        value = inspect_ground_labels(self.points, self.labels(self.points), self.declaration, anchor_z_metres=.9)
        self.assertIn('declared_sensor_origin_height_mismatch', value['issues'])
        self.assertTrue(value['declaredFloorBandHasNativeGroundReturns'])
        self.assertAlmostEqual(.3, value['declaredSensorHeightError'])
        self.assertFalse(value['configurationApplied'])


if __name__ == '__main__':
    unittest.main()
