"""Explicit synthetic module-selection fixtures; never load or execute an ELF."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'robot-runtime/python'))
import acquisition_bundle as bundle


class AcquisitionBundle(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (self.root / 'lib').mkdir()
        self.library = self.root / 'lib' / bundle.LIBRARY
        self.library.write_bytes(b'\x7fELF\x02\x01' + b'\0' * 12 + (183).to_bytes(2, 'little'))
        self.core = {'libglim.so': 'a' * 64, 'libgtsam.so.4': 'b' * 64}
        self.sources = bundle.source_hashes()
        contracts = ['acquisition_case' + str(i) for i in range(29)]
        self.report = dict(schemaVersion=1, result=bundle.RESULT, buildType='Release',
            inputs=dict(sourceSha256={name: self.sources[name] for name in bundle.BUILD_SOURCES},
                builderSha256=self.sources['tools/build_glim_acquisition_arm64.py'],
                crossCore=dict(referenceCoreLibrariesSha256=self.core), hostPassedContracts=contracts),
            stages=[dict(name=name, exitCode=0) for name in ('configure', 'build')],
            artifacts=[dict(file=name, machine='AArch64', **{'class': 'ELF64'}, sha256=bundle.sha256(self.library))
                       for name in (bundle.LIBRARY, 'acquisition_contract')],
            configuredTargetContracts=contracts, executedTargetContracts=0,
            exportedCFunctions=['lingxi_acquisition_' + name for name in ('close', 'counts', 'error', 'open', 'take')],
            boundaries=dict(bundle.BOUNDARIES))
        self.write('build-report.json', self.report)
        self.value = dict(schemaVersion=1, engine=bundle.ENGINE, platform='linux-aarch64',
            sourceSha256=self.sources, coreLibrariesSha256=self.core, boundaries=dict(bundle.BOUNDARIES))
        self.refresh()

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def refresh(self):
        self.value['files'] = bundle.inventory(self.root)
        self.write(bundle.MANIFEST, self.value)
        self.digest = bundle.sha256(self.root / bundle.MANIFEST)

    def verify(self, core=None):
        return bundle.verify_bundle(self.root, self.digest, core_libraries=self.core if core is None else core)

    def test_explicit_complete_module_is_checked_without_a_native_loader(self):
        self.assertEqual(bundle.ENGINE, self.verify()['engine'])
        self.assertEqual(bundle.ENGINE, self.verify({'libglim.so': 'a' * 64, 'libgtsam.so': 'b' * 64})['engine'])
        self.assertFalse(self.verify()['boundaries']['navigationReady'])

    def test_wrong_core_missing_glim_and_conflicting_aliases_are_not_accepted(self):
        for core in ({}, {'libglim.so': 'c' * 64, 'libgtsam.so': 'b' * 64}, {'libgtsam.so': 'b' * 64},
                     {**self.core, 'libgtsam.so': 'c' * 64}):
            with self.subTest(core=core), self.assertRaises(ValueError):
                self.verify(core)

    def test_module_report_or_manifest_replacement_is_not_adopted(self):
        for path in (self.library, self.root / 'build-report.json', self.root / bundle.MANIFEST):
            before = path.read_bytes()
            path.write_bytes(before + b' ')
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                self.verify()
            path.write_bytes(before)
        self.verify()

    def test_rehashed_wrong_architecture_and_extra_members_are_not_accepted(self):
        original = self.library.read_bytes()
        self.library.write_bytes(original[:18] + (62).to_bytes(2, 'little'))
        self.report['artifacts'][0]['sha256'] = bundle.sha256(self.library)
        self.write('build-report.json', self.report)
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'arm64_acquisition_elf'):
            self.verify()
        (self.root / 'unexpected.txt').write_text('synthetic')
        with self.assertRaises(ValueError):
            bundle.inventory(self.root)

    def test_changed_source_and_self_claimed_target_execution_are_rejected(self):
        original = deepcopy(self.report)
        changes = (('executedTargetContracts', 1), ('executedTargetContracts', False),
                   ('configuredTargetContracts', []), ('exportedCFunctions', []))
        for key, value in changes:
            self.report = dict(original, **{key: value})
            self.write('build-report.json', self.report)
            self.refresh()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify()
        self.report = deepcopy(original)
        self.report['inputs']['sourceSha256'][bundle.NATIVE_SOURCES[0]] = 'd' * 64
        self.write('build-report.json', self.report)
        self.refresh()
        with self.assertRaises(ValueError):
            self.verify()

    def test_every_acceptance_boundary_stays_explicitly_false(self):
        for key in bundle.BOUNDARIES:
            self.value['boundaries'][key] = True
            self.refresh()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify()
            self.value['boundaries'][key] = False
        self.refresh()
        self.verify()

    def test_missing_library_is_not_a_partial_bundle(self):
        self.library.unlink()
        with self.assertRaises(ValueError):
            bundle.inventory(self.root)


if __name__ == '__main__':
    unittest.main()
