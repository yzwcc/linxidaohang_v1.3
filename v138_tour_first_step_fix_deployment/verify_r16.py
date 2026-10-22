"""Run the shared regression suites against the shipped r16 runtime."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from build_r16 import BRIDGE, HERE, NAME, REFERENCE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--linux", action="store_true")
    parser.add_argument("--original", action="store_true")
    args = parser.parse_args()
    environment = dict(os.environ)
    environment.update(
        LINGXI_REFERENCE_ROOT=str(REFERENCE),
        LINGXI_BRIDGE_SOURCE=str(REFERENCE / ("runtime" if args.original else "patched") / BRIDGE),
        LINGXI_ARCHIVE=str(HERE / "dist" / (NAME + ".tar.gz")),
    )
    if args.linux:
        command = [sys.executable, "-m", "unittest", "discover", "-s", "tests",
                   "-p", "test_install.py", "-v"]
    else:
        command = [sys.executable, "-m", "pytest", "tests/test_first_step_race.py",
                   "tests/test_r20_compatibility.py", "tests/test_patch_scope.py", "-q"]
        if args.original:
            command = [sys.executable, "-m", "pytest", "tests/test_first_step_race.py", "-q",
                       "-k", "concurrent_start_requests or repeated_start_rejections or "
                       "enabled_task_step_without_point_id or rejected_attempt_is_invalidated"]
    raise SystemExit(subprocess.run(command, cwd=HERE, env=environment).returncode)


if __name__ == "__main__":
    main()
