"""Prepare an isolated Nav2 composition, not a robot deployment or execution claim."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'robot-navigation'))
from lingxi_navigation.nav2_profile import (
    BEHAVIOR_TREE, THROUGH_POSES_BEHAVIOR_TREE, OfflinePlatform, parameters, source_reference)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def load_platform(path):
    path = Path(path)
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or not path.is_file() or path.stat().st_size > 16 * 1024):
        raise ValueError('bounded_direct_platform_profile_required')
    value = json.loads(path.read_bytes(), object_pairs_hook=strict_object)
    if type(value) is not dict or set(value) != set(OfflinePlatform.__dataclass_fields__):
        raise ValueError('explicit_platform_fields_required')
    return OfflinePlatform(**value)


def prepare(platform, output, *, domain_id=173):
    if type(platform) is not OfflinePlatform or type(domain_id) is not int or not 1 <= domain_id <= 200:
        raise ValueError('explicit_offline_platform_and_domain_required')
    output = Path(output)
    if (not output.is_absolute() or output.exists() or output.is_symlink()
            or output.parent.resolve(strict=True) != output.parent or output.name in ('', '.', '..')
            or output == ROOT or output in ROOT.parents
            or ROOT in output.parents and not output.is_relative_to(ROOT / '.local')):
        raise ValueError('new_separate_offline_nav2_output_required')
    namespace = 'lingxi_offline_' + secrets.token_hex(16)
    tree = output / 'navigate.xml'
    through_tree = output / 'navigate-through.xml'
    config = parameters(platform, tree, through_tree, namespace)
    source_names = ('robot-navigation/lingxi_navigation/nav2_profile.py',
                    'robot-navigation/lingxi_navigation/runtime.py',
                    'robot-navigation/nav2_offline.launch.py', 'tools/prepare_nav2_offline.py')
    sources = {name: digest(ROOT / name) for name in source_names}
    files = {
        'navigate.xml': BEHAVIOR_TREE,
        'navigate-through.xml': THROUGH_POSES_BEHAVIOR_TREE,
        # JSON flow style is YAML 1.2 compatible. Keep a single serialization so
        # the diagnostic JSON and ROS YAML cannot diverge.
        'nav2-parameters.yaml': json.dumps({namespace: config}, indent=2, allow_nan=False) + '\n',
        'platform.json': json.dumps(asdict(platform), indent=2, allow_nan=False) + '\n',
        'nav2_offline.launch.py': (ROOT / source_names[2]).read_text(encoding='utf-8'),
    }
    output.mkdir(mode=0o700)
    for name, content in files.items():
        path = output / name
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
        path.chmod(0o600)
    manifest = dict(schemaVersion=1, purpose='offline_nav2_preparation_not_acceptance',
                    namespace=namespace, domainId=domain_id,
                    upstream=source_reference(), sources=sources,
                    files={name: digest(output / name) for name in files},
                    dynamicObstaclePolicy='mark_only_latched_no_automatic_clearing',
                    environment=dict(ROS_LOCALHOST_ONLY='1', ROS_DOMAIN_ID=str(domain_id),
                                     RMW_IMPLEMENTATION='rmw_fastrtps_cpp'),
                    physicalMotionSent=False, controllerExecuted=False, navigationReady=False)
    if any(digest(ROOT / name) != sha for name, sha in sources.items()):
        raise ValueError('navigation_sources_changed_during_preparation')
    path = output / 'offline-nav2-manifest.json'
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write('\n')
    path.chmod(0o600)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--domain-id', type=int, default=173)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = prepare(load_platform(args.platform), args.output, domain_id=args.domain_id)
        print(json.dumps(dict(namespace=result['namespace'], controllerExecuted=False,
                              physicalMotionSent=False, navigationReady=False)))
    finally:
        os.umask(previous)
