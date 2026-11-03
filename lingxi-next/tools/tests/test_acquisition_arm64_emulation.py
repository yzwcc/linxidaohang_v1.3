"""Emulation runner contracts use synthetic files/mocks, never a robot or QEMU."""
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_acquisition_arm64_emulation as check


class EmulationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.args = NS(bundle=self.root / 'trial', sysroot=self.root / 'sysroot',
            qemu=self.root / 'qemu-aarch64-static', output=self.root / 'output',
            qemu_sha256='a' * 64, manifest_sha256='a' * 64)

    def binary(self, path, machine=183):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = bytearray(20)
        data[:6] = b'\x7fELF\x02\x01'
        data[18:20] = machine.to_bytes(2, 'little')
        path.write_bytes(data)
        return path

    def test_explicit_guest_library_paths_and_binding(self):
        command = check.command(self.args, self.args.bundle / 'bin/acquisition_contract', 'copy')
        self.assertEqual(str(self.args.qemu), command[0])
        self.assertEqual(['-L', str(self.args.sysroot)], command[1:3])
        self.assertIn('LD_BIND_NOW=1', command)
        self.assertIn('LD_LIBRARY_PATH=' + ':'.join(str(path) for path in
            (self.args.bundle / 'lib', self.args.sysroot / 'usr/lib/aarch64-linux-gnu',
             self.args.sysroot / 'lib/aarch64-linux-gnu')), command)
        self.assertEqual('copy', command[-1])

    def test_subprocess_environment_is_clean_and_bounded(self):
        with patch.object(check.subprocess, 'run') as run:
            check.invoke(self.args, 'synthetic-argument')
        options = run.call_args.kwargs
        self.assertEqual({'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, options['env'])
        self.assertEqual(10., options['timeout'])
        self.assertTrue(options['capture_output'])
        self.assertNotIn('shell', options)

    def test_elf_class_byte_order_and_machine_are_checked(self):
        path = self.binary(self.root / 'binary')
        check.elf(path, 183)
        with self.assertRaisesRegex(ValueError, 'architecture'):
            check.elf(path, 62)
        path.write_bytes(b'not-an-elf')
        with self.assertRaisesRegex(ValueError, 'architecture'):
            check.elf(path, 183)

    def test_non_linux_and_root_execution_are_rejected(self):
        for system, machine, uid in (('Windows', 'AMD64', 1000), ('Linux', 'aarch64', 1000),
                                      ('Linux', 'x86_64', 0)):
            with self.subTest(system=system, machine=machine, uid=uid), \
                    patch.object(check.platform, 'system', return_value=system), \
                    patch.object(check.platform, 'machine', return_value=machine), \
                    patch.object(check.os, 'geteuid', return_value=uid, create=True), \
                    self.assertRaisesRegex(ValueError, 'ordinary_user'):
                check.verify_inputs(self.args)

    def test_pinned_inputs_and_new_output_are_required_before_execution(self):
        self.binary(self.args.qemu, 62)
        self.binary(self.args.sysroot / 'lib/aarch64-linux-gnu/ld-linux-aarch64.so.1')
        self.binary(self.args.bundle / 'bin/acquisition_contract')
        manifest = self.args.bundle / check.trial.MANIFEST
        manifest.write_bytes(b'synthetic-manifest')
        self.args.qemu_sha256 = check.trial.sha256(self.args.qemu)
        self.args.manifest_sha256 = check.trial.sha256(manifest)
        with patch.object(check.platform, 'system', return_value='Linux'), \
                patch.object(check.platform, 'machine', return_value='x86_64'), \
                patch.object(check.os, 'geteuid', return_value=1000, create=True), \
                patch.object(check.trial, 'verify_bundle', return_value={'sourceSha256': {}}), \
                patch.object(check, 'invoke') as invoke:
            self.assertEqual({'sourceSha256': {}}, check.verify_inputs(self.args))
            self.args.qemu.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'pinned_emulator'):
                check.verify_inputs(self.args)
            self.binary(self.args.qemu, 62)
            manifest.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'pinned_emulation_trial'):
                check.verify_inputs(self.args)
            manifest.write_bytes(b'synthetic-manifest')
            self.args.output.mkdir()
            with self.assertRaisesRegex(ValueError, 'new_emulation_output'):
                check.verify_inputs(self.args)
            invoke.assert_not_called()

    @unittest.skipUnless(platform.system() == 'Linux', 'POSIX dependency provider paths')
    def test_shared_object_loader_self_listing_is_explicitly_supported(self):
        loader = self.binary(self.args.sysroot / 'lib/ld-linux-aarch64.so.1')
        report = check.providers(str(loader) + ' (0x123)', self.args, {'files': {}})
        self.assertEqual('sysroot', report['ld-linux-aarch64.so.1']['origin'])

    @unittest.skipUnless(platform.system() == 'Linux', 'POSIX dependency provider paths')
    def test_dependency_paths_and_frozen_library_hashes(self):
        library = self.binary(self.args.bundle / 'lib/libglim.so')
        runtime = self.binary(self.args.sysroot / 'lib/libc.so.6')
        manifest = {'files': {'lib/libglim.so': {'sha256': check.trial.sha256(library)}}}
        text = (' linux-vdso.so.1 (0x123)\n libglim.so => ' + str(library) + ' (0x456)\n'
                ' libc.so.6 => ' + str(runtime) + ' (0x789)\n')
        report = check.providers(text, self.args, manifest)
        self.assertEqual('trial', report['libglim.so']['origin'])
        self.assertEqual('lib/libc.so.6', report['libc.so.6']['relative'])
        library.write_bytes(library.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'frozen_emulated'):
            check.providers(text, self.args, manifest)

    @unittest.skipUnless(platform.system() == 'Linux', 'POSIX dependency provider paths')
    def test_unresolved_external_wrong_architecture_and_empty_dependencies_fail(self):
        outside = self.binary(self.root / 'outside/libexample.so')
        internal = self.binary(self.args.sysroot / 'lib/libwrong.so', 62)
        cases = (('', 'nonempty'), ('libmissing.so => not found', 'complete_emulated'),
                 ('libexample.so => ' + str(outside) + ' (0x1)', 'isolated_emulated'),
                 ('libwrong.so => ' + str(internal) + ' (0x2)', 'architecture'))
        for text, error in cases:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, error):
                check.providers(text, self.args, {'files': {}})

    def mocked_run(self, failure=None, export_hash='a' * 64):
        manifest = {'files': {'lib/libglim.so': {}}, 'syntheticExportSha256': 'a' * 64,
                    'componentManifestSha256': 'b' * 64, 'sourceSha256': {}}
        dependencies = {'libglim.so': dict(origin='trial', relative='lib/libglim.so', sha256='a' * 64)}
        clean = subprocess.CompletedProcess([], 0, b'', b'')
        results = [clean, clean] + [clean] * 29 + [clean]
        if failure is not None:
            results[2] = failure
        patches = (patch.object(check, 'verify_inputs', return_value=manifest),
                   patch.object(check, 'elf'),
                   patch.object(check, 'invoke', side_effect=results),
                   patch.object(check, 'providers', return_value=dependencies),
                   patch.object(check.trial, 'sha256', return_value='a' * 64),
                   patch.object(check.trial, 'verify_bundle', return_value=manifest),
                   patch.object(check.trial, 'validate_export', return_value={'sha256': export_hash, 'bytes': 840}))
        from contextlib import ExitStack
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            return check.run(self.args)

    def test_success_records_all_contracts_without_hardware_admission(self):
        report = self.mocked_run()
        self.assertEqual(check.RESULT, report['result'])
        self.assertEqual('qemu-user', report['executionMode'])
        self.assertEqual([{'name': name, 'exitCode': 0} for name in check.trial.CONTRACTS], report['executedContracts'])
        self.assertTrue(all(value is False for value in report['boundaries'].values()))
        self.assertEqual(report, json.loads((self.args.output / 'report.json').read_bytes()))

    def test_nonzero_noisy_and_timeout_contracts_do_not_pass(self):
        for index, result in enumerate((subprocess.CompletedProcess([], 1, b'', b''),
                                      subprocess.CompletedProcess([], 0, b'unexpected', b''),
                                      subprocess.CompletedProcess([], 0, b'', b'unexpected'))):
            self.args.output = self.root / ('failed-' + str(index))
            with self.subTest(index=index), self.assertRaisesRegex(RuntimeError, 'emulated_contract_failed'):
                self.mocked_run(result)
            report = json.loads((self.args.output / 'report.json').read_bytes())
            self.assertEqual('arm64_emulation_failed_not_accepted', report['result'])
            self.assertEqual([], report['executedContracts'])
        self.args.output = self.root / 'timeout'
        with patch.object(check.subprocess, 'run', side_effect=subprocess.TimeoutExpired([], 10)), \
                self.assertRaises(subprocess.TimeoutExpired):
            check.invoke(self.args, 'copy')

    def test_export_mismatch_is_not_a_passing_emulation(self):
        with self.assertRaisesRegex(ValueError, 'same_host_and_emulated'):
            self.mocked_run(export_hash='c' * 64)
        self.assertEqual('arm64_emulation_failed_not_accepted',
                         json.loads((self.args.output / 'report.json').read_bytes())['result'])

    def test_recorded_emulation_is_current_synthetic_and_not_target_acceptance(self):
        path = check.ROOT / 'docs/evidence/acquisition-arm64-emulation-20261104.json'
        report = json.loads(path.read_bytes())
        self.assertEqual(check.RESULT, report['result'])
        self.assertEqual('qemu-user', report['executionMode'])
        self.assertEqual('synthetic_fixture', report['inputClassification'])
        self.assertEqual([{'name': name, 'exitCode': 0} for name in check.trial.CONTRACTS], report['executedContracts'])
        self.assertEqual(check.BOUNDARIES, report['boundaries'])
        self.assertTrue(all(value is False for value in report['boundaries'].values()))
        self.assertEqual(840, report['syntheticExport']['bytes'])
        self.assertEqual('7fbd486080b3d054af6e3eed817bad3c67642e71b2cd527039e885e065133108',
                         report['syntheticExport']['sha256'])
        self.assertEqual(19, len(report['resolvedDependencies']))
        self.assertEqual(7, sum(entry['origin'] == 'trial' for entry in report['resolvedDependencies'].values()))
        for name, identity in {**report['trialSourceSha256'], **report['sourceSha256']}.items():
            self.assertEqual(identity, check.trial.sha256(check.ROOT / name), name)
        for marker in ('/home/', '192.168.', '10.201.', 'password', 'pairingCode', 'credentials'):
            self.assertNotIn(marker, path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
