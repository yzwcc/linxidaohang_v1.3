"""Binding refresh preserves native provenance and never grants map acceptance."""
from pathlib import Path
import sys
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1])]
import test_async_mapping_bundle as fixtures
import refresh_installed_mapping_inputs as refresh
from test_async_mapping_bundle import bundle


class RefreshMappingInputContracts(unittest.TestCase):
    def setUp(self):
        self.fixture = f = fixtures.FullMappingBundleContracts()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.allowed = ['robot-runtime/python/mapping_platform.py']
        self.core = f.parent / 'core'
        (self.core / 'lib').mkdir(parents=True)
        f.write(self.core / 'bundle-manifest.json', {'synthetic': True})
        for name in bundle.CORE_LIBRARIES:
            (self.core / 'lib' / name).write_bytes(('synthetic core ' + name).encode())
        f.manifest['coreManifestSha256'] = bundle.sha256(self.core / 'bundle-manifest.json')
        f.manifest['coreLibrariesSha256'] = {name: bundle.sha256(self.core / 'lib' / name)
                                            for name in bundle.CORE_LIBRARIES}
        f.write(f.baseline / 'config/config_odometry_cpu.json', {'odometry_estimation': {}})
        f.write(f.root / 'config/config_odometry_cpu.json', {'odometry_estimation': {'save_imu_rate_trajectory': True}})
        before, after = bundle.verify_configuration(f.root, f.baseline, observed=True)
        f.manifest.update(engine=bundle.OBSERVED_ENGINE, sourceSha256=bundle.source_hashes(observed=True),
                          baselineConfigurationSha256=before, configurationSha256=after)
        acquisition = {name.removeprefix('tools/glim-observations/'): f.manifest['sourceSha256'][name]
                       for name in bundle.OBSERVATION_SOURCES if name.startswith('tools/glim-observations/')}
        f.report = {'result': 'arm64_observed_mapper_and_fixture_linked_not_target_executed',
            'artifacts': [{'file': bundle.LIBRARY, 'sha256': bundle.sha256(f.library)}],
            'privateEofSymbolsVerified': True,
            'inputs': {'observations': {'observations': acquisition, 'mapping': f.report['inputs']['mapper']}}}
        f.write(f.root / 'build-report.json', f.report)
        # Represents the explicitly verified previous Python version.
        f.manifest['sourceSha256'][self.allowed[0]] = 'f' * 64
        f.refresh()
        self.output = f.parent / 'refreshed'

    def run_refresh(self, **options):
        f = self.fixture
        return refresh.rebind_full(bundle, f.root, f.digest, self.output,
            baseline=f.baseline, core=self.core, allowed=options.get('allowed', self.allowed))

    def test_python_refresh_keeps_binary_configuration_build_report_and_acceptance(self):
        f = self.fixture
        original = (f.root / bundle.MANIFEST).read_bytes()
        identity, changed = self.run_refresh()
        after = bundle.read_json(self.output / bundle.MANIFEST)
        self.assertEqual(changed, self.allowed)
        self.assertEqual(after['files'], f.manifest['files'])
        self.assertEqual(after['sourceSha256'], bundle.source_hashes(observed=True))
        self.assertNotEqual(identity, f.digest)
        self.assertEqual(original, (f.root / bundle.MANIFEST).read_bytes())
        for key in ('targetExecutionValidated', 'geometryValidated', 'navigationValidated'):
            self.assertIs(after[key], False)

    def test_native_source_change_is_rejected_even_if_explicitly_requested(self):
        f = self.fixture
        native = bundle.SOURCES[0]
        f.manifest['sourceSha256'][native] = '0' * 64
        f.refresh()
        with self.assertRaisesRegex(ValueError, 'reviewed_python'):
            self.run_refresh(allowed=[*self.allowed, native])
        self.assertFalse(self.output.exists())

    def test_unknown_or_incomplete_review_list_is_rejected(self):
        for allowed in ([], ['unknown.py'], [*self.allowed, 'robot-slam/lingxi_slam/mapping_session.py']):
            with self.subTest(allowed=allowed), self.assertRaisesRegex(ValueError, 'reviewed_python'):
                self.run_refresh(allowed=allowed)

    def test_changed_native_binary_without_manifest_is_rejected(self):
        self.fixture.library.write_bytes(b'changed binary')
        with self.assertRaisesRegex(ValueError, 'previous_native_files'):
            self.run_refresh()

    def test_rehashed_build_provenance_tampering_is_rejected(self):
        f = self.fixture
        f.report['inputs']['observations']['mapping']['nativeSources']['full_mapping.cpp'] = '0' * 64
        f.write(f.root / 'build-report.json', f.report)
        f.refresh()
        with self.assertRaisesRegex(ValueError, 'native_sources'):
            self.run_refresh()

    def test_rehashed_physical_acceptance_claim_is_rejected(self):
        f = self.fixture
        f.manifest['navigationValidated'] = True
        f.refresh()
        with self.assertRaisesRegex(ValueError, 'source_bound'):
            self.run_refresh()

    def test_existing_output_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / 'keep'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'new_full_binding_output'):
            self.run_refresh()
        self.assertEqual(sentinel.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
