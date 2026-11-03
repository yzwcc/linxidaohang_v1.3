"""Explicit product composition; socket selection does not grant readiness."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


def private_path(value, *, directory=False):
    path = Path(value)
    info = path.lstat()
    if (not path.is_absolute() or str(path) != value or path.resolve(strict=True) != path
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077))):
        raise ValueError('direct_owned_private_product_path_required')
    return path


def navigation_socket(value):
    if (sys.platform != 'linux' or type(value) is not str or not value or '\x00' in value
            or len(value.encode('utf-8')) >= 100):
        raise ValueError('private_absolute_linux_navigation_socket_required')
    path = Path(value)
    if not path.is_absolute() or str(path) != value:
        raise ValueError('private_absolute_linux_navigation_socket_required')
    try:
        private_path(str(path.parent), directory=True)
    except OSError as error:
        raise ValueError('existing_private_navigation_socket_parent_required') from error
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('owned_private_navigation_socket_required')
    return path


def selection(args, config):
    file, expected = getattr(args, 'product_config', None), getattr(args, 'product_config_sha256', None)
    if file is None and expected is None:
        return {}
    if file is None or type(expected) is not str or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('paired_product_configuration_required')
    file = private_path(str(file))
    if not 0 < file.stat().st_size <= 16384:
        raise ValueError('bounded_product_configuration_required')
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('unchanged_product_configuration_required')
    value = json.loads(data)
    fields = {'schemaVersion', 'stateDirectory', 'agentUrl', 'agentTokenFile'}
    if (type(value) is not dict or set(value) not in (fields, fields | {'navigationSocket'})
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or any(type(value[name]) is not str for name in ('stateDirectory', 'agentUrl', 'agentTokenFile'))):
        raise ValueError('explicit_product_configuration_schema_required')
    if not re.fullmatch(r'http://127\.0\.0\.1:([1-9][0-9]{0,4})', value['agentUrl']) or int(value['agentUrl'].rsplit(':', 1)[1]) > 65535:
        raise ValueError('local_product_agent_endpoint_required')
    state = private_path(value['stateDirectory'], directory=True)
    token = private_path(value['agentTokenFile'])
    if not 1 <= token.stat().st_size <= 4096 or not re.fullmatch(r'[\x21-\x7e]{32,4096}', token.read_text(encoding='utf-8').strip()):
        raise ValueError('bounded_valid_product_agent_token_required')
    from managed_mapping import validate_output
    validate_output(state, config, (file, token, args.config, args.node, args.api_bundle), existing=True)
    selected = [file, state, token]
    if 'navigationSocket' in value:
        endpoint = navigation_socket(value['navigationSocket'])
        validate_output(endpoint.parent, config, (file, token, args.config, args.node, args.api_bundle), existing=True)
        selected.append(endpoint.parent)
    paths = [*selected, args.maps, args.output]
    for name in ('LINGXI_TLS_KEY', 'LINGXI_TLS_CERT', 'LINGXI_WEB_ROOT'):
        if os.environ.get(name):
            path = Path(os.environ[name]).resolve(strict=True)
            paths.append(path.parent if name == 'LINGXI_WEB_ROOT' else path)
    for index, left in enumerate(selected):
        for right in paths[index + 1:]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError('separate_product_configuration_state_and_runtime_required')
    return {'LINGXI_PRODUCT_CONFIG': str(file), 'LINGXI_PRODUCT_CONFIG_SHA256': expected}
