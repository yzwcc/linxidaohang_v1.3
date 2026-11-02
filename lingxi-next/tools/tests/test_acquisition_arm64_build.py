"""Cross-build provenance tests; synthetic fixtures never execute ARM64 code."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
try:
    import build_glim_acquisition_arm64 as builder
except ImportError:
    builder = None


@unittest.skipIf(builder is None, 'requires isolated cross-build Python dependencies')
class AcquisitionCrossBuild(unittest.TestCase):
    def test_plan_builds_both_artifacts_but_never_runs_target_contracts(self):
        args = argparse.Namespace(output=Path('/new'), cross_work=Path('/cross'))
        plan = dict(builder.commands(args, Path('/source')))
        self.assertEqual({'configure', 'build'}, set(plan))
        self.assertIn('-DBUILD_TESTING=ON', plan['configure'])
        self.assertIn('-DCMAKE_TOOLCHAIN_FILE=/cross/arm64-toolchain.cmake', plan['configure'])
        self.assertFalse(any('ctest' in command or 'acquisition_contract' in command for command in plan.values()))

    def test_abi_inspection_requires_only_the_five_versioned_source_functions(self):
        args = SimpleNamespace(output=Path('/new'), cross_work=Path('/cross'))
        for names in (builder.ABI, builder.ABI[:-1], (*builder.ABI, '_Zunwanted_cpp_symbol'), (*builder.ABI, builder.ABI[0])):
            text = ''.join('0001 T ' + name + '\n' for name in names)
            with self.subTest(names=names), patch.object(builder.subprocess, 'run', return_value=SimpleNamespace(stdout=text)) as run:
                if names == builder.ABI:
                    self.assertEqual(sorted(builder.ABI), builder.exported_abi(args, {}))
                else:
                    with self.assertRaises(ValueError):
                        builder.exported_abi(args, {})
                self.assertEqual('/cross/toolchain/usr/bin/aarch64-linux-gnu-nm', run.call_args.args[0][0])

    def test_ctest_only_lists_the_29_expected_target_commands(self):
        args = SimpleNamespace(output=Path('/new'))
        tests = [dict(name='acquisition_case' + str(i), command=['/new/build/acquisition_contract', 'case' + str(i)])
                 for i in range(29)]
        with patch.object(builder.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(dict(tests=tests)))) as run:
            self.assertEqual([row['name'] for row in tests], builder.configured_contracts(args, {}))
            self.assertIn('--show-only=json-v1', run.call_args.args[0])
        tests[0]['command'] = ['/unexpected/acquisition_contract', 'case0']
        with patch.object(builder.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(dict(tests=tests)))):
            with self.assertRaises(ValueError):
                builder.configured_contracts(args, {})

    def test_wrong_host_and_root_are_rejected_before_input_or_output(self):
        for target, value in (('system', 'Windows'), ('machine', 'aarch64')):
            with patch.object(builder.platform, target, return_value=value), patch.object(builder, 'inputs') as inputs:
                with self.assertRaises(ValueError):
                    builder.build(None)
                inputs.assert_not_called()
        with patch.object(builder.os, 'geteuid', return_value=0, create=True):
            with self.assertRaises(ValueError):
                builder.build(None)

    def fixture(self):
        output = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        (output / 'source').mkdir()
        (output / 'build').mkdir()
        sources = builder.native.source_hashes()
        for name in builder.native.NATIVE_NAMES:
            shutil.copyfile(ROOT / name, output / 'source' / Path(name).name)
        for name in (builder.native.LIBRARY, builder.FIXTURE):
            (output / 'build' / name).write_bytes(b'synthetic-nonexecuted-artifact')
        self.args = SimpleNamespace(output=output, cross_work=Path('/cross'), native_work=Path('/native'))
        self.frozen = dict(sourceSha256=sources, hostPassedContracts=['acquisition_case' + str(i) for i in range(29)])
        def inspect(path, *_):
            return dict(file=path.name, machine='AArch64', **{'class': 'ELF64'}, sha256=builder.sha256(path))
        self.enterContext(patch.object(builder, 'require_host'))
        self.enterContext(patch.object(builder, 'inputs', return_value=self.frozen))
        self.enterContext(patch.object(builder, 'cross_environment', return_value={}))
        self.enterContext(patch.object(builder, 'inspect_elf', side_effect=inspect))
        self.enterContext(patch.object(builder, 'exported_abi', return_value=sorted(builder.ABI)))
        self.enterContext(patch.object(builder, 'configured_contracts', return_value=self.frozen['hostPassedContracts']))
        self.report = dict(schemaVersion=1, result=builder.RESULT, inputs=self.frozen, buildType='Release',
            stages=[dict(name=name, exitCode=0) for name in ('configure', 'build')],
            artifacts=[inspect(output / 'build' / name) for name in (builder.native.LIBRARY, builder.FIXTURE)],
            exportedCFunctions=sorted(builder.ABI), configuredTargetContracts=self.frozen['hostPassedContracts'],
            executedTargetContracts=0, boundaries=dict(builder.BOUNDARIES))

    def save(self, report):
        (self.args.output / 'build-report.json').write_text(json.dumps(report), encoding='utf-8')

    def test_report_cannot_claim_execution_or_reuse_replaced_artifacts(self):
        self.fixture()
        self.save(self.report)
        builder.verify(self.args)
        for key, value in (('executedTargetContracts', 1), ('executedTargetContracts', False),
                           ('configuredTargetContracts', []), ('exportedCFunctions', []), ('buildType', 'Debug')):
            changed = dict(self.report, **{key: value})
            self.save(changed)
            with self.subTest(key=key), self.assertRaises(ValueError):
                builder.verify(self.args)
        for key in builder.BOUNDARIES:
            changed = deepcopy(self.report)
            changed['boundaries'][key] = True
            self.save(changed)
            with self.subTest(boundary=key), self.assertRaises(ValueError):
                builder.verify(self.args)
        self.save(self.report)
        (self.args.output / 'build' / builder.native.LIBRARY).write_bytes(b'replaced-synthetic-artifact')
        with self.assertRaises(ValueError):
            builder.verify(self.args)

    def test_copied_source_changes_cannot_reuse_build_report(self):
        self.fixture()
        self.save(self.report)
        (self.args.output / 'source/acquisition.cpp').write_text('synthetic changed source')
        with self.assertRaises(ValueError):
            builder.verify(self.args)

    def test_actual_committed_build_keeps_current_sources_and_nonexecution_boundaries(self):
        report = json.loads((ROOT / 'docs/evidence/acquisition-arm64-build-20261103.json').read_bytes())
        self.assertEqual(builder.RESULT, report['result'])
        self.assertEqual(builder.native.source_hashes(), report['inputs']['sourceSha256'])
        self.assertEqual(builder.sha256(builder.__file__), report['inputs']['builderSha256'])
        self.assertEqual(sorted(builder.ABI), report['exportedCFunctions'])
        self.assertEqual(29, len(report['configuredTargetContracts']))
        self.assertEqual(report['inputs']['hostPassedContracts'], report['configuredTargetContracts'])
        self.assertEqual(0, report['executedTargetContracts'])
        self.assertTrue(all(value is False for value in report['boundaries'].values()))
        self.assertEqual([builder.native.LIBRARY, builder.FIXTURE], [row['file'] for row in report['artifacts']])
        for row in report['artifacts']:
            self.assertEqual('AArch64', row['machine'])
            self.assertEqual('ELF64', row['class'])


if __name__ == '__main__':
    unittest.main()
