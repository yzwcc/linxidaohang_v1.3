"""Synthetic cross-build contracts; these tests never load ARM64 native code."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_terrain_arm64 as terrain


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())


class TerrainArm64BuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.project = self.root / 'project'
        self.args = SimpleNamespace(**{name: self.root / name for name in
            ('cross_work', 'native_work', 'ground_host', 'octomap_host', 'octomap_arm64', 'host_check', 'output')})
        self.enterContext(patch.object(terrain, 'ROOT', self.project))
        self.enterContext(patch.object(terrain, 'require_host'))
        self.enterContext(patch.object(terrain, 'private_output', side_effect=lambda path: path))
        self.enterContext(patch.object(terrain, 'cross_environment', return_value={}))
        self.enterContext(patch.object(terrain, 'cross_inputs', side_effect=lambda args: dict(
            nativePointSourceSha256={}, builderSourceSha256='synthetic', crossCore='synthetic')))
        for name in terrain.SOURCES:
            put(self.project / name, b'synthetic source, not executable')
        self.required = ('robot-navigation/lingxi_navigation/ground.py',
            'robot-navigation/native-ground/ground_bridge.cpp', 'robot-navigation/patchworkpp.lock.json',
            'tools/check_ground_native.py', 'tools/build_ground_offline.py', 'tools/tests/test_ground.py',
            'robot-navigation/lingxi_navigation/occupancy.py')
        for name in self.required:
            if not (self.project / name).exists():
                put(self.project / name, b'synthetic tested source')
        ground = self.args.ground_host
        put(ground / terrain.LIBRARIES[0], b'synthetic host ground, never loaded')
        put(ground / 'ground_bridge.cpp', (self.project / 'robot-navigation/native-ground/ground_bridge.cpp').read_bytes())
        put(ground / 'eigen/Eigen/Core', b'synthetic Eigen header')
        put(ground / 'upstream/LICENSE', b'synthetic license fixture')
        self.ground = dict(nativeSha256=terrain.digest(ground / terrain.LIBRARIES[0]),
            adapterSha256=terrain.digest(ground / 'ground_bridge.cpp'),
            eigenHeadersSha256={'Eigen/Core': terrain.digest(ground / 'eigen/Eigen/Core')},
            upstreamFilesSha256={'LICENSE': terrain.digest(ground / 'upstream/LICENSE')})
        put(ground / 'build-report.json', self.ground)
        self.enterContext(patch.object(terrain, 'verified_build', return_value=self.ground))
        octomap = self.args.octomap_host
        put(octomap / terrain.LIBRARIES[1], b'synthetic host OctoMap, never loaded')
        put(octomap / 'include/octomap/octomap.h', b'synthetic OctoMap header')
        self.octomap = dict(schemaVersion=1, octomapVersion='1.9.7',
            purpose='offline_native_build_not_target_acceptance',
            sourceSha256=terrain.digest(self.project / 'robot-navigation/native-octomap/octomap_bridge.cpp'),
            nativeSha256=terrain.digest(octomap / terrain.LIBRARIES[1]),
            headersSha256={'octomap.h': terrain.digest(octomap / 'include/octomap/octomap.h')},
            freshProcessLoadPassed=True, robotContacted=False, navigationReady=False,
            dependencies={'synthetic-host-dependency': '0' * 64})
        put(octomap / 'build-report.json', self.octomap)
        arm = b'\x7fELF\x02\x01' + bytes(12) + (183).to_bytes(2, 'little') + b'synthetic, never loaded'
        dependencies = {}
        for name in ('liboctomap.so.1.9.7', 'liboctomath.so.1.9.7'):
            path = self.args.octomap_arm64 / 'usr/lib/aarch64-linux-gnu' / name
            put(path, arm)
            dependencies[name] = terrain.digest(path)
        put(self.args.octomap_arm64 / 'usr/share/doc/liboctomap1.9/copyright', b'synthetic distribution license')
        put(self.project / 'robot-navigation/octomap.lock.json', dict(version='1.9.7', dependencies=dependencies))
        put(self.project / 'robot-navigation/patchworkpp.lock.json', dict(revision='1' * 40))
        self.check = dict(result='native_synthetic_ground_fusion_passed', machine='x86_64', testsRun=21,
            failures=0, errors=0, skipped=0, nativeSha256=self.ground['nativeSha256'],
            octomapSha256=self.octomap['nativeSha256'],
            groundBuildReportSha256=terrain.digest(ground / 'build-report.json'),
            octomapDependenciesSha256=self.octomap['dependencies'],
            compiledAdapterSha256=self.ground['adapterSha256'],
            inputClassification='isolated_synthetic_ground_and_rays',
            sourcesSha256={name: terrain.digest(self.project / name) for name in self.required},
            **dict.fromkeys(('robotContacted', 'sensorsSubscribed', 'physicalMotionSent',
                'physicalScaleValidated', 'groundSupportValidated', 'navigationReady'), False))
        put(self.args.host_check, self.check)

    def test_pins_tested_sources_headers_and_arm64_dependencies_without_target_claim(self):
        frozen = terrain.inputs(self.args)
        self.assertEqual(21, frozen['hostContracts'])
        self.assertEqual(set(terrain.SOURCES), set(frozen['sourceSha256']))
        self.assertNotIn('robot-navigation/native-ground/CMakeLists.txt', frozen['sourceSha256'])
        self.assertTrue(all(value is False for value in terrain.BOUNDARIES.values()))

    def test_stale_host_contracts_missing_boundaries_and_source_changes_reject(self):
        for key, change in (('testsRun', 20), ('skipped', 1), ('nativeSha256', '0' * 64),
                ('navigationReady', True), ('compiledAdapterSha256', '0' * 64),
                ('inputClassification', 'recorded-robot-data')):
            put(self.args.host_check, dict(self.check, **{key: change}))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'host_terrain_contracts'):
                terrain.inputs(self.args)
        changed = dict(self.check)
        del changed['sensorsSubscribed']
        put(self.args.host_check, changed)
        with self.assertRaisesRegex(ValueError, 'host_terrain_contracts'):
            terrain.inputs(self.args)
        put(self.args.host_check, self.check)
        put(self.project / self.required[-1], b'changed tested source')
        with self.assertRaisesRegex(ValueError, 'contract_sources'):
            terrain.inputs(self.args)

    def test_changed_target_dependency_and_unpinned_headers_reject(self):
        path = self.args.octomap_arm64 / 'usr/lib/aarch64-linux-gnu/liboctomap.so.1.9.7'
        put(path, b'not the selected ARM64 dependency')
        with self.assertRaisesRegex(ValueError, 'pinned_arm64_octomap'):
            terrain.inputs(self.args)
        with self.assertRaisesRegex(ValueError, 'direct_pinned_terrain_header'):
            terrain.checked_tree(self.args.ground_host / 'eigen', {'../escape': '0' * 64})
        put(self.args.ground_host / 'eigen/Eigen/unpinned', b'unpinned header')
        with self.assertRaisesRegex(ValueError, 'complete_pinned_terrain_header_tree'):
            terrain.checked_tree(self.args.ground_host / 'eigen', self.ground['eigenHeadersSha256'])

    def test_compiled_copy_rejects_changes(self):
        frozen = terrain.inputs(self.args)
        self.args.output.mkdir()
        terrain.copy_inputs(self.args, frozen)
        put(self.args.output / 'source/ground_bridge.cpp', b'changed compiled adapter')
        with self.assertRaisesRegex(ValueError, 'compiled_input_copies'):
            terrain.snapshots(self.args.output / 'source', frozen)

    def fake_inspect(self, args, environment):
        return ([dict(file=name, sha256=terrain.digest(args.output / 'build' / name))
            for name in terrain.LIBRARIES], terrain.ABI)

    def fake_run(self, command, **kwargs):
        if '--build' in command:
            for name in terrain.LIBRARIES:
                put(self.args.output / 'build' / name, b'synthetic output, never loaded')
        return SimpleNamespace(returncode=0)

    def test_abi_inspection_rejects_unknown_exports_and_dependencies(self):
        def symbols(command, **kwargs):
            names = terrain.ABI[Path(command[-1]).name]
            return SimpleNamespace(stdout='\n'.join('0000 T ' + name for name in names))
        def dynamic(command, **kwargs):
            result = '(NEEDED) Shared library: [libc.so.6]\n'
            if Path(command[-1]).name == terrain.LIBRARIES[1]:
                result += ('(NEEDED) Shared library: [liboctomap.so.1.9]\n'
                    '(NEEDED) Shared library: [liboctomath.so.1.9]\n'
                    '(RUNPATH) Library runpath: [$ORIGIN/lib]\n')
            return result
        with patch.object(terrain, 'inspect_elf', side_effect=lambda *args: {}), \
                patch.object(terrain.subprocess, 'run', side_effect=symbols), \
                patch.object(terrain.subprocess, 'check_output', side_effect=dynamic) as readelf:
            _, exports = terrain.inspect(self.args, {})
            self.assertEqual(terrain.ABI, exports)
            readelf.side_effect = lambda *args, **kwargs: '(NEEDED) Shared library: [libunconfigured.so]'
            with self.assertRaisesRegex(ValueError, 'only_declared_terrain_runtime_dependencies'):
                terrain.inspect(self.args, {})
        with patch.object(terrain, 'inspect_elf', return_value={}), \
                patch.object(terrain.subprocess, 'run', return_value=SimpleNamespace(stdout='0000 T unknown')):
            with self.assertRaisesRegex(ValueError, 'exact_four_function_terrain_abi'):
                terrain.inspect(self.args, {})

    @unittest.skipUnless(sys.platform == 'linux', 'relative SONAME symlinks need Linux build semantics')
    def test_build_and_verify_prepare_libraries_aliases_and_notices_without_target_execution(self):
        with patch.object(terrain.subprocess, 'run', side_effect=self.fake_run), \
                patch.object(terrain, 'inspect', side_effect=self.fake_inspect):
            report = terrain.build(self.args)
            self.assertEqual(terrain.RESULT, report['result'])
            self.assertEqual(terrain.BOUNDARIES, report['boundaries'])
            self.assertEqual(report, terrain.verify(self.args))
            elevated = dict(report, boundaries=dict(report['boundaries'], targetExecutionValidated=True))
            put(self.args.output / 'build-report.json', elevated)
            with self.assertRaisesRegex(ValueError, 'complete_nonexecuted_terrain_arm64_build'):
                terrain.verify(self.args)
            put(self.args.output / 'build-report.json', report)
            (self.args.output / 'build/lib/liboctomap.so.1.9').unlink()
            with self.assertRaisesRegex(ValueError, 'soname_alias'):
                terrain.verify(self.args)

    def test_build_failure_is_preserved_without_success_report(self):
        with patch.object(terrain.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'terrain_arm64_configure_failed'):
                terrain.build(self.args)
        report = terrain.load(self.args.output / 'build-report.json')
        self.assertEqual('failed_or_interrupted', report['result'])
        self.assertEqual(terrain.BOUNDARIES, report['boundaries'])

    def test_output_inside_selected_input_is_rejected_before_copy(self):
        self.args.output = self.args.ground_host / 'nested'
        with self.assertRaisesRegex(ValueError, 'output_outside_inputs'):
            terrain.build(self.args)


if __name__ == '__main__':
    unittest.main()
