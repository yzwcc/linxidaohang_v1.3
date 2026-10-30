"""Prepare pinned wheels, or install them offline in a NEW ordinary-user venv."""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "robot-slam/runtime-arm64-cp310.lock.json"
PINNED = {"numpy": "1.26.4", "scipy": "1.11.4", "small-gicp": "1.0.1",
          "PyYAML": "6.0.2", "json5": "0.12.1"}


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def load_lock(path=LOCK):
    value = json.loads(path.read_text(encoding="utf-8"))
    if (set(value) != {"schemaVersion", "platform", "packages"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or value["platform"] != "linux-aarch64-cp310"
            or type(value["packages"]) is not list or len(value["packages"]) != len(PINNED)):
        raise ValueError("complete_arm64_cp310_wheel_lock_required")
    seen = set()
    for entry in value["packages"]:
        if (type(entry) is not dict or set(entry) != {"name", "version", "filename", "bytes", "sha256", "url"}
                or entry["name"] not in PINNED or entry["name"] in seen
                or entry["version"] != PINNED[entry["name"]]
                or type(entry["bytes"]) is not int or not 0 < entry["bytes"] < 64 * 1024**2
                or type(entry["sha256"]) is not str or not re.fullmatch("[a-f0-9]{64}", entry["sha256"])
                or type(entry["filename"]) is not str
                or not re.fullmatch(r"[A-Za-z0-9_.-]+\.whl", entry["filename"])):
            raise ValueError("fixed_unique_hashed_runtime_wheels_required")
        url = urlsplit(entry["url"])
        if (url.scheme != "https" or url.netloc != "files.pythonhosted.org" or url.query or url.fragment
                or not url.path.startswith("/packages/") or url.path.rsplit("/", 1)[-1] != entry["filename"]):
            raise ValueError("pinned_pypi_wheel_url_required")
        seen.add(entry["name"])
    return value


def requirements(lock):
    return "".join(entry["name"] + "==" + entry["version"] + " --hash=sha256:" + entry["sha256"] + "\n"
                   for entry in lock["packages"])


def verify_wheelhouse(directory, lock):
    if directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
        raise ValueError("direct_wheelhouse_required")
    expected = {entry["filename"] for entry in lock["packages"]} | {"requirements.txt"}
    if {path.name for path in directory.iterdir()} != expected:
        raise ValueError("exact_offline_wheel_inventory_required")
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError("ordinary_wheelhouse_files_required")
    if (directory / "requirements.txt").read_text(encoding="utf-8") != requirements(lock):
        raise ValueError("same_hash_pinned_requirements_required")
    for entry in lock["packages"]:
        path = directory / entry["filename"]
        if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            raise ValueError("runtime_wheel_digest_or_size_mismatch")


def new_directory(path):
    if (not path.is_absolute() or path.resolve() != path or path.exists() or path.is_symlink()
            or not path.parent.is_dir()):
        raise ValueError("new_direct_absolute_runtime_directory_required")


def download(directory):
    lock = load_lock()
    new_directory(directory)
    directory.mkdir(mode=0o700)
    for entry in lock["packages"]:
        partial = directory / (entry["filename"] + ".partial")
        with urlopen(entry["url"], timeout=60) as response, partial.open("xb") as output:
            if urlsplit(response.url).netloc != "files.pythonhosted.org":
                raise ValueError("same_pypi_download_host_required")
            remaining = entry["bytes"] + 1
            while remaining:
                block = response.read(min(65536, remaining))
                if not block:
                    break
                output.write(block)
                remaining -= len(block)
        if partial.stat().st_size != entry["bytes"] or sha256(partial) != entry["sha256"]:
            raise ValueError("downloaded_runtime_wheel_integrity_mismatch")
        partial.rename(directory / entry["filename"])
    (directory / "requirements.txt").write_text(requirements(lock), encoding="utf-8", newline="\n")
    verify_wheelhouse(directory, lock)
    return {"result": "verified_offline_wheelhouse", "lockSha256": sha256(LOCK),
            "packages": PINNED, "wheelBytes": sum(entry["bytes"] for entry in lock["packages"])}


def isolated_venv(directory):
    config = configparser.ConfigParser()
    config.read_string("[venv]\n" + (directory / "pyvenv.cfg").read_text(encoding="utf-8"))
    if config.getboolean("venv", "include-system-site-packages") is not False:
        raise ValueError("runtime_must_not_inherit_system_site_packages")


def install(wheelhouse, directory):
    if (sys.platform != "linux" or platform.machine() != "aarch64" or sys.version_info[:2] != (3, 10)
            or os.geteuid() == 0 or sys.prefix != sys.base_prefix):
        raise ValueError("ordinary_arm64_system_python310_installer_required")
    new_directory(directory)
    home = Path.home().resolve(strict=True)
    if (not directory.is_relative_to(home) or wheelhouse == directory
            or directory in wheelhouse.parents or wheelhouse in directory.parents):
        raise ValueError("separate_new_user_home_runtime_required")
    lock = load_lock()
    verify_wheelhouse(wheelhouse, lock)
    directory.mkdir(mode=0o700)
    target = directory / "env"
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(home),
                   "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    # Use a clean child for ensurepip as well, never the caller's PYTHONPATH/LD_PRELOAD.
    subprocess.run([sys.executable, "-I", "-B", "-m", "venv", str(target)], env=environment,
                   check=True, timeout=90)
    isolated_venv(target)
    python = target / "bin/python"
    subprocess.run([str(python), "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
                    "install", "--no-index", "--no-cache-dir", "--only-binary=:all:", "--no-deps",
                    "--require-hashes", "--find-links", str(wheelhouse), "-r", str(wheelhouse / "requirements.txt")],
                   env=environment, check=True, timeout=180)
    subprocess.run([str(python), "-I", "-B", "-m", "pip", "--isolated", "check"],
                   env=environment, check=True, timeout=30)
    verify_wheelhouse(wheelhouse, lock)
    report = {"result": "installed_isolated_arm64_python_runtime", "lockSha256": sha256(LOCK),
              "machine": platform.machine(), "python": platform.python_version(), "packages": PINNED,
              "systemSitePackages": False, "targetNetworkRequired": False,
              "vendorPackagesChanged": False, "runtimeCompatibilityAccepted": False}
    (directory / "install-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("download").add_argument("--output", type=Path, required=True)
    target = commands.add_parser("install")
    target.add_argument("--wheelhouse", type=Path, required=True)
    target.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        result = download(args.output) if args.command == "download" else install(args.wheelhouse, args.output)
        print(json.dumps(result, allow_nan=False))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
