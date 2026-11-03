"""Prepare private installed acquisition/terrain files, never start native or ROS IO."""
import argparse
import copy
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
from types import SimpleNamespace

import prepare_installed_mapping_inputs as mapping

ROOT = mapping.ROOT
sys.path[:0] = [str(ROOT / 'robot-runtime/python'), str(ROOT / 'robot-slam'),
               str(ROOT / 'robot-navigation')]
import acquisition_bundle
from live_acquisition import AcquisitionSettings, strict_json
import terrain_target_probe as terrain

MANIFEST = 'acquisition-inputs-manifest.json'
PURPOSE = 'installed_passive_acquisition_inputs_not_target_acceptance'
BOUNDARIES = dict(mapping.BOUNDARIES, targetContractsExecuted=False, groundSupportValidated=False)
MAX_FILES, MAX_BYTES = 128, 128 * 1024**2


def read_settings(path):
    mapping.direct(path)
    if not 0 < path.stat().st_size <= 16384:
        raise ValueError('bounded_installed_acquisition_settings_required')
    return strict_json(path.read_bytes())


def selected_platform(selection):
    root = Path(selection['root'])
    mapping.verify(root, selection['manifestSha256'])
    value = mapping.read_json(root / 'mapping.json', 4096)
    core = mapping.read_json(Path(value['coreRoot']) / 'bundle-manifest.json')
    # Match MappingPlatform's full-mapper config choice, without its target/DDS checks.
    return SimpleNamespace(arm64=True, mode=value['inputMode'], value=value,
        config_dir=Path(value['fullMapping']['root']) / 'config',
        frozen={'coreLibrariesSha256': {Path(name).name: entry['sha256']
            for name, entry in core['files'].items() if name.startswith('lib/')}})


def inspect_inputs(path, selection):
    platform = selected_platform(selection)
    value = read_settings(path)
    if 'terrain' in value and (type(value['terrain']) is not dict or 'targetTrial' not in value['terrain']):
        raise ValueError('installed_terrain_requires_existing_target_trial_receipt')
    settings = AcquisitionSettings(path, platform)
    files, aliases = {}, {}

    def add(prefix, root, declared):
        for member in declared:
            relative = PurePosixPath(member)
            if (relative.is_absolute() or str(relative) != member or '..' in relative.parts
                    or '\\' in member or not member):
                raise ValueError('direct_declared_installed_acquisition_members_required')
            source = root / member
            files[prefix + member] = (source, mapping.fingerprint(source))

    component = Path(value['acquisition']['root'])
    manifest = mapping.read_json(component / acquisition_bundle.MANIFEST)
    add('acquisition/', component, (*manifest['files'], acquisition_bundle.MANIFEST))
    if 'terrain' in value:
        trial = value['terrain']['targetTrial']
        root = Path(trial['root'])
        manifest = terrain.verify_bundle(root)
        add('terrain/', root, (*manifest['files'], terrain.MANIFEST))
        report = Path(trial['report'])
        files['terrain-target-report.json'] = (report, mapping.fingerprint(report))
        aliases = {'terrain/' + name: target for name, target in manifest['symlinks'].items()}
    if (len(files) > MAX_FILES or sum(info['bytes'] for _, info in files.values()) > MAX_BYTES):
        raise ValueError('bounded_installed_acquisition_closure_required')
    if settings.verify() != settings.frozen:
        raise ValueError('unchanged_installed_acquisition_inputs_required')
    return value, files, aliases


def rewritten(value, output):
    value = copy.deepcopy(value)
    value['acquisition']['root'] = str(output / 'acquisition')
    if 'terrain' in value:
        trial = value['terrain']['targetTrial']
        trial['root'] = str(output / 'terrain')
        trial['report'] = str(output / 'terrain-target-report.json')
        for name, library in zip(('ground', 'octomap'), terrain.LIBRARIES):
            value['terrain'][name]['library'] = str(output / 'terrain/native' / library)
    return value


def tree(root, files, aliases):
    names = set(files) | set(aliases)
    expected_dirs = {str(parent) for name in names for parent in PurePosixPath(name).parents
                     if str(parent) != '.'}
    seen, directories = set(), set()
    for path in root.rglob('*'):
        info = path.lstat()
        name = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            mapping.private_directory(path)
            directories.add(name)
        elif stat.S_ISLNK(info.st_mode):
            if (name not in aliases or os.readlink(path) != aliases[name]
                    or path.resolve(strict=True) != path.parent / aliases[name]):
                raise ValueError('only_declared_relative_terrain_aliases_required')
            seen.add(name)
        elif stat.S_ISREG(info.st_mode):
            if name == MANIFEST:
                continue
            if info.st_mode & 0o077 or name not in files or mapping.fingerprint(path) != files[name]:
                raise ValueError('exact_private_acquisition_input_members_required')
            seen.add(name)
        else:
            raise ValueError('direct_installed_acquisition_input_members_required')
    if seen != names or directories != expected_dirs:
        raise ValueError('exact_installed_acquisition_input_tree_required')


def verify(root, expected):
    mapping.private_directory(root)
    receipt = mapping.direct(root / MANIFEST)
    if (not mapping.digest(expected) or not 0 < receipt.stat().st_size <= 262144
            or mapping.sha256(receipt) != expected
            or receipt.stat().st_mode & 0o077):
        raise ValueError('pinned_private_acquisition_input_manifest_required')
    report = strict_json(receipt.read_bytes())
    fields = {'schemaVersion', 'purpose', 'preparerSha256', 'mappingInputs', 'sourceSettingsSha256',
              'settingsSha256', 'files', 'symlinks', 'boundaries'}
    if (type(report) is not dict or set(report) != fields or type(report['schemaVersion']) is not int
            or report['schemaVersion'] != 1 or report['purpose'] != PURPOSE
            or report['preparerSha256'] != mapping.sha256(Path(__file__))
            or report['boundaries'] != BOUNDARIES
            or any(value is not False for value in report['boundaries'].values())
            or type(report['mappingInputs']) is not dict
            or set(report['mappingInputs']) != {'root', 'manifestSha256'}
            or type(report['files']) is not dict or not 2 < len(report['files']) <= MAX_FILES + 2
            or type(report['symlinks']) is not dict
            or report['symlinks'] not in ({}, {'terrain/' + name: target for name, target in terrain.aliases().items()})):
        raise ValueError('explicit_unaccepted_installed_acquisition_inputs_required')
    tree(root, report['files'], report['symlinks'])
    original, effective = root / 'original-settings.json', root / 'acquisition.json'
    if (mapping.sha256(original) != report['sourceSettingsSha256']
            or mapping.sha256(effective) != report['settingsSha256']):
        raise ValueError('unchanged_installed_acquisition_settings_required')
    value, plan, aliases = inspect_inputs(effective, report['mappingInputs'])
    if value != rewritten(read_settings(original), root):
        raise ValueError('only_explicit_installed_acquisition_paths_may_change')
    files = {name: info for name, (_, info) in plan.items()}
    files.update({path.name: mapping.fingerprint(path) for path in (original, effective)})
    if files != report['files'] or aliases != report['symlinks'] or mapping.sha256(receipt) != expected:
        raise ValueError('unchanged_installed_acquisition_closure_required')
    return report


def prepare(settings, expected, selection, output):
    preparer = mapping.sha256(Path(__file__))
    mapping.direct(settings)
    if not mapping.digest(expected) or mapping.sha256(settings) != expected:
        raise ValueError('pinned_original_acquisition_settings_required')
    value, plan, aliases = inspect_inputs(settings, selection)
    sources = [Path(selection['root']), Path(value['acquisition']['root'])]
    if 'terrain' in value:
        sources.append(Path(value['terrain']['targetTrial']['root']))
    if (not output.is_absolute() or output.resolve() != output or output.exists() or output.is_symlink()
            or any(output.is_relative_to(Path(path)) for path in ('/home', '/root'))
            or settings.is_relative_to(output)
            or any(source == output or source.is_relative_to(output) or output.is_relative_to(source)
                   for source in sources)):
        raise ValueError('new_direct_service_accessible_acquisition_output_required')
    mapping.private_directory(output.parent)
    output.mkdir(mode=0o700)
    for name, (source, info) in sorted(plan.items()):
        target = output / name
        for parent in reversed(PurePosixPath(name).parents):
            if str(parent) != '.':
                (output / str(parent)).mkdir(mode=0o700, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o700 if info['executable'] else 0o600)
        if mapping.fingerprint(target) != info or mapping.fingerprint(source) != info:
            raise ValueError('identical_installed_acquisition_copy_required')
    for name, target in aliases.items():
        (output / name).symlink_to(target)
    original, effective = output / 'original-settings.json', output / 'acquisition.json'
    shutil.copyfile(settings, original)
    original.chmod(0o600)
    effective.write_text(json.dumps(rewritten(value, output), indent=2, allow_nan=False) + '\n', encoding='utf-8')
    effective.chmod(0o600)
    copied, copied_plan, copied_aliases = inspect_inputs(effective, selection)
    files = {name: info for name, (_, info) in plan.items()}
    if (copied != rewritten(value, output) or copied_aliases != aliases
            or {name: info for name, (_, info) in copied_plan.items()} != files
            or mapping.sha256(settings) != expected or mapping.sha256(original) != expected
            or mapping.sha256(Path(__file__)) != preparer):
        raise ValueError('unchanged_original_and_relocated_acquisition_inputs_required')
    files.update({path.name: mapping.fingerprint(path) for path in (original, effective)})
    tree(output, files, aliases)
    report = dict(schemaVersion=1, purpose=PURPOSE, preparerSha256=preparer,
        mappingInputs=selection, sourceSettingsSha256=expected, settingsSha256=mapping.sha256(effective),
        files=files, symlinks=aliases, boundaries=BOUNDARIES)
    receipt = output / MANIFEST
    receipt.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    receipt.chmod(0o600)
    identity = mapping.sha256(receipt)
    verify(output, identity)
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    install = commands.add_parser('prepare')
    for name in ('settings', 'mapping-inputs', 'output'):
        install.add_argument('--' + name, type=Path, required=True)
    for name in ('settings-sha256', 'mapping-manifest-sha256'):
        install.add_argument('--' + name, required=True)
    check = commands.add_parser('verify')
    check.add_argument('--root', type=Path, required=True)
    check.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise ValueError('ordinary_user_linux_acquisition_preparation_required')
    previous = os.umask(0o077)
    try:
        if args.command == 'prepare':
            root = args.output
            identity = prepare(args.settings, args.settings_sha256,
                {'root': str(args.mapping_inputs), 'manifestSha256': args.mapping_manifest_sha256}, root)
        else:
            root, identity = args.root, args.manifest_sha256
        report = verify(root, identity)
        print(json.dumps(dict(result='installed_acquisition_inputs_verified_not_target_accepted',
            manifestSha256=identity, settings=str(root / 'acquisition.json'),
            settingsSha256=report['settingsSha256'], files=len(report['files']),
            terrainIncluded=bool(report['symlinks']), boundaries=report['boundaries']), allow_nan=False))
    finally:
        os.umask(previous)


if __name__ == '__main__':
    main()
