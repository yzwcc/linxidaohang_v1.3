"""Prepare and run a source-bound synthetic acquisition trial in a new robot directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time
from types import SimpleNamespace

import acquisition_target_probe as probe
from run_slam_target_trial import connect, target_directory

ROOT = Path(__file__).resolve().parents[1]


def private_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + '\n')
    path.chmod(0o600)


def prepare(args):
    import build_glim_acquisition_arm64 as builder
    sys.path.insert(0, str(ROOT / 'robot-runtime/python'))
    import acquisition_bundle as component

    output = builder.native.private_output(args.output)
    paths = (args.build, args.component, args.cross_work, args.native_work, args.native_acquisition)
    if any(output == path or output in path.parents or path in output.parents for path in paths):
        raise ValueError('new_trial_outside_acquisition_inputs_required')
    options = SimpleNamespace(output=args.build, cross_work=args.cross_work,
        native_work=args.native_work, native_acquisition=args.native_acquisition)
    report = builder.verify(options)
    host_report = builder.native.load(args.native_acquisition / 'build-report.json')
    exported = host_report.get('syntheticGeometry', {}).get('archiveSha256')
    core = report['inputs']['crossCore']['referenceCoreLibrariesSha256']
    value = component.verify_bundle(args.component, args.component_sha256, core_libraries=core)
    if (not probe.digest(exported) or report['configuredTargetContracts'] != list(probe.CONTRACTS)
            or probe.sha256(args.component / 'build-report.json') != probe.sha256(args.build / 'build-report.json')):
        raise ValueError('same_verified_acquisition_build_required')
    sources = {**value['sourceSha256'], **{name: probe.sha256(ROOT / name) for name in (
        'tools/acquisition_target_probe.py', 'tools/run_acquisition_target_trial.py')}}
    members = {'lib/' + probe.LIBRARY: args.component / 'lib' / probe.LIBRARY,
        'bin/acquisition_contract': args.build / 'build/acquisition_contract',
        'acquisition_target_probe.py': ROOT / 'tools/acquisition_target_probe.py'}
    members.update({'lib/' + name: args.cross_work / 'prefix/lib' / name for name in core})
    output.mkdir(mode=0o700)
    (output / 'lib').mkdir(mode=0o700)
    (output / 'bin').mkdir(mode=0o700)
    files = {}
    for name, path in members.items():
        destination = output / name
        shutil.copyfile(path, destination)
        executable = name == 'bin/acquisition_contract'
        destination.chmod(0o700 if executable else 0o600)
        files[name] = dict(bytes=destination.stat().st_size, sha256=probe.sha256(destination), executable=executable)
    if files['bin/acquisition_contract']['sha256'] != report['artifacts'][1]['sha256']:
        raise ValueError('same_compiled_arm64_contract_executable_required')
    manifest = dict(schemaVersion=1, purpose='synthetic_arm64_acquisition_trial', platform='linux-aarch64',
        inputClassification='synthetic_fixture', componentManifestSha256=args.component_sha256,
        componentBuildReportSha256=probe.sha256(args.component / 'build-report.json'), sourceSha256=sources,
        coreLibrariesSha256=core, syntheticExportSha256=exported,
        contracts=list(probe.CONTRACTS), files=files, boundaries=dict(probe.BOUNDARIES))
    private_json(output / probe.MANIFEST, manifest)
    probe.verify_bundle(output)
    if (builder.verify(options) != report
            or component.verify_bundle(args.component, args.component_sha256, core_libraries=core) != value
            or any(probe.sha256(ROOT / name) != identity for name, identity in sources.items())):
        raise ValueError('unchanged_acquisition_trial_inputs_required')
    return dict(result='arm64_acquisition_trial_prepared_not_executed',
        trialManifestSha256=probe.sha256(output / probe.MANIFEST), files=len(files),
        contracts=len(probe.CONTRACTS), boundaries=dict(probe.BOUNDARIES))


def command(remote):
    return shlex.join(['timeout', '--signal=TERM', '--kill-after=5s', '120s',
        'env', '-i', 'PATH=/usr/bin:/bin', 'HOME=' + str(remote.parent), 'LANG=C.UTF-8',
        'LD_LIBRARY_PATH=' + str(remote / 'lib'), 'OMP_NUM_THREADS=2', 'OPENBLAS_NUM_THREADS=2',
        'python3', '-I', '-B', str(remote / 'acquisition_target_probe.py'), '--root', str(remote)])


def validate_result(result, manifest, identity):
    libraries = {Path(name).name: entry['sha256'] for name, entry in manifest['files'].items()
                 if name.startswith('lib/')}
    if (result.get('result') != probe.RESULT or result.get('inputClassification') != 'synthetic_fixture'
            or result.get('target', {}).get('machine') != 'aarch64'
            or result.get('trialManifestSha256') != identity
            or result.get('componentManifestSha256') != manifest['componentManifestSha256']
            or result.get('componentBuildReportSha256') != manifest['componentBuildReportSha256']
            or result.get('mappedLibrariesSha256') != libraries
            or result.get('executedContracts') != [dict(name=name, exitCode=0) for name in probe.CONTRACTS]
            or result.get('checks') != probe.CHECKS or any(item is not True for item in result['checks'].values())
            or result.get('boundaries') != probe.BOUNDARIES
            or any(item is not False for item in result['boundaries'].values())
            or result.get('syntheticExport') != dict(bytes=840, sha256=manifest['syntheticExportSha256'],
                frameId=1, stamp=1002., returnCount=3, trajectorySamples=2)):
        raise ValueError('complete_source_bound_arm64_acquisition_result_required')


def receive(channel):
    output, errors = bytearray(), bytearray()
    deadline = time.monotonic() + 140.
    while True:
        for ready, read, data in ((channel.recv_ready, channel.recv, output),
                                 (channel.recv_stderr_ready, channel.recv_stderr, errors)):
            if ready():
                block = read(65536)
                if len(data) + len(block) > 1024 * 1024:
                    raise RuntimeError('bounded_acquisition_target_output_required')
                data.extend(block)
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            return channel.recv_exit_status(), output, errors
        if time.monotonic() >= deadline:
            raise TimeoutError('bounded_acquisition_target_process_did_not_return')
        time.sleep(.05)


def run(args):
    bundle = args.bundle.resolve(strict=True)
    manifest = probe.verify_bundle(bundle)
    identity = probe.sha256(bundle / probe.MANIFEST)
    output = args.output.resolve()
    if output == bundle or bundle in output.parents:
        raise ValueError('acquisition_trial_report_outside_frozen_bundle_required')
    if args.output.exists() or args.output.is_symlink() or args.user == 'root':
        raise ValueError('new_private_report_and_ordinary_robot_user_required')
    if args.manifest_sha256 != identity or any(
            probe.sha256(ROOT / name) != value for name, value in manifest['sourceSha256'].items()):
        raise ValueError('explicit_current_source_bound_acquisition_trial_required')
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    client, report = None, dict(result='running', trialManifestSha256=identity)
    try:
        client = connect(args.host, args.user, args.known_identity)
        with client.open_sftp() as sftp:
            remote = target_directory(sftp.normalize('.'), args.directory)
            sftp.mkdir(str(remote), mode=0o700)
            report['privateRemoteDirectory'] = str(remote)
            for name in ('lib', 'bin'):
                sftp.mkdir(str(remote / name), mode=0o700)
            for name in sorted(manifest['files']) + [probe.MANIFEST]:
                sftp.put(str(bundle / name), str(remote / name), confirm=True)
                sftp.chmod(str(remote / name), 0o700 if manifest['files'].get(name, {}).get('executable') else 0o600)
        with client.get_transport().open_session(timeout=8) as channel:
            channel.exec_command(command(remote))
            status, output, errors = receive(channel)
        report.update(probeExitCode=status, privateOutputSha256=hashlib.sha256(output + errors).hexdigest())
        if status != 0 or errors:
            report['privateProbeOutput'] = (output + errors).decode('utf-8', errors='replace')
            raise RuntimeError('arm64_acquisition_probe_failed_see_private_report')
        result = json.loads(output.decode('utf-8'))
        validate_result(result, manifest, identity)
        if probe.verify_bundle(bundle) != manifest or probe.sha256(bundle / probe.MANIFEST) != identity:
            raise ValueError('local_acquisition_trial_changed_during_target_execution')
        report.update(result=probe.RESULT, targetProbe=result)
    except BaseException as error:
        report.update(result='failed_or_interrupted', errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        private_json(args.output, report)
    return dict(result=report['result'], trialManifestSha256=identity,
        executedContracts=len(report['targetProbe']['executedContracts']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare')
    for name in ('build', 'component', 'cross-work', 'native-work', 'native-acquisition', 'output'):
        prepare_parser.add_argument('--' + name, type=lambda value: Path(value).absolute(), required=True)
    prepare_parser.add_argument('--component-sha256', required=True)
    run_parser = commands.add_parser('run')
    for name in ('host', 'user', 'known-identity', 'directory', 'manifest-sha256'):
        run_parser.add_argument('--' + name, required=True)
    for name in ('bundle', 'output'):
        run_parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps({'prepare': prepare, 'run': run}[args.command](args)))
    finally:
        os.umask(previous)


if __name__ == '__main__':
    main()
