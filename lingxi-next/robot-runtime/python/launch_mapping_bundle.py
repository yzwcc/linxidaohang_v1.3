"""Source-bound ARM64 managed mapping commissioning; never install a service."""
import argparse
import os
from pathlib import Path
import platform
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_bundle as bundle
import managed_mapping


def launch(args):
    root = args.bundle.resolve(strict=True)
    if bundle.ROOT != root / "source":
        raise ValueError("execute_launcher_from_extracted_mapping_bundle_required")
    manifest = bundle.verify_bundle(root, args.manifest_sha256, profile=bundle.MAPPING)
    if (sys.platform != "linux" or platform.machine() != "aarch64"
            or os.geteuid() == 0 or sys.version_info[:2] != (3, 10)):
        raise ValueError("ordinary_user_arm64_humble_python310_required")
    output = args.output.absolute()
    if (output == root or root in output.parents or output in root.parents
            or args.config.resolve(strict=True).is_relative_to(root)):
        raise ValueError("mapping_configuration_and_output_outside_runtime_required")
    for name in ("LINGXI_TLS_KEY", "LINGXI_TLS_CERT"):
        value = os.environ.get(name, "")
        file = Path(value)
        if (not file.is_absolute() or file.resolve(strict=True) != file or not file.is_file()
                or file.is_relative_to(root) or file.is_relative_to(output)):
            raise ValueError("external_direct_mapping_tls_file_required")
    port = os.environ.get("LINGXI_PORT", "")
    if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535 or int(port) == 3000:
        raise ValueError("explicit_isolated_mapping_port_required")
    web = str(root / "web")
    if (os.environ.get("LINGXI_WEB_ROOT", web) != web
            or any(name in os.environ for name in ("LINGXI_DATA_DIR", "LINGXI_POINT_READ_ONLY"))):
        raise ValueError("packaged_mapping_web_and_owner_data_required")
    args.node, args.api_bundle = root / "node/bin/node", root / "api/main.mjs"
    args.node_sha256 = manifest["nodeBinarySha256"]
    args.api_sha256 = manifest["files"]["api/main.mjs"]["sha256"]
    previous = os.environ.get("LINGXI_WEB_ROOT")
    try:
        os.environ["LINGXI_WEB_ROOT"] = web
        return managed_mapping.run(args)
    finally:
        if previous is None:
            os.environ.pop("LINGXI_WEB_ROOT", None)
        else:
            os.environ["LINGXI_WEB_ROOT"] = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "config", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        return launch(args)
    finally:
        os.umask(previous)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Lingxi Next verified mapping runtime failed; inspect private reports.", file=sys.stderr)
        raise SystemExit(1)
