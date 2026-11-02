"""Cross-build the tested per-return adapter; never run target code or robot IO."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

from build_glim_point_arm64 import inputs as cross_inputs
from build_slam_arm64_trial import cross_environment, inspect_elf
import run_glim_acquisition_trial as native
from verify_slam_capture import sha256

RESULT = 'arm64_acquisition_adapter_linked_not_target_executed'
FIXTURE = 'acquisition_contract'
ABI = tuple('lingxi_acquisition_' + name for name in ('close', 'counts', 'error', 'open', 'take'))
BOUNDARIES = dict.fromkeys(('robotContacted', 'targetExecutionValidated', 'liveTerrainIntegrated',
    'physicalScaleValidated', 'groundSupportValidated', 'navigationReady', 'deploymentApproved'), False)


def require_host():
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() == 0:
        raise ValueError('ordinary_user_linux_acquisition_cross_build_required')


def inputs(args):
    core = cross_inputs(args)
    core.pop('nativePointSourceSha256')
    core.pop('builderSourceSha256')
    report_path = args.native_acquisition / 'build-report.json'
    report = native.load(report_path)
    library = args.native_acquisition / 'build' / native.LIBRARY
    if (report.get('result') != native.RESULT or report.get('sourceCommits') != native.PINS
            or report.get('sourceSha256') != native.source_hashes()
            or report.get('coreBuildReportSha256') != core['nativeToolsBuildSha256']
            or type(report.get('nativeContracts')) is not int or report['nativeContracts'] != 29
            or report.get('stages') != [dict(name=name, exitCode=0) for name in ('configure', 'build', 'contracts')]
            or report.get('librarySha256') != sha256(library)
            or any(report.get(key) is not False for key in ('robotContacted', 'targetArm64Validated',
                'liveTerrainIntegrated', 'physicalScaleValidated', 'navigationReady'))):
        raise ValueError('current_tested_host_acquisition_build_required')
    contracts = report.get('executedContracts')
    if (type(contracts) is not list or len(contracts) != 29 or len(set(contracts)) != 29
            or any(type(name) is not str or not name.startswith('acquisition_') for name in contracts)):
        raise ValueError('complete_host_acquisition_contract_inventory_required')
    return dict(crossCore=core, nativeBuildReportSha256=sha256(report_path),
        nativeLibrarySha256=sha256(library), sourceSha256=native.source_hashes(),
        builderSha256=sha256(__file__), hostPassedContracts=contracts)


def commands(args, source):
    return (
        ('configure', ['cmake', '-S', str(source), '-B', str(args.output / 'build'), '-G', 'Ninja',
            '-DCMAKE_TOOLCHAIN_FILE=' + str(args.cross_work / 'arm64-toolchain.cmake'),
            '-DCMAKE_PREFIX_PATH=' + str(args.cross_work / 'prefix') + ';' + str(args.cross_work / 'sysroot/usr'),
            '-DCMAKE_BUILD_TYPE=Release', '-DBUILD_TESTING=ON']),
        ('build', ['cmake', '--build', str(args.output / 'build'), '--parallel', '2']),
    )


def exported_abi(args, environment):
    result = subprocess.run([str(args.cross_work / 'toolchain/usr/bin/aarch64-linux-gnu-nm'),
        '-D', '--defined-only', str(args.output / 'build' / native.LIBRARY)],
        check=True, capture_output=True, text=True, env=environment, timeout=30.)
    names = [line.split()[-1] for line in result.stdout.splitlines() if line.split()]
    if len(names) != len(ABI) or set(names) != set(ABI):
        raise ValueError('exact_five_function_arm64_acquisition_abi_required')
    return sorted(names)


def configured_contracts(args, environment):
    # CTest only describes target executables here; it must never execute them.
    result = subprocess.run(['ctest', '--test-dir', str(args.output / 'build'), '--show-only=json-v1'],
        check=True, capture_output=True, text=True, env=environment, timeout=30.)
    tests = json.loads(result.stdout)['tests']
    executable = str(args.output / 'build' / FIXTURE)
    if (len(tests) != 29 or len({row['name'] for row in tests}) != 29
            or any(row.get('command') != [executable, row['name'].removeprefix('acquisition_')]
                for row in tests)):
        raise ValueError('complete_nonexecuted_acquisition_target_contracts_required')
    return [row['name'] for row in tests]


def source_snapshot(directory):
    return {name: sha256(directory / Path(name).name) for name in native.NATIVE_NAMES}


def build(args):
    require_host()
    output = native.private_output(args.output)
    for path in (args.cross_work, args.native_work, args.native_acquisition):
        if output == path or path in output.parents or output in path.parents:
            raise ValueError('new_acquisition_cross_output_outside_inputs_required')
    frozen = inputs(args)
    output.mkdir(mode=0o700)
    source = output / 'source'
    source.mkdir(mode=0o700)
    for name in native.NATIVE_NAMES:
        shutil.copyfile(native.ROOT / name, source / Path(name).name)
    expected = {name: frozen['sourceSha256'][name] for name in native.NATIVE_NAMES}
    if source_snapshot(source) != expected:
        raise ValueError('identical_tested_acquisition_native_sources_required')
    report = dict(schemaVersion=1, result='running', inputs=frozen, buildType='Release', stages=[],
        artifacts=[], exportedCFunctions=[], configuredTargetContracts=[], executedTargetContracts=0,
        boundaries=dict(BOUNDARIES))
    environment = cross_environment(args.cross_work, args.native_work)
    try:
        for name, command in commands(args, source):
            with (output / (name + '.private.log')).open('x') as log:
                result = subprocess.run(command, env=environment, stdout=log, stderr=log, timeout=180.)
            report['stages'].append(dict(name=name, exitCode=result.returncode))
            if result.returncode:
                raise RuntimeError('acquisition_arm64_' + name + '_failed_see_private_log')
        readelf = args.cross_work / 'toolchain/usr/bin/aarch64-linux-gnu-readelf'
        report['artifacts'] = [inspect_elf(output / 'build' / name, readelf, environment)
                               for name in (native.LIBRARY, FIXTURE)]
        report['exportedCFunctions'] = exported_abi(args, environment)
        report['configuredTargetContracts'] = configured_contracts(args, environment)
        if (inputs(args) != frozen or source_snapshot(source) != expected
                or report['configuredTargetContracts'] != frozen['hostPassedContracts']):
            raise ValueError('unchanged_source_bound_acquisition_cross_inputs_required')
        report['result'] = RESULT
    except BaseException:
        report['result'] = 'failed_or_interrupted'
        raise
    finally:
        native.private_json(output / 'build-report.json', report)
    return report


def verify(args):
    require_host()
    report = native.load(args.output / 'build-report.json')
    frozen = inputs(args)
    if (report.get('schemaVersion') != 1 or report.get('result') != RESULT
            or report.get('inputs') != frozen or report.get('buildType') != 'Release'
            or report.get('stages') != [dict(name=name, exitCode=0) for name in ('configure', 'build')]
            or report.get('exportedCFunctions') != sorted(ABI)
            or report.get('configuredTargetContracts') != frozen['hostPassedContracts']
            or type(report.get('executedTargetContracts')) is not int or report['executedTargetContracts'] != 0
            or report.get('boundaries') != BOUNDARIES
            or source_snapshot(args.output / 'source') != {
                name: frozen['sourceSha256'][name] for name in native.NATIVE_NAMES}):
        raise ValueError('complete_nonexecuted_acquisition_arm64_build_required')
    environment = cross_environment(args.cross_work, args.native_work)
    readelf = args.cross_work / 'toolchain/usr/bin/aarch64-linux-gnu-readelf'
    artifacts = [inspect_elf(args.output / 'build' / name, readelf, environment)
                 for name in (native.LIBRARY, FIXTURE)]
    if (report['artifacts'] != artifacts or exported_abi(args, environment) != report['exportedCFunctions']
            or configured_contracts(args, environment) != report['configuredTargetContracts']):
        raise ValueError('unchanged_acquisition_arm64_artifacts_and_contracts_required')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'verify'))
    for name in ('cross-work', 'native-work', 'native-acquisition', 'output'):
        parser.add_argument('--' + name, type=lambda value: Path(value).absolute(), required=True)
    parser.add_argument('--export', type=Path)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        report = {'build': build, 'verify': verify}[args.command](args)
        if args.export is not None:
            public = verify(args)
            public = dict(public, privateBuildReportSha256=sha256(args.output / 'build-report.json'))
            native.private_json(args.export, public)
        print(json.dumps(dict(result=report['result'], artifacts=report['artifacts'])))
    finally:
        os.umask(previous)


if __name__ == '__main__':
    main()
