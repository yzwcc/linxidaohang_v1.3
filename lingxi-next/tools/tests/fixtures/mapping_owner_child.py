"""Standard-library-only synthetic processes for the real mapping/SLAM owner."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'robot-runtime/python'))
from mapping_owner import ManagedMappingOwner, MappingOwnerLimits
from slam_owner import ManagedSlamOwner


def main():
    mode, scenario, output = sys.argv[1:4]
    if mode != 'owner':
        behavior = scenario.removeprefix(mode + '-') if scenario.startswith(mode + '-') else 'healthy'
        if behavior == 'exit':
            return 1
        if behavior == 'closed':
            os.close(1)
        elif behavior == 'json':
            print('PRIVATE_MARKER: not JSON', flush=True)
        elif behavior == 'oversize':
            print('x' * 4097, flush=True)
        elif behavior == 'wrong':
            print(json.dumps({'service': 'PRIVATE_MARKER', 'event': 'listening'}), flush=True)
        elif behavior == 'port':
            print(json.dumps({'service': 'lingxi-next', 'event': 'listening', 'port': 0}), flush=True)
        elif behavior != 'timeout':
            print(json.dumps({'service': sys.argv[4] if mode == 'service' else 'lingxi-next',
                              'event': 'listening', **({'port': 3443} if mode == 'api' else {})}), flush=True)
        sys.stdin.buffer.read()
        if behavior == 'stubborn':
            time.sleep(10)
        return 0
    environment = {'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'}
    output = Path(output)
    owner_class = ManagedSlamOwner if scenario == 'slam-healthy' else ManagedMappingOwner
    maps = output.parent / 'maps'
    maps.mkdir(mode=0o700)
    owner = owner_class(
        [sys.executable, '-B', __file__, 'service', scenario, str(output), owner_class.service_name],
        [sys.executable, '-B', __file__, 'api', scenario, str(output)],
        output=output, maps=maps, service_environment=environment, api_environment=environment,
        limits=MappingOwnerLimits(startup_seconds=.6, service_stop_seconds=.6, api_stop_seconds=.6,
                                  signal_seconds=.3, tick_seconds=.01))

    def ready(_):
        if scenario.startswith('callback-error'):
            raise RuntimeError('PRIVATE_MARKER: callback path or credential')
        if scenario in ('service-running-exit', 'api-running-exit'):
            child = owner.service if scenario.startswith('service') else owner.api
            child.kill()
            child.wait(timeout=2)

    if scenario in ('cleanup-error', 'callback-error-cleanup'):
        def failed_cleanup():
            raise RuntimeError('PRIVATE_MARKER: adopted cleanup')
        owner._adopted = failed_cleanup
    report = owner.run(seconds=.06, on_listening=None if scenario == 'cleanup-error' else ready)
    return 0 if report['result'] == 'stopped' else 1


if __name__ == '__main__':
    raise SystemExit(main())
