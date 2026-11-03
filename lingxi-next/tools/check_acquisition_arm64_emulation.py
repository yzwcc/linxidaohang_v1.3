"""Run frozen ARM64 synthetic contracts under QEMU, never as hardware acceptance."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time

import acquisition_target_probe as trial

ROOT = Path(__file__).resolve().parents[1]
RESULT = 'qemu_arm64_acquisition_contracts_passed_not_robot_execution'
SOURCES = ('tools/check_acquisition_arm64_emulation.py', 'tools/acquisition_target_probe.py')
BOUNDARIES = {**trial.BOUNDARIES, 'robotContacted': False,
              'targetExecutionValidated': False, 'liveTerrainIntegrated': False}


def elf(path, machine):
    path = Path(path)
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError('direct_emulation_binary_required')
    with path.open('rb') as stream:
        header = stream.read(20)
    if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != machine:
        raise ValueError('expected_emulation_elf_architecture_required')


def command(args, *arguments):
    libraries = ':'.join(str(path) for path in (args.bundle / 'lib',
        args.sysroot / 'usr/lib/aarch64-linux-gnu', args.sysroot / 'lib/aarch64-linux-gnu'))
    return [str(args.qemu), '-L', str(args.sysroot), '-E', 'LD_LIBRARY_PATH=' + libraries,
            '-E', 'LD_BIND_NOW=1', '-E', 'OMP_NUM_THREADS=2', '-E', 'OPENBLAS_NUM_THREADS=2',
            *(str(value) for value in arguments)]


def invoke(args, *arguments):
    return subprocess.run(command(args, *arguments), cwd=args.output, capture_output=True,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, timeout=10., check=False)


def providers(text, args, manifest):
    found = {}
    for line in text.splitlines():
        if not line.strip() or re.fullmatch(r'\s*linux-vdso\.so\.1 \(0x[0-9a-f]+\)\s*', line):
            continue
        match = re.fullmatch(r'\s*(\S+) => (/\S+) \(0x[0-9a-f]+\)\s*', line)
        if match is not None:
            name, path = Path(match[1]).name, Path(match[2]).resolve(strict=True)
        else:
            # The loader lists itself without an arrow when inspecting a shared object.
            match = re.fullmatch(r'\s*(/\S*/ld-linux-aarch64\.so\.1) \(0x[0-9a-f]+\)\s*', line)
            if match is None:
                raise ValueError('complete_emulated_dependency_resolution_required')
            name, path = 'ld-linux-aarch64.so.1', Path(match[1]).resolve(strict=True)
        expected = manifest['files'].get('lib/' + name)
        if expected is not None:
            if path != args.bundle / 'lib' / name or trial.sha256(path) != expected['sha256']:
                raise ValueError('frozen_emulated_component_provider_required')
            origin = 'trial'
        elif path.is_relative_to(args.sysroot):
            origin = 'sysroot'
        else:
            raise ValueError('isolated_emulated_dependency_provider_required')
        elf(path, 183)
        base = args.bundle if origin == 'trial' else args.sysroot
        entry = dict(origin=origin, relative=path.relative_to(base).as_posix(), sha256=trial.sha256(path))
        if name in found and found[name] != entry:
            raise ValueError('single_emulated_dependency_provider_required')
        found[name] = entry
    if not found:
        raise ValueError('nonempty_emulated_dependency_inventory_required')
    return found


def verify_inputs(args):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() == 0:
        raise ValueError('ordinary_user_linux_x64_emulation_required')
    for path in (args.bundle, args.sysroot, args.qemu, args.output):
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError('direct_absolute_emulation_paths_required')
    if (args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir()
            or any(a == b or a in b.parents or b in a.parents for a, b in
                   ((args.output, args.bundle), (args.output, args.sysroot), (args.output, args.qemu)))):
        raise ValueError('new_emulation_output_outside_inputs_required')
    if not trial.digest(args.qemu_sha256) or trial.sha256(args.qemu) != args.qemu_sha256:
        raise ValueError('pinned_emulator_required')
    elf(args.qemu, 62)
    elf(args.sysroot / 'lib/aarch64-linux-gnu/ld-linux-aarch64.so.1', 183)
    manifest = trial.verify_bundle(args.bundle)
    if not trial.digest(args.manifest_sha256) or trial.sha256(args.bundle / trial.MANIFEST) != args.manifest_sha256:
        raise ValueError('pinned_emulation_trial_required')
    for name, identity in manifest['sourceSha256'].items():
        if trial.sha256(ROOT / name) != identity:
            raise ValueError('current_emulation_trial_sources_required')
    elf(args.bundle / 'bin/acquisition_contract', 183)
    return manifest


def run(args):
    manifest = verify_inputs(args)
    source_hashes = {name: trial.sha256(ROOT / name) for name in SOURCES}
    loader = args.sysroot / 'lib/aarch64-linux-gnu/ld-linux-aarch64.so.1'
    loader_hash, started = trial.sha256(loader), time.monotonic()
    args.output.mkdir(mode=0o700)
    dependencies, executed = {}, []
    try:
        # Resolve the plugin separately: the synthetic frame executable does not dlopen it.
        for binary in (args.bundle / 'bin/acquisition_contract',
                       args.bundle / 'lib/libodometry_estimation_cpu.so'):
            elf(binary, 183)
            result = invoke(args, loader, '--list', binary)
            if result.returncode or result.stderr:
                raise RuntimeError('emulated_dependency_listing_failed')
            resolved = providers(result.stdout.decode('utf-8'), args, manifest)
            if binary.parent == args.bundle / 'lib':
                resolved[binary.name] = dict(origin='trial', relative=binary.relative_to(args.bundle).as_posix(),
                                            sha256=trial.sha256(binary))
            for name, entry in resolved.items():
                if name in dependencies and dependencies[name] != entry:
                    raise ValueError('single_emulated_dependency_provider_required')
                dependencies[name] = entry
        for name in (Path(value).name for value in manifest['files'] if value.startswith('lib/')):
            if dependencies.get(name, {}).get('origin') != 'trial':
                raise ValueError('all_frozen_component_dependencies_required')
        executable = args.bundle / 'bin/acquisition_contract'
        for name in trial.CONTRACTS:
            result = invoke(args, executable, name.removeprefix('acquisition_'))
            if result.returncode or result.stdout or result.stderr:
                raise RuntimeError('emulated_contract_failed_' + name)
            executed.append(dict(name=name, exitCode=0))
        result = invoke(args, executable, 'export')
        if result.returncode or result.stderr:
            raise RuntimeError('emulated_export_failed')
        exported = trial.validate_export(result.stdout)
        if exported['sha256'] != manifest['syntheticExportSha256']:
            raise ValueError('same_host_and_emulated_synthetic_export_required')
        for entry in dependencies.values():
            base = args.bundle if entry['origin'] == 'trial' else args.sysroot
            if trial.sha256(base / entry['relative']) != entry['sha256']:
                raise ValueError('unchanged_emulated_dependency_required')
        if (trial.verify_bundle(args.bundle) != manifest
                or trial.sha256(args.bundle / trial.MANIFEST) != args.manifest_sha256
                or trial.sha256(args.qemu) != args.qemu_sha256 or trial.sha256(loader) != loader_hash
                or any(trial.sha256(ROOT / name) != identity for name, identity in
                       {**manifest['sourceSha256'], **source_hashes}.items())):
            raise ValueError('unchanged_emulation_inputs_required')
        report = dict(schemaVersion=1, result=RESULT, executionMode='qemu-user',
            inputClassification='synthetic_fixture', qemuSha256=args.qemu_sha256,
            loaderSha256=loader_hash, trialManifestSha256=args.manifest_sha256,
            componentManifestSha256=manifest['componentManifestSha256'],
            sourceSha256=source_hashes, trialSourceSha256=manifest['sourceSha256'],
            resolvedDependencies=dependencies, executedContracts=executed,
            syntheticExport=exported, seconds=time.monotonic() - started, boundaries=dict(BOUNDARIES))
    except Exception as error:
        report = dict(schemaVersion=1, result='arm64_emulation_failed_not_accepted',
            error=type(error).__name__, executedContracts=executed, boundaries=dict(BOUNDARIES))
        (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        raise
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'sysroot', 'qemu', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('qemu-sha256', 'manifest-sha256'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps(run(args), allow_nan=False))
    finally:
        os.umask(previous)


if __name__ == '__main__':
    main()
