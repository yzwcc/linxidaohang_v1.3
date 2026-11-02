"""Cross-build the tested ground/clearance adapters without target execution."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess

from build_glim_point_arm64 import inputs as cross_inputs
from build_slam_arm64_trial import cross_environment, inspect_elf
from check_ground_native import verified_build
from evaluate_recorded_clearance import ROOT, digest
from run_glim_point_output_trial import private_output

RESULT = 'arm64_declared_terrain_linked_not_target_executed'
SOURCES = ('tools/build_terrain_arm64.py', 'tools/terrain-arm64/CMakeLists.txt',
    'tools/terrain-arm64/ground.exports', 'tools/terrain-arm64/octomap.exports',
    'robot-navigation/native-ground/ground_bridge.cpp',
    'robot-navigation/native-octomap/octomap_bridge.cpp',
    'robot-navigation/patchworkpp.lock.json', 'robot-navigation/octomap.lock.json',
    'robot-navigation/THIRD_PARTY_NOTICES.md')
LIBRARIES = ('liblingxi_ground.so', 'liblingxi_octomap.so')
ABI = {'liblingxi_ground.so': sorted('lingxi_ground_' + name for name in
    ('abi', 'revision', 'error', 'segment')), 'liblingxi_octomap.so': sorted(
        'lingxi_octomap_' + name for name in ('abi_version', 'version', 'error', 'project'))}
BOUNDARIES = dict.fromkeys(('robotContacted', 'targetExecutionValidated', 'freshProcessLoadPassed',
    'sensorsSubscribed', 'physicalMotionSent', 'physicalScaleValidated', 'groundSupportValidated',
    'navigationReady', 'deploymentApproved'), False)


def require_host():
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() == 0:
        raise ValueError('ordinary_user_linux_terrain_cross_build_required')


def load(path):
    path = Path(path)
    if (not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file()
            or not 0 < path.stat().st_size <= 1048576):
        raise ValueError('direct_bounded_terrain_build_input_required')
    return json.loads(path.read_bytes())


def checked_tree(root, expected):
    root = Path(root)
    if type(expected) is not dict or not expected:
        raise ValueError('complete_pinned_terrain_header_tree_required')
    for name, sha256 in expected.items():
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name
                or not relative.parts or '\\' in name):
            raise ValueError('direct_pinned_terrain_header_required')
        path = root / name
        if path.resolve(strict=True) != path or not path.is_file() or digest(path) != sha256:
            raise ValueError('unchanged_pinned_terrain_header_required')
    if {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()} != set(expected):
        raise ValueError('complete_pinned_terrain_header_tree_required')


def inputs(args):
    cross = cross_inputs(args)
    cross.pop('nativePointSourceSha256')
    cross.pop('builderSourceSha256')
    ground = args.ground_host / LIBRARIES[0]
    ground_report = verified_build(ground, digest(ground), args.ground_host / 'build-report.json')
    checked_tree(args.ground_host / 'eigen', ground_report['eigenHeadersSha256'])
    octomap = load(args.octomap_host / 'build-report.json')
    lock = load(ROOT / 'robot-navigation/octomap.lock.json')
    source_sha256 = {name: digest(ROOT / name) for name in SOURCES}
    if (octomap.get('schemaVersion') != 1 or octomap.get('octomapVersion') != lock['version']
            or octomap.get('purpose') != 'offline_native_build_not_target_acceptance'
            or octomap.get('sourceSha256') != source_sha256['robot-navigation/native-octomap/octomap_bridge.cpp']
            or octomap.get('nativeSha256') != digest(args.octomap_host / LIBRARIES[1])
            or octomap.get('freshProcessLoadPassed') is not True
            or any(octomap.get(key) is not False for key in ('robotContacted', 'navigationReady'))):
        raise ValueError('current_tested_host_octomap_build_required')
    checked_tree(args.octomap_host / 'include/octomap', octomap['headersSha256'])
    host_check = load(args.host_check)
    if (host_check.get('result') != 'native_synthetic_ground_fusion_passed'
            or host_check.get('machine') != 'x86_64' or host_check.get('testsRun') != 21
            or any(type(host_check.get(key)) is not int or host_check[key] != 0
                   for key in ('failures', 'errors', 'skipped'))
            or host_check.get('nativeSha256') != ground_report['nativeSha256']
            or host_check.get('octomapSha256') != octomap['nativeSha256']
            or host_check.get('groundBuildReportSha256') != digest(args.ground_host / 'build-report.json')
            or host_check.get('octomapDependenciesSha256') != octomap.get('dependencies')
            or host_check.get('inputClassification') != 'isolated_synthetic_ground_and_rays'
            or host_check.get('compiledAdapterSha256') != ground_report['adapterSha256']
            or any(host_check.get(key) is not False for key in ('robotContacted', 'sensorsSubscribed',
                'physicalMotionSent', 'physicalScaleValidated', 'groundSupportValidated', 'navigationReady'))):
        raise ValueError('complete_current_host_terrain_contracts_required')
    tested_sources = host_check.get('sourcesSha256')
    required_sources = {'robot-navigation/lingxi_navigation/ground.py',
        'robot-navigation/native-ground/ground_bridge.cpp', 'robot-navigation/patchworkpp.lock.json',
        'tools/check_ground_native.py', 'tools/build_ground_offline.py', 'tools/tests/test_ground.py',
        'robot-navigation/lingxi_navigation/occupancy.py'}
    if (type(tested_sources) is not dict or set(tested_sources) != required_sources
            or any(digest(ROOT / name) != expected for name, expected in tested_sources.items())):
        raise ValueError('current_host_terrain_contract_sources_required')
    dependency_root = args.octomap_arm64 / 'usr/lib/aarch64-linux-gnu'
    dependencies = lock['dependencies']
    for name, expected in dependencies.items():
        path = dependency_root / name
        if path.resolve(strict=True) != path or digest(path) != expected:
            raise ValueError('pinned_arm64_octomap_dependencies_required')
        with path.open('rb') as stream:
            header = stream.read(20)
        if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
            raise ValueError('arm64_octomap_dependency_architecture_required')
    notice = args.octomap_arm64 / 'usr/share/doc/liboctomap1.9/copyright'
    if notice.resolve(strict=True) != notice or not notice.is_file() or not 0 < notice.stat().st_size <= 262144:
        raise ValueError('direct_octomap_distribution_notice_required')
    return dict(crossCore=cross, sourceSha256=source_sha256, hostContractSourceSha256=tested_sources,
        groundBuildReportSha256=digest(args.ground_host / 'build-report.json'),
        octomapBuildReportSha256=digest(args.octomap_host / 'build-report.json'),
        hostContractReportSha256=digest(args.host_check), hostContracts=21,
        upstreamFilesSha256=ground_report['upstreamFilesSha256'],
        eigenHeadersSha256=ground_report['eigenHeadersSha256'],
        octomapHeadersSha256=octomap['headersSha256'], dependenciesSha256=dependencies,
        octomapDistributionNoticeSha256=digest(notice))


def snapshots(root, frozen):
    expected = {Path(name).name: sha256 for name, sha256 in frozen['sourceSha256'].items()
                if name.endswith(('.cpp', '.exports', 'CMakeLists.txt', '.lock.json', 'NOTICES.md'))}
    expected.update({'upstream/' + name: sha256 for name, sha256 in frozen['upstreamFilesSha256'].items()})
    expected.update({'eigen/' + name: sha256 for name, sha256 in frozen['eigenHeadersSha256'].items()})
    expected.update({'include/octomap/' + name: sha256 for name, sha256 in frozen['octomapHeadersSha256'].items()})
    expected.update({'lib/' + name: sha256 for name, sha256 in frozen['dependenciesSha256'].items()})
    expected['OCTOMAP_DISTRIBUTION_COPYRIGHT'] = frozen['octomapDistributionNoticeSha256']
    for name, sha256 in expected.items():
        path = root / name
        if path.resolve(strict=True) != path or digest(path) != sha256:
            raise ValueError('unchanged_terrain_compiled_input_copies_required')
    return expected


def copy_inputs(args, frozen):
    root = args.output / 'source'
    root.mkdir(mode=0o700)
    copies = {Path(name).name: ROOT / name for name in frozen['sourceSha256'] if name != SOURCES[0]}
    copies.update({'upstream/' + name: args.ground_host / 'upstream' / name
                   for name in frozen['upstreamFilesSha256']})
    copies.update({'eigen/' + name: args.ground_host / 'eigen' / name
                   for name in frozen['eigenHeadersSha256']})
    copies.update({'include/octomap/' + name: args.octomap_host / 'include/octomap' / name
                   for name in frozen['octomapHeadersSha256']})
    copies.update({'lib/' + name: args.octomap_arm64 / 'usr/lib/aarch64-linux-gnu' / name
                   for name in frozen['dependenciesSha256']})
    copies['OCTOMAP_DISTRIBUTION_COPYRIGHT'] = args.octomap_arm64 / 'usr/share/doc/liboctomap1.9/copyright'
    for name, source in copies.items():
        destination = root / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return snapshots(root, frozen)


def inspect(args, environment):
    tools = args.cross_work / 'toolchain/usr/bin'
    artifacts, exports = [], {}
    for name in LIBRARIES:
        path = args.output / 'build' / name
        artifacts.append(inspect_elf(path, tools / 'aarch64-linux-gnu-readelf', environment))
        result = subprocess.run([str(tools / 'aarch64-linux-gnu-nm'), '-D', '--defined-only', str(path)],
            check=True, capture_output=True, text=True, env=environment, timeout=30.)
        names = sorted(line.split()[-1] for line in result.stdout.splitlines() if line.split())
        if names != ABI[name]:
            raise ValueError('exact_four_function_terrain_abi_required')
        exports[name] = names
        dynamic = subprocess.check_output([str(tools / 'aarch64-linux-gnu-readelf'), '-d', str(path)],
            env=environment, text=True, timeout=30.)
        if '/home/' in dynamic or '/work/' in dynamic or str(args.output) in dynamic:
            raise ValueError('no_build_host_paths_in_terrain_dynamic_links_required')
        if name == LIBRARIES[1] and '$ORIGIN/lib' not in dynamic:
            raise ValueError('relative_private_octomap_dependency_lookup_required')
        needed = sorted(re.findall(r'\(NEEDED\).*?\[(.*?)\]', dynamic))
        allowed = {'libstdc++.so.6', 'libm.so.6', 'libgcc_s.so.1', 'libc.so.6', 'ld-linux-aarch64.so.1'}
        octomap = {'liboctomap.so.1.9', 'liboctomath.so.1.9'}
        if name == LIBRARIES[1]:
            allowed |= octomap
            if not octomap <= set(needed):
                raise ValueError('explicit_octomap_shared_dependencies_required')
        if not set(needed) <= allowed:
            raise ValueError('only_declared_terrain_runtime_dependencies_required')
        artifacts[-1]['neededSharedLibraries'] = needed
    return artifacts, exports


def runtime_files(root, frozen):
    expected = {'lib/' + name: sha256 for name, sha256 in frozen['dependenciesSha256'].items()}
    expected.update({'THIRD_PARTY_NOTICES.md': frozen['sourceSha256']['robot-navigation/THIRD_PARTY_NOTICES.md'],
        'PATCHWORKPP_LICENSE': frozen['upstreamFilesSha256']['LICENSE'],
        'OCTOMAP_DISTRIBUTION_COPYRIGHT': frozen['octomapDistributionNoticeSha256']})
    for name, sha256 in expected.items():
        path = root / name
        if path.resolve(strict=True) != path or digest(path) != sha256:
            raise ValueError('unchanged_prepared_arm64_terrain_dependency_required')
    for name in frozen['dependenciesSha256']:
        alias = root / 'lib' / name.removesuffix('.7')
        if not alias.is_symlink() or os.readlink(alias) != name or alias.resolve(strict=True) != root / 'lib' / name:
            raise ValueError('private_relative_octomap_soname_alias_required')


def write_report(path, report):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')


def build(args):
    require_host()
    if args.output.resolve() != args.output:
        raise ValueError('direct_new_terrain_cross_output_required')
    args.output = private_output(args.output)
    for path in (args.cross_work, args.native_work, args.ground_host, args.octomap_host, args.octomap_arm64):
        if args.output == path or path in args.output.parents or args.output in path.parents:
            raise ValueError('new_terrain_cross_output_outside_inputs_required')
    frozen = inputs(args)
    args.output.mkdir(mode=0o700)
    report = dict(schemaVersion=1, result='running', inputs=frozen, stages=[], artifacts=[],
        exportedCFunctions={}, boundaries=dict(BOUNDARIES))
    environment = cross_environment(args.cross_work, args.native_work)
    try:
        copy_inputs(args, frozen)
        revision = load(ROOT / 'robot-navigation/patchworkpp.lock.json')['revision']
        commands = (('configure', ['cmake', '-S', str(args.output / 'source'), '-B', str(args.output / 'build'),
            '-G', 'Ninja', '-DCMAKE_TOOLCHAIN_FILE=' + str(args.cross_work / 'arm64-toolchain.cmake'),
            '-DCMAKE_BUILD_TYPE=Release', '-DLINGXI_PATCHWORKPP_REVISION=' + revision]),
            ('build', ['cmake', '--build', str(args.output / 'build'), '--parallel', '2']))
        for name, command in commands:
            with (args.output / (name + '.private.log')).open('x') as log:
                result = subprocess.run(command, env=environment, stdout=log, stderr=log, timeout=240.)
            report['stages'].append(dict(name=name, exitCode=result.returncode))
            if result.returncode:
                raise RuntimeError('terrain_arm64_' + name + '_failed_see_private_log')
        report['artifacts'], report['exportedCFunctions'] = inspect(args, environment)
        # Runtime libraries and notices accompany the adapters, never vendor/system files.
        shutil.copytree(args.output / 'source/lib', args.output / 'build/lib')
        for name in frozen['dependenciesSha256']:
            (args.output / 'build/lib' / name.removesuffix('.7')).symlink_to(name)
        for name, source in (('THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md'),
                ('PATCHWORKPP_LICENSE', 'upstream/LICENSE'),
                ('OCTOMAP_DISTRIBUTION_COPYRIGHT', 'OCTOMAP_DISTRIBUTION_COPYRIGHT')):
            shutil.copyfile(args.output / 'source' / source, args.output / 'build' / name)
        runtime_files(args.output / 'build', frozen)
        if inputs(args) != frozen:
            raise ValueError('unchanged_source_bound_terrain_cross_inputs_required')
        snapshots(args.output / 'source', frozen)
        report['result'] = RESULT
    except BaseException:
        report['result'] = 'failed_or_interrupted'
        raise
    finally:
        write_report(args.output / 'build-report.json', report)
    return report


def verify(args):
    require_host()
    frozen, report = inputs(args), load(args.output / 'build-report.json')
    if (report.get('schemaVersion') != 1 or report.get('result') != RESULT or report.get('inputs') != frozen
            or report.get('boundaries') != BOUNDARIES or report.get('stages') != [
                dict(name=name, exitCode=0) for name in ('configure', 'build')]):
        raise ValueError('complete_nonexecuted_terrain_arm64_build_required')
    snapshots(args.output / 'source', frozen)
    runtime_files(args.output / 'build', frozen)
    artifacts, exports = inspect(args, cross_environment(args.cross_work, args.native_work))
    if report.get('artifacts') != artifacts or report.get('exportedCFunctions') != exports:
        raise ValueError('unchanged_arm64_terrain_artifacts_required')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'verify'))
    for name in ('cross-work', 'native-work', 'ground-host', 'octomap-host', 'octomap-arm64', 'host-check', 'output'):
        parser.add_argument('--' + name, type=lambda value: Path(value).absolute(), required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        report = {'build': build, 'verify': verify}[args.command](args)
        print(json.dumps(dict(result=report['result'], artifacts=report['artifacts'])))
    finally:
        os.umask(previous)


if __name__ == '__main__':
    main()
