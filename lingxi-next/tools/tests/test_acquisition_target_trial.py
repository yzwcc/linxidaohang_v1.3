"""Target trial contracts use temporary synthetic files and never contact a robot."""
import ast
import copy
import json
from pathlib import Path, PurePosixPath
import shlex
import struct
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import acquisition_target_probe as probe
import run_acquisition_target_trial as runner


class AcquisitionTargetTrial(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / 'trial'
        self.root.mkdir()
        (self.root / 'lib').mkdir()
        (self.root / 'bin').mkdir()
        files = {}
        for name in ('lib/' + probe.LIBRARY, 'lib/libglim.so', 'bin/acquisition_contract',
                     'acquisition_target_probe.py'):
            path = self.root / name
            path.write_bytes(('synthetic-only-' + name).encode('ascii'))
            files[name] = dict(bytes=path.stat().st_size, sha256=probe.sha256(path),
                executable=name == 'bin/acquisition_contract')
        self.manifest = dict(schemaVersion=1, purpose='synthetic_arm64_acquisition_trial',
            platform='linux-aarch64', inputClassification='synthetic_fixture',
            componentManifestSha256='a' * 64, componentBuildReportSha256='b' * 64,
            sourceSha256={'tools/acquisition_target_probe.py': files['acquisition_target_probe.py']['sha256']},
            coreLibrariesSha256={'libglim.so': files['lib/libglim.so']['sha256']},
            syntheticExportSha256='c' * 64, contracts=list(probe.CONTRACTS), files=files,
            boundaries=dict(probe.BOUNDARIES))
        self.write_manifest()

    def write_manifest(self):
        (self.root / probe.MANIFEST).write_text(json.dumps(self.manifest), encoding='utf-8')

    def result(self):
        return dict(result=probe.RESULT, inputClassification='synthetic_fixture',
            target=dict(machine='aarch64'), trialManifestSha256=probe.sha256(self.root / probe.MANIFEST),
            componentManifestSha256=self.manifest['componentManifestSha256'],
            componentBuildReportSha256=self.manifest['componentBuildReportSha256'],
            mappedLibrariesSha256={Path(name).name: value['sha256']
                for name, value in self.manifest['files'].items() if name.startswith('lib/')},
            executedContracts=[dict(name=name, exitCode=0) for name in probe.CONTRACTS],
            syntheticExport=dict(bytes=840, sha256='c' * 64, frameId=1, stamp=1002., returnCount=3,
                trajectorySamples=2), checks=dict(probe.CHECKS), boundaries=dict(probe.BOUNDARIES))

    def test_exact_synthetic_bundle_verifies_without_loading_native_code(self):
        self.assertEqual(self.manifest, probe.verify_bundle(self.root))

    def test_modified_binary_is_rejected_before_upload(self):
        (self.root / 'lib' / probe.LIBRARY).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'integrity_mismatch'):
            probe.verify_bundle(self.root)

    def test_extra_map_or_directory_is_not_a_synthetic_trial(self):
        (self.root / 'saved-map.bin').write_bytes(b'not-allowed')
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            probe.verify_bundle(self.root)
        (self.root / 'saved-map.bin').unlink()
        (self.root / 'config').mkdir()
        with self.assertRaisesRegex(ValueError, 'direct_acquisition_trial_members'):
            probe.verify_bundle(self.root)

    def test_no_contract_can_be_missing_or_duplicated(self):
        self.assertEqual(29, len(set(probe.CONTRACTS)))
        for contracts in (list(probe.CONTRACTS[:-1]), [probe.CONTRACTS[0]] * 29):
            self.manifest['contracts'] = contracts
            self.write_manifest()
            with self.assertRaisesRegex(ValueError, 'complete_synthetic'):
                probe.verify_bundle(self.root)

    def test_real_input_or_navigation_claim_is_rejected(self):
        self.manifest['inputClassification'] = 'live'
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'complete_synthetic'):
            probe.verify_bundle(self.root)
        self.manifest['inputClassification'] = 'synthetic_fixture'
        self.manifest['boundaries']['navigationReady'] = True
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'complete_synthetic'):
            probe.verify_bundle(self.root)

    def test_manifest_member_cannot_escape_inventory(self):
        self.manifest['files']['../outside'] = self.manifest['files'].pop('lib/libglim.so')
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'exact_acquisition_trial_file'):
            probe.verify_bundle(self.root)

    def test_source_path_cannot_escape_repository(self):
        self.manifest['sourceSha256']['../outside'] = 'a' * 64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'pinned_acquisition_trial_sources'):
            probe.verify_bundle(self.root)

    def test_wrong_core_digest_is_rejected_even_with_valid_file_inventory(self):
        self.manifest['coreLibrariesSha256']['libglim.so'] = 'e' * 64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'core_identity_mismatch'):
            probe.verify_bundle(self.root)

    def test_probe_requires_actual_clean_arm64_target_before_native_load(self):
        with patch.object(probe.platform, 'machine', return_value='x86_64'), patch.object(probe.ctypes, 'CDLL') as load:
            with self.assertRaisesRegex(ValueError, 'clean_arm64'):
                probe.probe(self.root)
            load.assert_not_called()

    def test_library_provider_must_be_trial_directory_and_exact_bytes(self):
        text = '\n'.join('000-111 r-xp 0 0 1 ' + str(self.root / name)
            for name in self.manifest['files'] if name.startswith('lib/'))
        found = probe.mapped_libraries(self.root, self.manifest, text)
        self.assertEqual(2, len(found))
        with self.assertRaisesRegex(ValueError, 'outside_isolated'):
            probe.mapped_libraries(self.root, self.manifest,
                text + '\n000-111 r-xp 0 0 1 ' + str(self.home / 'outside/libglim.so.1'))
        with self.assertRaisesRegex(ValueError, 'must_be_observed'):
            probe.mapped_libraries(self.root, self.manifest, '')

    def test_remote_command_is_clean_bounded_and_nonprivileged(self):
        remote = PurePosixPath('/home/synthetic/lingxi-next-validation-arm64-acquisition-a')
        values = shlex.split(runner.command(remote))
        self.assertEqual(['timeout', '--signal=TERM', '--kill-after=5s', '120s', 'env', '-i'], values[:6])
        self.assertIn('LD_LIBRARY_PATH=' + str(remote / 'lib'), values)
        self.assertIn('-I', values)
        for token in ('sudo', 'source', 'bash', 'ros2', 'systemctl'):
            self.assertNotIn(token, values)

    def test_result_requires_all_contracts_exact_loaded_libraries_and_export(self):
        result = self.result()
        identity = result['trialManifestSha256']
        runner.validate_result(result, self.manifest, identity)
        for field, value in (('executedContracts', result['executedContracts'][:-1]),
                             ('mappedLibrariesSha256', {}), ('trialManifestSha256', '0' * 64),
                             ('target', dict(machine='x86_64')), ('syntheticExport', dict(bytes=840, sha256='d' * 64))):
            changed = copy.deepcopy(result)
            changed[field] = value
            with self.assertRaisesRegex(ValueError, 'complete_source_bound'):
                runner.validate_result(changed, self.manifest, identity)

    def test_existing_report_is_preserved_without_connecting(self):
        path = self.home / 'report.json'
        path.write_text('existing')
        with patch.object(runner, 'connect') as connect:
            with self.assertRaisesRegex(ValueError, 'new_private_report'):
                runner.run(NS(bundle=self.root, output=path, user='synthetic'))
            connect.assert_not_called()
        self.assertEqual('existing', path.read_text())

    def test_authentication_or_network_failure_has_private_failure_record(self):
        source = self.home / 'source/tools'
        source.mkdir(parents=True)
        (source / 'acquisition_target_probe.py').write_bytes((self.root / 'acquisition_target_probe.py').read_bytes())
        output = self.home / 'reports/failed.json'
        args = NS(bundle=self.root, output=output, user='synthetic', host='synthetic-host',
            known_identity='synthetic-identity', manifest_sha256=probe.sha256(self.root / probe.MANIFEST))
        with patch.object(runner, 'ROOT', source.parent), patch.object(runner, 'connect', side_effect=OSError('unreachable')):
            with self.assertRaises(OSError):
                runner.run(args)
        report = json.loads(output.read_text())
        self.assertEqual('failed_or_interrupted', report['result'])
        self.assertEqual('OSError', report['errorType'])
        self.assertNotIn('targetProbe', report)
        self.assertNotIn('host', report)

    def test_report_cannot_mutate_frozen_bundle_even_through_parent_alias(self):
        for output in (self.root / 'report.json', self.root / 'reports' / 'report.json',
                       self.home / 'missing' / '..' / 'trial' / 'report.json'):
            with self.subTest(output=output), patch.object(runner, 'connect') as connect:
                with self.assertRaisesRegex(ValueError, 'report_outside_frozen_bundle'):
                    runner.run(NS(bundle=self.root, output=output, user='synthetic'))
                connect.assert_not_called()
                self.assertFalse(output.exists())
                self.assertEqual(self.manifest, probe.verify_bundle(self.root))

    def test_blocked_remote_channel_has_bounded_wait(self):
        channel = Mock()
        channel.recv_ready.return_value = channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = False
        with patch.object(runner.time, 'monotonic', side_effect=[0, 141]):
            with self.assertRaisesRegex(TimeoutError, 'bounded_acquisition_target_process'):
                runner.receive(channel)

    def test_export_decodes_expected_owned_synthetic_metadata(self):
        data = bytearray(840)
        data[:8] = b'LXOBS002'
        struct.pack_into('<Qd', data, 184, 1, 1002.)
        for offset, value in ((624, 2.), (752, 1.), (512, -2.), (384, .2)):
            struct.pack_into('<d', data, offset, value)
        self.assertEqual(3, probe.validate_export(data)['returnCount'])
        data[0] = 0
        with self.assertRaisesRegex(ValueError, 'synthetic_export_mismatch'):
            probe.validate_export(data)

    def test_probe_has_no_sensor_network_or_motion_imports(self):
        text = (ROOT / 'tools/acquisition_target_probe.py').read_text()
        imports = []
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                imports.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)
        for name in ('rclpy', 'socket', 'paramiko', 'requests'):
            self.assertNotIn(name, imports)
        for name in ('cmd_vel', 'McLocomotion', 'create_publisher', 'create_subscription'):
            self.assertNotIn(name, text)


if __name__ == '__main__':
    unittest.main()
