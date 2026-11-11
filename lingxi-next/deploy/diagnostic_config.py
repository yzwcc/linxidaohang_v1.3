"""Read diagnostic targets without evaluating configuration or exposing secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys


KEYS = frozenset((
    'LINGXI_PORT', 'LINGXI_SLAM_PORT', 'LINGXI_NAVIGATION_SOCKET',
    'LINGXI_PRODUCT_CONFIG', 'LINGXI_PRODUCT_CONFIG_SHA256',
))


def read_settings(config):
    with config.open('rb') as stream:
        raw = stream.read(131073)
    if len(raw) > 131072:
        raise ValueError('diagnostic_configuration_too_large')
    settings = {}
    for line in raw.decode('utf-8').splitlines():
        key, separator, value = line.partition('=')
        if separator and key in KEYS:
            if key in settings:
                raise ValueError('duplicate_diagnostic_setting')
            settings[key] = value.strip()
    return settings


def port(value):
    if not re.fullmatch(r'[1-9][0-9]{0,4}', value) or not 1 <= int(value) <= 65535:
        raise ValueError('invalid_diagnostic_port')
    if int(value) == 3000:
        raise ValueError('unrelated_project_port_reserved')
    return int(value)


def health_targets(settings):
    targets = [('independent-slam', port(settings.get('LINGXI_SLAM_PORT', '3444')))]
    core_port = port(settings.get('LINGXI_PORT', '3445'))
    if core_port in (3000, 3444, targets[0][1]):
        raise ValueError('diagnostic_core_api_port_conflict')
    targets.append(('core-api', core_port))
    return targets


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_product_configuration_field')
        result[key] = value
    return result


def navigation_socket(settings):
    selected = settings.get('LINGXI_PRODUCT_CONFIG', '')
    digest = settings.get('LINGXI_PRODUCT_CONFIG_SHA256', '')
    if not (selected or digest):
        return settings.get('LINGXI_NAVIGATION_SOCKET', '')
    if not selected or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('pinned_product_configuration_required')
    path = Path(selected)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError('direct_product_configuration_required')
    with path.open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('product_configuration_integrity_failed')
    product = json.loads(raw, object_pairs_hook=unique_object)
    if (type(product) is not dict or type(product.get('schemaVersion')) is not int
            or product['schemaVersion'] != 1):
        raise ValueError('invalid_product_configuration_schema')
    endpoint = product.get('navigationSocket', '')
    if type(endpoint) is not str or ('navigationSocket' in product and not endpoint):
        raise ValueError('invalid_product_navigation_socket')
    # Product mode ignores the unrelated core socket, just like the live owner.
    return endpoint


def bridge_summary(settings):
    value = navigation_socket(settings)
    if not value:
        return 'navigation bridge disabled'
    endpoint = Path(value)
    if (not endpoint.is_absolute() or str(endpoint) != value or '\0' in value
            or '..' in endpoint.parts or len(value.encode('utf-8')) >= 100):
        raise ValueError('invalid_navigation_bridge_socket_path')
    try:
        metadata = endpoint.lstat()
    except FileNotFoundError:
        return 'navigation bridge configured but not listening (socket absent)'
    if not stat.S_ISSOCK(metadata.st_mode):
        raise ValueError('invalid_navigation_bridge_socket_file_type')
    result = 'navigation bridge present (not a liveness or readiness check)'
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        result += '\nnavigation bridge socket permissions require review (expected 0600)'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('targets', 'bridge'))
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    try:
        settings = read_settings(args.config)
        if args.mode == 'targets':
            print('\n'.join(f'{name} {number}' for name, number in health_targets(settings)))
        else:
            print(bridge_summary(settings))
    except (OSError, ValueError, UnicodeError):
        # Paths, JSON decoder snippets and environment values can contain secrets.
        print('diagnostic configuration invalid or unavailable; inspect locally', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
