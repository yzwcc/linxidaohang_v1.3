"""Move this product's internal API off ports reserved for other services."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import tempfile


RESERVED = {'3000': '3445', '3444': '3445'}


def migrate(path):
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('direct_robot_environment_file_required')
    original = path.read_bytes()
    text = original.decode('utf-8')
    lines = text.splitlines(keepends=True)
    selected = {}
    positions = {}
    for index, line in enumerate(lines):
        body = line.rstrip('\r\n')
        if not body or body.lstrip().startswith('#') or '=' not in body:
            continue
        key, value = body.split('=', 1)
        if key in ('LINGXI_PORT', 'LINGXI_API_URL'):
            if key in selected:
                raise ValueError('duplicate_robot_environment_setting')
            selected[key] = value
            positions[key] = index

    port_is_explicit = 'LINGXI_PORT' in selected
    current = selected.get('LINGXI_PORT', '3445').strip()
    if not re.fullmatch(r'[1-9][0-9]{0,4}', current) or not 1 <= int(current) <= 65535:
        raise ValueError('invalid_internal_api_port')
    replacement = RESERVED.get(current)
    api_url = selected.get('LINGXI_API_URL', '').strip()
    legacy_local_urls = {'https://127.0.0.1:3000', 'https://127.0.0.1:3444'}
    if not port_is_explicit and api_url in legacy_local_urls:
        replacement = '3445'
    if replacement is None:
        return {'changed': False, 'updatedKeys': []}

    updated = []
    if 'LINGXI_PORT' in positions:
        index = positions['LINGXI_PORT']
        ending = '\r\n' if lines[index].endswith('\r\n') else '\n' if lines[index].endswith('\n') else ''
        lines[index] = f'LINGXI_PORT={replacement}{ending}'
    else:
        if lines and not lines[-1].endswith(('\n', '\r')):
            lines[-1] += '\n'
        lines.append(f'LINGXI_PORT={replacement}\n')
    updated.append('LINGXI_PORT')

    if api_url == f'https://127.0.0.1:{current}' or (
            not port_is_explicit and api_url in legacy_local_urls):
        index = positions['LINGXI_API_URL']
        ending = '\r\n' if lines[index].endswith('\r\n') else '\n' if lines[index].endswith('\n') else ''
        lines[index] = f'LINGXI_API_URL=https://127.0.0.1:{replacement}{ending}'
        updated.append('LINGXI_API_URL')

    value = ''.join(lines).encode('utf-8')
    metadata = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix='.robot.env.', dir=path.parent)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
        if hasattr(os, 'fchown') and os.geteuid() == 0:
            os.fchown(descriptor, metadata.st_uid, metadata.st_gid)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return {'changed': True, 'updatedKeys': updated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('robot_env', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.robot_env)))
    except (OSError, UnicodeError, ValueError):
        print('robot environment migration failed; inspect the protected backup')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
