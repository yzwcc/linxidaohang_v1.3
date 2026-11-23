#!/usr/bin/env python3
"""Create a new, motion-disabled X2 app profile after the target sensor probe."""

import argparse
import os
from pathlib import Path

import yaml


def build_profile(source, overlay, fastlio, data):
    if not all(item.is_absolute() for item in (source, overlay, fastlio, data)):
        raise ValueError("reference profile inputs must be absolute paths")
    app = yaml.safe_load((source / "config/app.yaml").read_text())
    localizer = source / "config/localizer.yaml"
    mapper = overlay / "lib/fast_lio/fastlio_mapping"
    recorder = overlay / "lib/robot_nav_recorder/mapping_recorder"
    for item in (localizer, fastlio):
        if not item.is_file():
            raise ValueError("missing reference profile input")
    if any(not item.is_file() or not os.access(item, os.X_OK) for item in (mapper, recorder)):
        raise ValueError("missing executable FAST-LIO or recorder")
    app.update(motion_enabled=False, nav2_enabled=True, navigation_mode="point", data_dir=str(data),
               localizer_config=str(localizer), allowed_import_roots=[str(data / "inbox"), str(data / "maps")],
               allowed_recording_roots=[str(data / "recordings")])
    app["visualization"]["enabled"] = False
    app["color_mapping"]["enabled"] = False
    app["mapping"].update(enabled=True, loop_closure_enabled=False, grid_cache_enabled=False,
                          root_dir=str(data / "recordings"),
                          fastlio_executable=str(mapper), fastlio_config=str(fastlio),
                          recorder_executable=str(recorder), calibration_files=[str(fastlio)])
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source", "overlay", "fastlio-config", "data-dir", "output"):
        parser.add_argument(f"--{option}", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.is_absolute():
        parser.error("output path must be absolute")
    try:
        profile = build_profile(args.source, args.overlay, args.fastlio_config, args.data_dir)
        payload = yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(payload)
    except (KeyError, AttributeError, OSError, TypeError, ValueError, yaml.YAMLError):
        parser.error("unable to prepare profile; check the inputs and choose a new output path")


if __name__ == "__main__":
    main()
