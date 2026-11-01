"""Supervisor failures never publish an accepted offline-clearance report."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import evaluate_recorded_clearance as runner

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ("numpy", "scipy", "json5"))


class ClearanceConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.config = self.root / "configuration.json"
        self.value = {"schemaVersion": 1, "purpose": "offline_declared_clearance_only",
            "savedMap": str(self.root / "source"), "mapReceiptSha256": "a" * 64,
            "bindingSha256": "b" * 64, "observationImplementationSha256": "c" * 64,
            "geometry": {"metresPerRawUnit": 1., "declaredFromRaw": [1., 0, 0, 0, 0, 1., 0, 0, 0, 0, 1., 0, 0, 0, 0, 1.]},
            "selection": {"maximum_rays": 131072, "maximum_per_scan": 8192},
            "projection": {"width": 128, "height": 128, "resolution_metres": .1,
                           "origin_x_metres": -6.4, "origin_y_metres": -6.4},
            "octomap": {"library": "unused", "sha256": "d" * 64, "dependencies": {}},
            "maximumWorkUnits": 16000000, "wallTimeoutSeconds": 300}
        self.write()

    def write(self):
        self.config.write_text(json.dumps(self.value), encoding="utf-8")

    def test_bounded_explicit_configuration(self):
        self.assertEqual(self.value, runner.configuration(self.config))
        for key, value in (("purpose", "navigation"), ("schemaVersion", True), ("wallTimeoutSeconds", 301),
                           ("wallTimeoutSeconds", 0), ("maximumWorkUnits", 16000001), ("maximumWorkUnits", True)):
            with self.subTest(key=key, value=value):
                original = self.value[key]
                self.value[key] = value
                self.write()
                with self.assertRaises(ValueError):
                    runner.configuration(self.config)
                self.value[key] = original
        self.value["extra"] = True
        self.write()
        with self.assertRaises(ValueError):
            runner.configuration(self.config)
        self.config.write_text(" " * 32769)
        with self.assertRaises(ValueError):
            runner.configuration(self.config)

    def test_ground_requires_an_explicit_separate_library_and_height_declaration(self):
        ground = {'library': '/offline/liblingxi_ground.so', 'sha256': 'e' * 64,
                  'declaration': {'sensor_height_metres': .6, 'ground_z_metres': 0.}}
        self.value['ground'] = ground
        self.write()
        self.assertEqual(self.value, runner.configuration(self.config))
        for invalid in (None, {}, {**ground, 'sha256': 1}, {**ground, 'library': 1},
                {**ground, 'declaration': {}}, {**ground, 'declaration': {'sensor_height_metres': .6}},
                {**ground, 'declaration': {**ground['declaration'], 'physicalCalibration': True}}):
            self.value['ground'] = invalid
            self.write()
            with self.subTest(ground=invalid), self.assertRaises(ValueError):
                runner.configuration(self.config)

    @unittest.skipUnless(AVAILABLE, 'requires isolated scientific Python dependencies')
    def test_ground_artifacts_fail_closed_before_publishing_supervised_report(self):
        sys.path.insert(0, str(runner.ROOT / 'robot-navigation'))
        from lingxi_navigation.ground import (DeclaredGroundCandidate, DeclaredTraversability,
                                             GroundDeclaration)
        from lingxi_navigation.occupancy import ClearanceProjection, DeclaredClearanceResult
        self.value['ground'] = {'library': 'unused', 'sha256': 'e' * 64,
            'declaration': {'sensor_height_metres': .6, 'ground_z_metres': 0.}}
        modes = ('missing-report', 'accepted-flag', 'stale-library', 'stale-declaration',
                 'stale-revision', 'bad-label-counts', 'negative-label-counts',
                 'missing-ground', 'missing-fusion', 'changed-ground', 'changed-fusion',
                 'bad-cell-counts', 'invalid-cell', 'air-without-support',
                 'stale-candidate-identity', 'stale-fusion-identity', 'stale-ground-identity',
                 'stale-clearance-identity', 'missing-native-statistics', 'changed-native-statistics',
                 'invalid-native-statistics', 'exceeded-work-budget', 'accepted')
        for mode in modes:
            with self.subTest(mode=mode):
                output = self.root / ('ground-' + mode)
                self.write()
                def execute(command, **kwargs):
                    air, support, fused = bytes(16384), bytes([255]) * 16384, bytes([255]) * 16384
                    declaration = GroundDeclaration(**self.value['ground']['declaration'])
                    projection = ClearanceProjection(**self.value['projection'])
                    candidate = DeclaredGroundCandidate('a' * 64, declaration.sha256,
                        'e' * 64, projection, support, 2, 6, 0, 0)
                    statistics = dict(rays=6, scans=2, nodes=123,
                        **runner.cell_statistics(air), workUnits=456)
                    clearance = DeclaredClearanceResult('a' * 64, projection, air,
                        tuple(statistics[name] for name in runner.STATISTIC_NAMES))
                    traversal = DeclaredTraversability(clearance.sha256, candidate.sha256, fused)
                    ground = {'nativeSha256': 'e' * 64, 'declaration': self.value['ground']['declaration'],
                        'declarationSha256': declaration.sha256, 'candidateSha256': candidate.sha256,
                        'traversabilitySha256': traversal.sha256,
                        'upstreamRevision': json.loads((runner.ROOT / runner.GROUND_SOURCES[2]).read_text())['revision'],
                        'groundSupportValidated': False, 'navigationReady': False,
                        'labels': {'groundReturns': 6, 'nongroundReturns': 0, 'unclassifiedReturns': 0},
                        'candidateBytesSha256': hashlib.sha256(support).hexdigest(),
                        'traversabilityBytesSha256': hashlib.sha256(fused).hexdigest(),
                        'candidateCells': runner.cell_statistics(support),
                        'traversabilityCells': runner.cell_statistics(fused)}
                    if mode == 'accepted-flag':
                        ground['groundSupportValidated'] = True
                    if mode == 'stale-library':
                        ground['nativeSha256'] = 'f' * 64
                    if mode == 'stale-declaration':
                        ground['declaration'] = {}
                    if mode == 'stale-revision':
                        ground['upstreamRevision'] = '0' * 40
                    if mode == 'bad-label-counts':
                        ground['labels']['groundReturns'] = 7
                    if mode == 'negative-label-counts':
                        ground['labels'].update(groundReturns=7, nongroundReturns=-1)
                    if mode == 'bad-cell-counts':
                        ground['candidateCells']['unknownCells'] = 0
                    if mode == 'invalid-cell':
                        support = bytes([1]) * 16384
                        ground['candidateBytesSha256'] = hashlib.sha256(support).hexdigest()
                    if mode == 'air-without-support':
                        fused = bytes(16384)
                        ground['traversabilityBytesSha256'] = hashlib.sha256(fused).hexdigest()
                        ground['traversabilityCells'] = runner.cell_statistics(fused)
                    for current, key in (('stale-candidate-identity', 'candidateSha256'),
                            ('stale-fusion-identity', 'traversabilitySha256'),
                            ('stale-ground-identity', 'declarationSha256')):
                        if mode == current:
                            ground[key] = '0' * 64
                    report = {'result': 'recorded_declared_clearance_evaluated_not_navigation',
                        'configurationSha256': runner.digest(self.config), 'offlineOnly': True,
                        'projection': self.value['projection'], 'statistics': statistics,
                        'nativeSha256': self.value['octomap']['sha256'],
                        'loadedDependenciesSha256': self.value['octomap']['dependencies'],
                        'runnerSourcesSha256': {name: runner.digest(runner.ROOT / name)
                                               for name in runner.source_names(self.value)},
                        'clearanceBytesSha256': hashlib.sha256(air).hexdigest(),
                        'clearanceSha256': clearance.sha256, 'selectedReturns': 6,
                        'observationsSha256': 'a' * 64, 'scans': 2,
                        **dict.fromkeys(('physicalScaleValidated', 'groundFrameValidated', 'navigationReady',
                            'groundSupportValidated', 'obstacleCompletenessValidated',
                            'robotContacted', 'sensorsSubscribed', 'physicalMotionSent'), False)}
                    if mode == 'stale-clearance-identity':
                        report['clearanceSha256'] = '0' * 64
                    if mode == 'missing-native-statistics':
                        del statistics['workUnits']
                    if mode == 'changed-native-statistics':
                        statistics['nodes'] += 1
                    if mode == 'invalid-native-statistics':
                        statistics['nodes'] = True
                    if mode == 'exceeded-work-budget':
                        statistics['workUnits'] = self.value['maximumWorkUnits'] + 1
                    if mode != 'missing-report':
                        report['ground'] = ground
                    (output / 'private-declared-clearance.bin').write_bytes(air)
                    if mode != 'missing-ground':
                        (output / 'private-declared-ground.bin').write_bytes(air if mode == 'changed-ground' else support)
                    if mode != 'missing-fusion':
                        (output / 'private-declared-traversability.bin').write_bytes(air if mode == 'changed-fusion' else fused)
                    (output / 'report.pending').write_text(json.dumps(report))
                    return subprocess.CompletedProcess(command, 0)
                with patch('refine_saved_map.private_output', return_value=output), \
                        patch.object(runner.subprocess, 'run', side_effect=execute):
                    if mode == 'accepted':
                        result = runner.run(self.config, output)
                        self.assertFalse(result['navigationReady'])
                        self.assertTrue((output / 'report.json').is_file())
                    else:
                        with self.assertRaises((ValueError, RuntimeError)):
                            runner.run(self.config, output)
                        self.assertFalse((output / 'report.json').exists())

    @unittest.skipUnless(AVAILABLE, "requires isolated scientific Python dependencies")
    def test_timeout_child_failure_missing_result_and_false_acceptance_do_not_publish(self):
        for mode in ("timeout", "exit", "missing", "bad-report", "changed-config", "changed-source",
                     "missing-artifact", "changed-artifact", "bad-counts", "bad-support", "bad-size", "accepted"):
            with self.subTest(mode=mode):
                output = self.root / mode
                self.write()
                def execute(command, **kwargs):
                    self.assertEqual(300, kwargs["timeout"])
                    self.assertEqual("1", kwargs["env"]["OPENBLAS_NUM_THREADS"])
                    self.assertIn("--worker", command)
                    report = {"result": "recorded_declared_clearance_evaluated_not_navigation",
                        "configurationSha256": runner.digest(self.config),
                        "offlineOnly": True, "projection": self.value["projection"],
                        "statistics": {"freeCells": 0, "occupiedCells": 0, "unknownCells": 16384},
                        "nativeSha256": self.value["octomap"]["sha256"],
                        "loadedDependenciesSha256": self.value["octomap"]["dependencies"],
                        "runnerSourcesSha256": {name: runner.digest(runner.ROOT / name) for name in runner.SOURCES},
                        "clearanceBytesSha256": hashlib.sha256(bytes([255]) * 16384).hexdigest(),
                        **dict.fromkeys(("physicalScaleValidated", "groundFrameValidated", "navigationReady",
                            "groundSupportValidated", "obstacleCompletenessValidated",
                            "robotContacted", "sensorsSubscribed", "physicalMotionSent"), False)}
                    if mode != "missing-artifact":
                        (output / "private-declared-clearance.bin").write_bytes(bytes([255]) * 16384)
                    if mode == "bad-report":
                        report["navigationReady"] = True
                    if mode == "bad-support":
                        report["groundSupportValidated"] = True
                    if mode == "changed-source":
                        report["runnerSourcesSha256"] = {}
                    if mode == "changed-artifact":
                        (output / "private-declared-clearance.bin").write_bytes(bytes([0]) * 16384)
                    if mode == "bad-size":
                        (output / "private-declared-clearance.bin").write_bytes(b"\xff")
                    if mode == "bad-counts":
                        report["statistics"]["unknownCells"] = 0
                    if mode == "changed-config":
                        self.config.write_text(self.config.read_text() + "\n")
                    if mode != "missing":
                        (output / "report.pending").write_text(json.dumps(report))
                    if mode == "timeout":
                        raise subprocess.TimeoutExpired(command, 300)
                    return subprocess.CompletedProcess(command, 1 if mode == "exit" else 0)
                with patch("refine_saved_map.private_output", return_value=output), \
                        patch.object(runner.subprocess, "run", side_effect=execute):
                    if mode == "accepted":
                        self.assertFalse(runner.run(self.config, output)["navigationReady"])
                        self.assertTrue((output / "report.json").is_file())
                        self.assertFalse((output / "report.pending").exists())
                    else:
                        with self.assertRaises((RuntimeError, ValueError)):
                            runner.run(self.config, output)
                        self.assertFalse((output / "report.json").exists())

    @unittest.skipIf(sys.platform == "win32", "symlink test requires ordinary-user Linux")
    def test_configuration_symlinks_are_not_resolved_away(self):
        link = self.root / "link.json"
        link.symlink_to(self.config)
        with self.assertRaisesRegex(ValueError, "direct_offline_configuration"):
            runner.configuration(link)
        with self.assertRaises(ValueError):
            runner.run(link, self.root / "output")


if __name__ == "__main__":
    unittest.main()
