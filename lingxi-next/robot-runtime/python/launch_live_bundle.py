"""Verified read-only live point trial entrypoint; not a service installer."""
import argparse
import os
from pathlib import Path
import sys

import live_bundle as bundle
import live_points


def launch(args):
    if args.operator_confirmed is not True:
        raise ValueError("fresh_stationary_supervised_estop_confirmation_required")
    root = args.bundle.resolve(strict=True)
    output = args.output.absolute()
    if output == root or root in output.parents or output in root.parents:
        raise ValueError("live_trial_output_outside_verified_runtime_required")
    manifest = bundle.verify_bundle(root, args.manifest_sha256)
    if os.environ.get("LINGXI_POINT_READ_ONLY", "1") != "1":
        raise ValueError("read_only_live_trial_required")
    web, data = str(root / "web"), str(output / "data")
    if (os.environ.get("LINGXI_WEB_ROOT", web) != web or os.environ.get("LINGXI_DATA_DIR", data) != data
            or not os.environ.get("LINGXI_PORT") or os.environ["LINGXI_PORT"] in ("0", "3000")):
        raise ValueError("isolated_trial_port_web_and_private_data_required")
    args.node, args.api_bundle = root / "node/bin/node", root / "api/main.mjs"
    args.node_sha256 = manifest["nodeBinarySha256"]
    args.api_sha256 = manifest["files"]["api/main.mjs"]["sha256"]
    previous = {name: os.environ.get(name) for name in (
        "LINGXI_POINT_READ_ONLY", "LINGXI_WEB_ROOT", "LINGXI_DATA_DIR")}
    try:
        os.environ.update(LINGXI_POINT_READ_ONLY="1", LINGXI_WEB_ROOT=web, LINGXI_DATA_DIR=data)
        return live_points.run(args)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "root", "core-root", "point-bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("manifest-sha256", "point-manifest-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--hypothesis", choices=("header_is_scan_start", "header_is_scan_end"), required=True)
    parser.add_argument("--seconds", type=float, default=60.)
    parser.add_argument("--operator-confirmed", action="store_true")
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        return 0 if launch(args)["result"] == "stopped" else 1
    finally:
        os.umask(previous)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Lingxi Next verified live trial failed; inspect private reports.", file=sys.stderr)
        raise SystemExit(1)
