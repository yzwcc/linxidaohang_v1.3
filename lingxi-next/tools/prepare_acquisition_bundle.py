"""Package verified ARM64 acquisition bytes; no native load, deployment or sensors."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'robot-runtime/python'))
import acquisition_bundle as bundle
import build_glim_acquisition_arm64 as builder


def prepare(args):
    output = builder.native.private_output(args.output)
    for path in (args.build, args.cross_work, args.native_work, args.native_acquisition):
        if output == path or path in output.parents or output in path.parents:
            raise ValueError('new_acquisition_bundle_outside_frozen_inputs_required')
    options = SimpleNamespace(output=args.build, cross_work=args.cross_work,
        native_work=args.native_work, native_acquisition=args.native_acquisition)
    report = builder.verify(options)
    sources = bundle.source_hashes()
    core = report['inputs']['crossCore']['referenceCoreLibrariesSha256']
    output.mkdir(mode=0o700)
    (output / 'lib').mkdir(mode=0o700)
    shutil.copyfile(args.build / 'build' / bundle.LIBRARY, output / 'lib' / bundle.LIBRARY)
    shutil.copyfile(args.build / 'build-report.json', output / 'build-report.json')
    value = dict(schemaVersion=1, engine=bundle.ENGINE, platform='linux-aarch64', sourceSha256=sources,
        coreLibrariesSha256=core, files=bundle.inventory(output), boundaries=dict(bundle.BOUNDARIES))
    builder.native.private_json(output / bundle.MANIFEST, value)
    identity = bundle.sha256(output / bundle.MANIFEST)
    bundle.verify_bundle(output, identity, core_libraries=core)
    if builder.verify(options) != report or bundle.source_hashes() != sources:
        raise ValueError('unchanged_acquisition_bundle_build_and_sources_required')
    return dict(result='source_bound_arm64_acquisition_bundle_prepared_not_target_executed',
        manifestSha256=identity, librarySha256=value['files']['lib/' + bundle.LIBRARY]['sha256'],
        boundaries=dict(bundle.BOUNDARIES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'cross-work', 'native-work', 'native-acquisition', 'output'):
        parser.add_argument('--' + name, type=lambda value: Path(value).absolute(), required=True)
    previous = os.umask(0o077)
    try:
        print(json.dumps(prepare(parser.parse_args())))
    finally:
        os.umask(previous)


if __name__ == '__main__':
    main()
