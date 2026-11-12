"""Refresh reviewed Python bindings into NEW private inputs; preserve native build receipts."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def reviewed_changes(bundle, before, after, allowed):
    eligible = {name for name in bundle.OBSERVATION_SOURCES if name.endswith('.py')}
    changed = {name for name in before if before[name] != after.get(name)}
    if (set(before) != set(after) or not changed or changed != set(allowed)
            or not changed <= eligible):
        raise ValueError('only_explicit_reviewed_python_binding_changes_required')
    return sorted(changed)


def rebind_full(bundle, source, expected, output, *, baseline, core, allowed):
    """Caller verifies the old closure with its pinned previous release first."""
    source = bundle.direct(source, directory=True)
    manifest = source / bundle.MANIFEST
    if bundle.sha256(manifest) != expected:
        raise ValueError('pinned_previous_full_manifest_required')
    value = bundle.read_json(manifest)
    before, after = value['sourceSha256'], bundle.source_hashes(observed=True)
    changed = reviewed_changes(bundle, before, after, allowed)
    if value['files'] != bundle.inventory(source):
        raise ValueError('unchanged_previous_native_files_required')
    if output.exists() or output.is_symlink() or output.resolve() != output:
        raise ValueError('new_full_binding_output_required')
    shutil.copytree(source, output)
    value['sourceSha256'] = after
    target = output / bundle.MANIFEST
    target.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    target.chmod(0o600)
    identity = bundle.sha256(target)
    # Validates ALL new Python/native hashes, binary build provenance, sensor
    # configuration, core libraries and false acceptance flags without exceptions.
    bundle.verify_bundle(output, identity, baseline=baseline,
        core_manifest=bundle.sha256(core / 'bundle-manifest.json'),
        core_libraries={name: bundle.sha256(core / 'lib' / name) for name in bundle.CORE_LIBRARIES},
        platform='linux-aarch64', observed=True)
    if bundle.source_hashes(observed=True) != after or bundle.sha256(manifest) != expected:
        raise ValueError('unchanged_source_bindings_during_refresh_required')
    return identity, changed


def refresh(args):
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise ValueError('ordinary_user_linux_binding_refresh_required')
    project, previous = args.project_root.resolve(strict=True), args.previous_project.resolve(strict=True)
    sys.path[:0] = [str(project / 'tools'), str(project / 'robot-runtime/python'), str(project / 'robot-slam')]
    import async_mapping_bundle as bundle
    import prepare_installed_mapping_inputs as installed

    root = bundle.direct(args.root, directory=True)
    installed.private_directory(root)
    installed.private_directory(args.output.parent)
    old_command = [sys.executable, '-s', '-B', str(previous / 'tools/prepare_installed_mapping_inputs.py'),
                   'verify', '--root', str(root), '--manifest-sha256', args.manifest_sha256]
    def verify_previous():
        result = subprocess.run(old_command, check=True, capture_output=True, text=True, timeout=120)
        report = json.loads(result.stdout)
        if report['manifestSha256'] != args.manifest_sha256:
            raise ValueError('verified_previous_input_identity_required')
    verify_previous()
    config = installed.read_json(root / 'mapping.json')
    full = Path(config['fullMapping']['root'])
    if full != root / 'full':
        raise ValueError('materialized_previous_full_mapper_required')
    output = args.output
    source = output.with_name(output.name + '-source.json')
    new_full = output.with_name(output.name + '-full-source')
    if any(path.exists() or path.is_symlink() for path in (output, source, new_full)):
        raise ValueError('new_refresh_paths_required')
    if type(args.localization_seconds) not in (int, float) or not 5 <= args.localization_seconds <= 3600:
        raise ValueError('bounded_localization_duration_required')
    identity, changed = rebind_full(bundle, full, config['fullMapping']['manifestSha256'], new_full,
        baseline=Path(config['root']), core=Path(config['coreRoot']), allowed=args.allow_python_source)
    config['fullMapping']['root'] = str(new_full)
    config['fullMapping']['manifestSha256'] = identity
    config['localizationMaxSeconds'] = args.localization_seconds
    source.write_text(json.dumps(config, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    source.chmod(0o600)
    receipt = installed.prepare(source, bundle.sha256(source), output)
    installed.verify(output, receipt)
    verify_previous()
    return {'result': 'reviewed_python_bindings_refreshed_not_target_accepted',
        'manifestSha256': receipt, 'config': str(output / 'mapping.json'),
        'reviewedChangedSources': changed, 'mappingMaxSeconds': config['maxSeconds'],
        'localizationMaxSeconds': config['localizationMaxSeconds'],
        'previousInputsPreserved': True, 'nativeBinariesRebuilt': False,
        'physicalCalibrationAccepted': False, 'navigationReady': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    for name in ('previous-project', 'root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--localization-seconds', type=float, required=True)
    parser.add_argument('--allow-python-source', action='append', required=True)
    args = parser.parse_args()
    previous_umask = os.umask(0o077)
    try:
        print(json.dumps(refresh(args), allow_nan=False))
    finally:
        os.umask(previous_umask)


if __name__ == '__main__':
    main()
