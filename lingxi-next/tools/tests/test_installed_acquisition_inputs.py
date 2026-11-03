"""Private relocation with synthetic ELF/terrain receipts; no native or robot IO."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).parent)]
AVAILABLE = all(importlib.util.find_spec(name) for name in ('numpy', 'scipy', 'json5'))
if AVAILABLE:
    import prepare_installed_acquisition_inputs as inputs
    import test_acquisition_bundle as acquisition_fixture
    from test_terrain_bundle import TargetFixture


class InstalledAcquisitionFixture:
    def setUp(self):
        self.parent = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='lingxi-installed-acquisition-'))).resolve()
        fixture = acquisition_fixture.AcquisitionBundle()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.component = fixture
        self.mapping = {'root': str(self.parent / 'mapping-inputs'), 'manifestSha256': 'a' * 64}
        self.config = self.parent / 'full-mapper-config'
        self.config.mkdir(mode=0o700)
        (self.config / 'config_odometry_cpu.json').write_text('{odometry_estimation:{save_imu_rate_trajectory:true}}')
        self.platform = SimpleNamespace(arm64=True, mode='live', value={}, config_dir=self.config,
                                       frozen={'coreLibrariesSha256': fixture.core})
        self.enterContext(patch.object(inputs, 'selected_platform', return_value=self.platform))
        self.value = dict(schemaVersion=1, purpose='passive_acquisition_and_declared_terrain_not_navigation',
            acquisition=dict(root=str(fixture.root), manifestSha256=fixture.digest))
        self.settings = self.parent / 'settings.json'
        self.output = self.parent / 'version-1'
        self.write()

    def write(self):
        self.settings.write_text(json.dumps(self.value), encoding='utf-8')
        self.settings.chmod(0o600)
        self.identity = inputs.mapping.sha256(self.settings)

    def prepare(self):
        return inputs.prepare(self.settings, self.identity, self.mapping, self.output)


@unittest.skipUnless(AVAILABLE and sys.platform == 'linux', 'requires Linux numerical Python')
class InstalledAcquisitionInputs(InstalledAcquisitionFixture, unittest.TestCase):
    def test_byte_identical_receipts_and_localization_output_survive_source_removal(self):
        self.value['localizationRosOutput'] = {'mode': 'private_saved_map_observations'}
        self.write()
        before = self.settings.read_bytes()
        identity = self.prepare()
        report = inputs.verify(self.output, identity)
        self.assertEqual(5, len(report['files']))
        self.assertEqual({}, report['symlinks'])
        self.assertEqual(before, self.settings.read_bytes())
        self.assertEqual(before, (self.output / 'original-settings.json').read_bytes())
        for name in ('build-report.json', inputs.acquisition_bundle.MANIFEST):
            self.assertEqual((self.component.root / name).read_bytes(), (self.output / 'acquisition' / name).read_bytes())
        self.component.root.rename(self.parent / 'archived-acquisition')
        inputs.verify(self.output, identity)
        self.assertTrue(all(value is False for value in report['boundaries'].values()))

    def test_wrong_source_pin_fails_before_mapping_validation_or_output(self):
        with patch.object(inputs, 'selected_platform') as platform, self.assertRaises(ValueError):
            inputs.prepare(self.settings, '0' * 64, self.mapping, self.output)
        platform.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_existing_output_and_nonprivate_parent_are_not_overwritten(self):
        self.output.mkdir(mode=0o700)
        marker = self.output / 'preserve'
        marker.write_text('existing user data')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual('existing user data', marker.read_text())
        self.output = self.parent / 'version-2'
        self.parent.chmod(0o755)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_core_config_and_component_changes_are_not_repaired(self):
        odometry = self.config / 'config_odometry_cpu.json'
        original = odometry.read_bytes()
        odometry.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'enabled_acquisition'):
            self.prepare()
        self.assertEqual('{}', odometry.read_text())
        odometry.write_bytes(original)
        self.platform.frozen['coreLibrariesSha256'] = {'libglim.so': '0' * 64}
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_partial_failed_output_is_retained_without_completion(self):
        original = inputs.inspect_inputs
        def inspect(path, selection):
            if path == self.settings:
                return original(path, selection)
            raise ValueError('synthetic relocated check failure')
        with patch.object(inputs, 'inspect_inputs', side_effect=inspect), self.assertRaises(ValueError):
            self.prepare()
        self.assertTrue((self.output / 'acquisition.json').is_file())
        self.assertFalse((self.output / inputs.MANIFEST).exists())

    def test_extra_or_changed_files_and_claimed_acceptance_are_rejected(self):
        identity = self.prepare()
        extra = self.output / 'extra'
        extra.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            inputs.verify(self.output, identity)
        extra.rmdir()
        library = self.output / 'acquisition/lib' / inputs.acquisition_bundle.LIBRARY
        before = library.read_bytes()
        library.write_bytes(before + b'changed')
        with self.assertRaises(ValueError):
            inputs.verify(self.output, identity)
        library.write_bytes(before)
        receipt = self.output / inputs.MANIFEST
        report = inputs.mapping.read_json(receipt)
        report['boundaries']['navigationReady'] = True
        receipt.write_text(json.dumps(report), encoding='utf-8')
        with self.assertRaises(ValueError):
            inputs.verify(self.output, inputs.mapping.sha256(receipt))

    def test_trajectory_and_private_files_are_checked_without_native_or_children(self):
        with patch('ctypes.CDLL', side_effect=AssertionError('native load forbidden')), \
                patch('subprocess.Popen', side_effect=AssertionError('child creation forbidden')):
            identity = self.prepare()
            inputs.verify(self.output, identity)
        for path in self.output.rglob('*'):
            self.assertFalse(path.stat().st_mode & 0o077)


if AVAILABLE:
    @unittest.skipUnless(sys.platform == 'linux', 'private terrain SONAME aliases require Linux')
    class InstalledTerrainInputs(TargetFixture, InstalledAcquisitionFixture, unittest.TestCase):
        def setUp(self):
            super().setUp()
            self.prepare_target()
            self.value['terrain'] = self.target_terrain()
            self.value['localizationRosOutput'] = {'mode': 'private_saved_map_observations'}
            self.value['rosOutput'] = {'mode': 'private_saved_map_terrain'}
            flat = self.value['terrain']['geometry']['declared_from_raw']
            self.platform.value['basePose'] = {'metresPerMapUnit': 1.,
                'navigationFromRaw': [flat[index:index + 4] for index in range(0, 16, 4)]}
            self.write()

        def test_target_receipt_geometry_and_aliases_survive_relocation(self):
            original = deepcopy(self.value)
            receipt = Path(self.selection['report']).read_bytes()
            identity = self.prepare()
            report = inputs.verify(self.output, identity)
            effective = inputs.read_settings(self.output / 'acquisition.json')
            self.assertEqual(original['terrain']['geometry'], effective['terrain']['geometry'])
            self.assertEqual(original['terrain']['projection'], effective['terrain']['projection'])
            self.assertEqual(original['terrain']['ground']['declaration'], effective['terrain']['ground']['declaration'])
            self.assertEqual(receipt, (self.output / 'terrain-target-report.json').read_bytes())
            self.assertEqual(receipt, Path(self.selection['report']).read_bytes())
            self.assertEqual(2, len(report['symlinks']))
            for name, target in report['symlinks'].items():
                self.assertEqual(target, os.readlink(self.output / name))
            self.assertTrue(all(value is False for value in report['boundaries'].values()))
            self.trial.root.rename(self.parent / 'archived-terrain')
            Path(self.selection['report']).unlink()
            inputs.verify(self.output, identity)

        def test_missing_target_receipt_or_host_report_is_not_a_fallback(self):
            self.value['terrain'].pop('targetTrial')
            self.write()
            with self.assertRaisesRegex(ValueError, 'existing_target_trial_receipt'):
                self.prepare()
            self.assertFalse(self.output.exists())

        def test_rehashed_geometry_reselection_is_not_only_a_path_change(self):
            identity = self.prepare()
            effective = self.output / 'acquisition.json'
            value = inputs.read_settings(effective)
            value['terrain']['ground']['declaration']['sensor_height_metres'] = .7
            effective.write_text(json.dumps(value), encoding='utf-8')
            receipt = self.output / inputs.MANIFEST
            report = inputs.mapping.read_json(receipt)
            report['settingsSha256'] = inputs.mapping.sha256(effective)
            report['files']['acquisition.json'] = inputs.mapping.fingerprint(effective)
            receipt.write_text(json.dumps(report), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'only_explicit_installed_acquisition_paths'):
                inputs.verify(self.output, inputs.mapping.sha256(receipt))

        def test_foreign_or_absolute_alias_is_not_adopted(self):
            identity = self.prepare()
            alias = self.output / next(iter(inputs.verify(self.output, identity)['symlinks']))
            original = os.readlink(alias)
            alias.unlink()
            alias.symlink_to(alias.parent / original)
            with self.assertRaisesRegex(ValueError, 'relative_terrain_aliases'):
                inputs.verify(self.output, identity)


@unittest.skipUnless(AVAILABLE, 'requires numerical Python for the file-only settings adapter')
class MappingPlatformSelection(unittest.TestCase):
    def test_prepared_mapping_pin_and_full_mapper_config_are_not_replaced_by_legacy_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            core = root / 'core'
            core.mkdir()
            value = {'inputMode': 'live', 'coreRoot': str(core), 'root': str(root / 'legacy'),
                     'fullMapping': {'root': str(root / 'full')}}
            (root / 'mapping.json').write_text(json.dumps(value), encoding='utf-8')
            files = {'lib/libgtsam.so.4.3a0': {'sha256': 'a' * 64},
                     'lib/libglim.so': {'sha256': 'b' * 64}, 'config/profile.json': {'sha256': 'c' * 64}}
            (core / 'bundle-manifest.json').write_text(json.dumps({'files': files}), encoding='utf-8')
            with patch.object(inputs.mapping, 'verify') as verify:
                platform = inputs.selected_platform({'root': str(root), 'manifestSha256': 'd' * 64})
            verify.assert_called_once_with(root, 'd' * 64)
            self.assertEqual(root / 'full/config', platform.config_dir)
            self.assertEqual({'libgtsam.so.4.3a0': 'a' * 64, 'libglim.so': 'b' * 64},
                             platform.frozen['coreLibrariesSha256'])


@unittest.skipUnless(AVAILABLE and os.environ.get('LINGXI_INSTALLED_ACQUISITION_INPUTS_ROOT')
                     and os.environ.get('LINGXI_INSTALLED_ACQUISITION_INPUTS_SHA256'),
                     'requires explicitly selected private prepared acquisition inputs')
class RealInstalledAcquisitionInputs(unittest.TestCase):
    def test_real_file_closure_without_mapping_loads_or_children(self):
        with patch('ctypes.CDLL', side_effect=AssertionError('native load forbidden')), \
                patch('subprocess.Popen', side_effect=AssertionError('child creation forbidden')):
            report = inputs.verify(Path(os.environ['LINGXI_INSTALLED_ACQUISITION_INPUTS_ROOT']),
                                   os.environ['LINGXI_INSTALLED_ACQUISITION_INPUTS_SHA256'])
        self.assertTrue(all(value is False for value in report['boundaries'].values()))


if __name__ == '__main__':
    unittest.main()
