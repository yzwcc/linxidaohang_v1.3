"""Verify an explicit ARM64 per-return module without loading native code."""
import re

from async_mapping_bundle import ROOT, bounded_text, digest, direct, sha256
import json

MANIFEST = 'acquisition-manifest.json'
LIBRARY = 'liblingxi_glim_acquisition_output.so'
ENGINE = 'glim-per-return-acquisition-v1'
RESULT = 'arm64_acquisition_adapter_linked_not_target_executed'
NATIVE_SOURCES = tuple('robot-slam/native-acquisition-output/' + name for name in (
    'acquisition.h', 'acquisition.cpp', 'acquisition_contract.cpp', 'CMakeLists.txt', 'exports.map'))
BUILD_SOURCES = (*NATIVE_SOURCES, 'robot-slam/lingxi_slam/acquisition_output.py',
    'robot-slam/lingxi_slam/observation_archive.py', 'robot-slam/lingxi_slam/point_output.py',
    'tools/run_glim_acquisition_trial.py', 'tools/glim_matched_session.py')
SOURCES = (*BUILD_SOURCES, 'tools/build_glim_acquisition_arm64.py',
    'robot-runtime/python/acquisition_bundle.py')
BOUNDARIES = dict.fromkeys(('robotContacted', 'targetExecutionValidated', 'liveTerrainIntegrated',
    'physicalScaleValidated', 'groundSupportValidated', 'navigationReady', 'deploymentApproved'), False)


def source_hashes():
    return {name: sha256(direct(ROOT / name)) for name in SOURCES}


def inventory(root):
    direct(root, directory=True)
    expected = {'build-report.json', 'lib/' + LIBRARY}
    members = {}
    for path in root.rglob('*'):
        direct(path, directory=path.is_dir())
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            if name != 'lib':
                raise ValueError('explicit_acquisition_bundle_directory_required')
            continue
        if name == MANIFEST:
            continue
        if name not in expected or not 0 < path.stat().st_size <= 16 * 1024**2:
            raise ValueError('bounded_explicit_acquisition_bundle_members_required')
        members[name] = dict(bytes=path.stat().st_size, sha256=sha256(path))
    if set(members) != expected:
        raise ValueError('complete_acquisition_bundle_members_required')
    return members


def core_matches(required, available):
    """Versioned ELF SONAMEs and bundled aliases must resolve to identical bytes."""
    def canonical(values):
        if type(values) is not dict or not 1 <= len(values) <= 64:
            raise ValueError('explicit_acquisition_core_library_inventory_required')
        result = {}
        for name, value in values.items():
            if (type(name) is not str or re.fullmatch(r'lib[A-Za-z0-9_-]+\.so(?:\.[0-9][A-Za-z0-9]*)*', name) is None
                    or not digest(value)):
                raise ValueError('named_pinned_acquisition_core_libraries_required')
            key = name.partition('.so')[0]
            if key in result and result[key] != value:
                raise ValueError('unambiguous_acquisition_core_aliases_required')
            result[key] = value
        return result
    required, available = canonical(required), canonical(available)
    return 'libglim' in required and all(available.get(name) == value for name, value in required.items())


def verify_bundle(root, expected, *, core_libraries):
    root = direct(root, directory=True)
    manifest = direct(root / MANIFEST)
    if not digest(expected) or sha256(manifest) != expected:
        raise ValueError('explicit_acquisition_manifest_identity_required')
    value = json.loads(bounded_text(manifest))
    fields = {'schemaVersion', 'engine', 'platform', 'sourceSha256', 'coreLibrariesSha256', 'files', 'boundaries'}
    if (type(value) is not dict or set(value) != fields or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['engine'] != ENGINE or value['platform'] != 'linux-aarch64'
            or value['sourceSha256'] != source_hashes() or value['files'] != inventory(root)
            or value['boundaries'] != BOUNDARIES
            or any(value['boundaries'].get(name) is not False for name in BOUNDARIES)
            or not core_matches(value['coreLibrariesSha256'], core_libraries)):
        raise ValueError('unchanged_source_bound_acquisition_bundle_required')
    report = json.loads(bounded_text(root / 'build-report.json'))
    compiled = report.get('inputs', {})
    artifacts = report.get('artifacts', [])
    contracts = compiled.get('hostPassedContracts')
    if (report.get('schemaVersion') != 1 or report.get('result') != RESULT
            or report.get('buildType') != 'Release'
            or compiled.get('sourceSha256') != {name: value['sourceSha256'][name] for name in BUILD_SOURCES}
            or compiled.get('builderSha256') != value['sourceSha256']['tools/build_glim_acquisition_arm64.py']
            or compiled.get('crossCore', {}).get('referenceCoreLibrariesSha256') != value['coreLibrariesSha256']
            or report.get('boundaries') != BOUNDARIES
            or any(report['boundaries'].get(name) is not False for name in BOUNDARIES)
            or type(report.get('executedTargetContracts')) is not int or report['executedTargetContracts'] != 0
            or report.get('stages') != [dict(name=name, exitCode=0) for name in ('configure', 'build')]
            or report.get('exportedCFunctions') != [
                'lingxi_acquisition_' + name for name in ('close', 'counts', 'error', 'open', 'take')]
            or type(contracts) is not list or len(contracts) != 29 or len(set(contracts)) != 29
            or report.get('configuredTargetContracts') != contracts
            or len(artifacts) != 2 or [row.get('file') for row in artifacts] != [LIBRARY, 'acquisition_contract']
            or artifacts[0].get('sha256') != value['files']['lib/' + LIBRARY]['sha256']
            or any(row.get('machine') != 'AArch64' or row.get('class') != 'ELF64' for row in artifacts)):
        raise ValueError('tested_host_source_and_nonexecuted_acquisition_arm64_build_required')
    with (root / 'lib' / LIBRARY).open('rb') as stream:
        header = stream.read(20)
    if len(header) != 20 or header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
        raise ValueError('arm64_acquisition_elf_required')
    return value
