"""The terrain supervisor accepts only complete source-bound offline revisions."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS.parent / 'robot-navigation')]
import evaluate_recorded_terrain as runner
from lingxi_navigation.terrain import TerrainBudget
from lingxi_navigation.occupancy import ClearanceProjection


class TerrainReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.projection = ClearanceProjection(8, 8, .1, 0., 0.)
        self.costs = bytes([255] * 64)
        (self.output / 'private-terrain-grids.bin').write_bytes(self.costs)
        self.value = dict(ground=dict(sha256='a' * 64), octomap=dict(sha256='b' * 64, dependencies={}),
            projection=self.projection.__dict__, mapReceiptSha256='c' * 64, bindingSha256='d' * 64,
            maximumWorkUnits=16000000)
        self.report = dict(schemaVersion=1, result='recorded_rolling_terrain_not_navigation_acceptance',
            offlineOnly=True, sensorPaced=False, **dict.fromkeys(runner.FALSE_FLAGS, False),
            sourcesSha256={name: runner.digest(runner.ROOT / name) for name in runner.source_names(self.value) + runner.SOURCES},
            nativeSha256=dict(ground='a' * 64, octomap='b' * 64), dependenciesSha256={},
            input=dict(mapReceiptSha256='c' * 64, bindingSha256='d' * 64, scans=2, revision=7, offlineOnly=True,
                **dict.fromkeys(('physicalScaleValidated', 'groundFrameValidated', 'groundSupportValidated',
                    'obstacleCompletenessValidated', 'navigationReady'), False)),
            projection=self.projection.__dict__, budget=TerrainBudget(maximum_work_units=16000000).__dict__,
            artifactSha256=hashlib.sha256(self.costs).hexdigest(), artifactBytes=64,
            windows=[dict(sequence=0, sourceStart=0, sourceEnd=2, scans=2, rays=6, mapRevision=7,
                offlineOnly=True, physicalScaleValidated=False, groundSupportValidated=False, navigationReady=False,
                processingSeconds=.1, ingressToCompletionSeconds=.2,
                traversabilityBytesSha256=hashlib.sha256(self.costs).hexdigest(), cells=dict(free=0, occupied=0, unknown=64))],
            coveredSourceScans=2, unrepresentedSourceScans=0)

    def test_complete_report_and_exact_defaults_pass(self):
        runner.validate_report(self.value, self.report, self.output)
        with self.assertRaises(ValueError):
            runner.validate_report(dict(self.value, maximumWorkUnits=1), self.report, self.output)

    def test_wrong_sources_dependencies_frame_flags_or_geometry_fail(self):
        cases = []
        for key, value in (('schemaVersion', True), ('sensorPaced', True), ('navigationReady', True),
                ('nativeSha256', {}), ('sourcesSha256', {}), ('dependenciesSha256', {'wrong': 'a' * 64})):
            item = deepcopy(self.report)
            item[key] = value
            cases.append(item)
        for field in ('groundFrameValidated', 'groundSupportValidated', 'obstacleCompletenessValidated', 'navigationReady'):
            item = deepcopy(self.report)
            item['input'][field] = True
            cases.append(item)
        item = deepcopy(self.report)
        item['budget']['maximum_age_seconds'] = 15.
        cases.append(item)
        item = deepcopy(self.report)
        item['projection']['minimum_z_metres'] = 0.
        cases.append(item)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                runner.validate_report(self.value, item, self.output)

    def test_incomplete_stale_and_late_revisions_fail(self):
        changes = ({'sequence': 1}, {'sourceStart': 1}, {'sourceEnd': 3}, {'mapRevision': 8},
            {'scans': 1}, {'rays': 65537}, {'processingSeconds': 2.1}, {'processingSeconds': float('nan')},
            {'ingressToCompletionSeconds': 3.}, {'processingSeconds': .3}, {'navigationReady': True},
            {'cells': dict(free=64, occupied=0, unknown=0)}, {'traversabilityBytesSha256': 'e' * 64})
        for change in changes:
            item = deepcopy(self.report)
            item['windows'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                runner.validate_report(self.value, item, self.output)

    def test_modified_or_partial_artifact_and_bad_coverage_fail(self):
        for costs in (b'', bytes([255] * 63), bytes(64), bytes([1] * 64)):
            (self.output / 'private-terrain-grids.bin').write_bytes(costs)
            with self.subTest(size=len(costs)), self.assertRaises(ValueError):
                runner.validate_report(self.value, self.report, self.output)
        (self.output / 'private-terrain-grids.bin').write_bytes(self.costs)
        for key in ('coveredSourceScans', 'unrepresentedSourceScans', 'artifactBytes'):
            item = deepcopy(self.report)
            item[key] += 1
            with self.subTest(key=key), self.assertRaises(ValueError):
                runner.validate_report(self.value, item, self.output)

    @unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('numpy', 'scipy', 'json5')),
                         'requires isolated scientific dependencies')
    def test_worker_timeout_and_failure_never_publish_an_accepted_report(self):
        import refine_saved_map
        config = self.output / 'config.json'
        config.write_text('{}')
        value = dict(self.value, savedMap=str(self.output / 'source'), wallTimeoutSeconds=1)
        for mode in ('timeout', 'failure', 'missing-report'):
            output = self.output / mode
            def execute(command, **kwargs):
                if mode == 'timeout':
                    raise subprocess.TimeoutExpired(command, 1)
                return subprocess.CompletedProcess(command, 1 if mode == 'failure' else 0)
            with patch.object(runner, 'configuration', return_value=value), \
                    patch.object(refine_saved_map, 'private_output', return_value=output), \
                    patch.object(runner.subprocess, 'run', side_effect=execute):
                with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                    runner.run(config, output)
            self.assertFalse((output / 'report.json').exists())


if __name__ == '__main__':
    unittest.main()
