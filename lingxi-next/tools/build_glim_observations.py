"""Build a separate source-bound observed-ray mapper; do not alter a live mapper."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

import run_glim_async_mapping as asynchronous

ROOT = asynchronous.ROOT
SOURCE = ROOT / "tools/glim-observations"
SOURCES = ("CMakeLists.txt", "observation_capture.hpp", "observation_capture.cpp", "observation-capture.patch")


def inputs(args):
    return {"mapping": asynchronous.inputs(args),
            "observations": {name: asynchronous.full.sha256(SOURCE / name) for name in SOURCES},
            "builderSha256": asynchronous.full.sha256(Path(__file__).resolve())}


def verified_adapter(args):
    root = args.mapping_adapter
    report = json.loads((root / "build-report.json").read_text())
    library = root / "build" / asynchronous.full.LIBRARY
    current = inputs(args)
    if (report.get("result") != "observed_mapping_adapter_built_not_geometry_acceptance"
            or report.get("inputs") != current or report.get("librarySha256") != asynchronous.full.sha256(library)
            or report.get("copiedSourceSha256") != {
                p.relative_to(root / "source").as_posix(): asynchronous.full.sha256(p)
                for p in sorted((root / "source").rglob("*")) if p.is_file()}):
        raise ValueError("unchanged_source_bound_observed_mapper_required")
    return library


def build(args):
    if platform.system() != "Linux" or platform.machine() != "x86_64" or os.geteuid() == 0:
        raise ValueError("ordinary_linux_offline_observation_build_required")
    output = asynchronous.full.private_output(args.output)
    if any(output == path or path in output.parents or output in path.parents for path in (args.work, args.upstream)):
        raise ValueError("observation_build_output_must_be_outside_inputs")
    frozen = inputs(args)
    output.mkdir(mode=0o700)
    source = output / "source"
    source.mkdir()
    for name in SOURCES:
        shutil.copyfile(SOURCE / name, source / name)
    for name in asynchronous.NATIVE_SOURCES:
        target = source / "async" / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(asynchronous.SOURCE / name, target)
    (source / "async/sync").mkdir()
    shutil.copyfile(asynchronous.full.SOURCE / "full_mapping.cpp", source / "async/sync/full_mapping.cpp")
    for name in asynchronous.full.UPSTREAM_INPUTS:
        target = source / "upstream" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.upstream / name, target)
    env = asynchronous.full.replay.library_environment(args.work)
    commands = []
    for index, patch in enumerate((asynchronous.full.SOURCE / "submapping-eof.patch", source / "observation-capture.patch")):
        for check in (True, False):
            commands.append((f"patch-{index}-{'check' if check else 'apply'}", ["git", "-C", str(source / "upstream"),
                "apply", "--recount", *(["--check"] if check else []), str(patch)]))
    commands += [("configure", ["cmake", "-S", str(source), "-B", str(output / "build"), "-DCMAKE_BUILD_TYPE=Release"]),
                 ("build", ["cmake", "--build", str(output / "build"), "--parallel", "1"])]
    for name, command in commands:
        with (output / (name + ".private.log")).open("x") as log:
            result = subprocess.run(command, env=env, stdout=log, stderr=log, timeout=600)
        if result.returncode:
            raise RuntimeError("observed_mapping_build_failed_see_private_log_" + name)
    library = output / "build" / asynchronous.full.LIBRARY
    if inputs(args) != frozen:
        raise ValueError("observation_build_inputs_changed")
    report = {"result": "observed_mapping_adapter_built_not_geometry_acceptance", "inputs": frozen,
              "librarySha256": asynchronous.full.sha256(library),
              "privateClassSymbols": asynchronous.full.private_class_symbols(library),
              "copiedSourceSha256": {p.relative_to(source).as_posix(): asynchronous.full.sha256(p)
                                    for p in sorted(source.rglob("*")) if p.is_file()},
              "boundaries": dict.fromkeys(asynchronous.full.BOUNDARIES, False)}
    asynchronous.full.save(output / "build-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("work", "upstream", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    for name, value in vars(args).items():
        setattr(args, name, value.absolute())
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": build(args)["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
