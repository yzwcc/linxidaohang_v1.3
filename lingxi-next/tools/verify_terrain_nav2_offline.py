"""Run four actual-native terrain trials, optionally rerunning the ROS baseline.

Linux root is required only for the isolated rootfs namespaces. No sensor or
actuator service is launched. Outputs are exclusive, private offline artifacts.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from export_nav2_evidence import collect as collect_baseline
from export_terrain_nav2_evidence import collect
from terrain_nav2_fixture import CASES, ROOT


def verify(args):
    if os.geteuid() != 0 or not re.fullmatch(r'[a-z0-9-]{1,60}', args.run_id):
        raise ValueError('linux_root_and_bounded_new_run_id_required')
    fixtures, reports = {}, {}
    shell = ROOT / 'tools/nav2-offline-isolated.sh'

    def trial(case, output, fixture=None):
        command = ['bash', str(shell), str(args.rootfs), case, output]
        if fixture is not None:
            command.append(str(fixture))
        subprocess.run(command, check=True, timeout=100)
        return args.rootfs / output.lstrip('/') / 'report.json'

    for case in CASES:
        path = args.cache / (args.run_id + '-' + case + '.json')
        command = [sys.executable, str(ROOT / 'tools/terrain_nav2_fixture.py'), '--case', case, '--output', str(path)]
        for name in ('ground_library', 'ground_sha256', 'ground_build_report', 'octomap_library', 'octomap_sha256', 'octomap_build_report'):
            command.extend(['--' + name.replace('_', '-'), str(getattr(args, name))])
        subprocess.run(command, check=True, timeout=75, stdout=subprocess.DEVNULL)
        fixtures[case] = path
        reports[case] = trial(case, '/' + args.run_id + '-' + case, path)
    receipt = collect(reports, fixtures)
    with args.evidence_output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write('\n')
    if args.baseline_output is not None:
        from run_nav2_offline import CASES as BASELINE_CASES
        paths = [trial(case, '/' + args.run_id + '-baseline-' + case) for case in BASELINE_CASES]
        receipt = collect_baseline(paths)
        with args.baseline_output.open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, indent=2, allow_nan=False)
            stream.write('\n')
        trial('publication-probe', '/' + args.run_id + '-publication-probe')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('rootfs', 'cache', 'ground-library', 'ground-build-report', 'octomap-library',
                 'octomap-build-report', 'evidence-output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('run-id', 'ground-sha256', 'octomap-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--baseline-output', type=Path)
    os.umask(0o077)
    verify(parser.parse_args())
