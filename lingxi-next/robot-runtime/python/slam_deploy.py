"""Install or roll back the shared commissioning workspace as an ordinary-user service."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_bundle as bundle

SERVICE = "lingxi-next-slam.service"
RUN_ID_LENGTH = 12


def run_path(root):
    return root / "runs" / uuid.uuid4().hex[:RUN_ID_LENGTH]


def target():
    if sys.platform != "linux" or platform.machine() != "aarch64" or os.geteuid() == 0:
        raise ValueError("ordinary_user_arm64_slam_installation_required")


def private_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("direct_absolute_private_deployment_directory_required")
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("owned_private_deployment_directory_required")
    return path


def external_file(path, root):
    path = Path(path)
    if (not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file()
            or path.is_relative_to(root)):
        raise ValueError("direct_external_deployment_input_required")
    return path


@contextmanager
def locked(root):
    import fcntl
    target()
    root = Path(root)
    if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(root))
            or any(len(os.fsencode(path)) >= 100 for path in (
                root / "maps" / ("a" * 32 + ".points.sock"),
                root / "runs" / ("a" * RUN_ID_LENGTH) / "r" / ("a" * 32) / "localization.sock"))):
        raise ValueError("short_direct_slam_installation_root_required")
    private_directory(root)
    lock = root / ".install.lock"
    if lock.exists() or lock.is_symlink():
        private_file(lock)
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name in ("releases", "records", "maps", "runs"):
            private_directory(root / name)
        yield root


def save(path, value):
    temporary = path.with_name(".new-" + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def pointer(root, name):
    path = root / name
    if not path.is_symlink():
        if path.exists():
            raise ValueError("managed_release_pointer_required")
        return None
    destination = path.resolve(strict=True)
    if destination.parent != root / "releases" or not bundle.digest(destination.name):
        raise ValueError("managed_release_pointer_required")
    return destination.name


def private_file(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise ValueError("owned_private_deployment_file_required")
    return path


def set_pointer(root, name, release):
    temporary = root / (".pointer-" + uuid.uuid4().hex)
    temporary.symlink_to(Path("releases") / release)
    os.replace(temporary, root / name)


def unit(root):
    return ("[Unit]\nDescription=Lingxi shared SLAM commissioning workspace\n\n[Service]\n"
            "Type=notify\nNotifyAccess=main\nUMask=0077\nRestart=no\nNoNewPrivileges=true\n"
            "KillMode=control-group\nTimeoutStartSec=60\nTimeoutStopSec=240\n"
            f"WorkingDirectory={root}\nExecStart=/usr/bin/python3 -I -B "
            f"{root}/current/source/robot-runtime/python/slam_deploy.py run --root {root}\n"
            "\n[Install]\nWantedBy=default.target\n")


def systemctl(*arguments):
    subprocess.run(["/usr/bin/systemctl", "--user", *arguments], check=True, timeout=300,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def environment(root, record):
    code = private_file(root / "pairing-code.txt").read_text(encoding="utf-8").strip()
    if hashlib.sha256(code.encode()).hexdigest() != record["pairingCodeSha256"]:
        raise ValueError("matching_private_pairing_code_required")
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1", "LINGXI_HOST": record["host"], "LINGXI_PORT": str(record["port"]),
            "LINGXI_TLS_KEY": record["tlsKey"], "LINGXI_TLS_CERT": record["tlsCert"],
            "LINGXI_PAIRING_CODE_SHA256": record["pairingCodeSha256"]}


def command(root, release, record, output, *, check=False):
    destination = root / "releases" / release
    if record["manifestSha256"] != release:
        raise ValueError("matching_deployment_record_required")
    bundle.verify_bundle(destination, release, project=destination / "source", profile=bundle.SLAM)
    config = external_file(record["config"], root)
    if bundle.sha256(config) != record["configSha256"]:
        raise ValueError("unchanged_external_native_configuration_required")
    for name in ("tlsKey", "tlsCert", "python"):
        path = Path(record[name])
        # A virtualenv interpreter may legitimately be a symlink.
        if name == "python":
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError("explicit_external_python_environment_required")
        else:
            external_file(path, root)
    arguments = [record["python"], "-I", "-B", str(destination / "source/robot-runtime/python/launch_slam_bundle.py"),
                 "--bundle", str(destination), "--manifest-sha256", release, "--config", str(config),
                 "--maps", str(root / "maps"), "--output", str(output)]
    return [*arguments, "--check-only"] if check else arguments


def preflight(root, release, record):
    arguments = command(root, release, record, run_path(root), check=True)
    subprocess.run(arguments, env=environment(root, record), check=True, timeout=90,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def read_record(root, release):
    return json.loads(private_file(root / "records" / (release + ".json")).read_text(encoding="utf-8"))


def switch(root, release, *, start):
    record = read_record(root, release)
    preflight(root, release, record)
    current = pointer(root, "current")
    pointer(root, "previous")
    if current:
        systemctl("stop", SERVICE)
    set_pointer(root, "current", release)
    starting = False
    try:
        systemctl("link", str(root / SERVICE))
        systemctl("daemon-reload")
        if start:
            starting = True
            systemctl("start", SERVICE)
    except BaseException:
        # Retain both releases and maps even if activation fails.
        try:
            if starting:
                systemctl("stop", SERVICE)
        finally:
            if current:
                set_pointer(root, "current", current)
            else:
                (root / "current").unlink()
        raise
    if current and current != release:
        set_pointer(root, "previous", current)
    return {"result": "service_ready" if start else "installed_stopped", "release": release,
            "previous": pointer(root, "previous"), "mapsPreserved": True, "productionAccepted": False}


def install(args):
    with locked(args.root) as root:
        source = args.bundle.resolve(strict=True)
        release = args.manifest_sha256
        bundle.verify_bundle(source, release, project=source / "source", profile=bundle.SLAM)
        if source == root or source.is_relative_to(root) or root.is_relative_to(source):
            raise ValueError("separate_extracted_bundle_and_installation_required")
        if type(args.port) is not int or not 1 <= args.port <= 65535 or args.port == 3000:
            raise ValueError("isolated_slam_deployment_port_required")
        if args.host not in ("127.0.0.1", "0.0.0.0"):
            raise ValueError("explicit_loopback_or_lan_listen_address_required")
        record = {"manifestSha256": release, "config": str(external_file(args.config, root)),
                  "python": str(args.python), "tlsKey": str(external_file(args.tls_key, root)),
                  "tlsCert": str(external_file(args.tls_cert, root)), "host": args.host, "port": args.port}
        record["configSha256"] = bundle.sha256(Path(record["config"]))
        code_file = root / "pairing-code.txt"
        if code_file.exists() or code_file.is_symlink():
            private_file(code_file)
        if not code_file.exists():
            descriptor = os.open(code_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(secrets.token_urlsafe(24) + "\n")
        record["pairingCodeSha256"] = hashlib.sha256(code_file.read_text(encoding="utf-8").strip().encode()).hexdigest()
        destination = root / "releases" / release
        if destination.exists():
            bundle.verify_bundle(destination, release, project=destination / "source", profile=bundle.SLAM)
        else:
            staging = root / "releases" / (".stage-" + uuid.uuid4().hex)
            shutil.copytree(source, staging, symlinks=False)
            bundle.verify_bundle(staging, release, project=staging / "source", profile=bundle.SLAM)
            staging.rename(destination)
        record_file = root / "records" / (release + ".json")
        if record_file.exists() and read_record(root, release) != record:
            raise ValueError("release_configuration_is_immutable_required")
        save(record_file, record)
        unit_file = root / SERVICE
        expected = unit(root)
        if unit_file.exists() or unit_file.is_symlink():
            private_file(unit_file)
        if unit_file.exists() and unit_file.read_text(encoding="utf-8") != expected:
            raise ValueError("existing_managed_unit_must_match_required")
        unit_file.write_text(expected, encoding="utf-8")
        unit_file.chmod(0o600)
        return switch(root, release, start=args.start)


def rollback(args):
    with locked(args.root) as root:
        previous = pointer(root, "previous")
        if not previous:
            raise ValueError("previous_verified_slam_release_required")
        return switch(root, previous, start=args.start)


def run(args):
    target()
    root = private_directory(args.root)
    release = pointer(root, "current")
    if not release:
        raise ValueError("installed_slam_release_required")
    record = read_record(root, release)
    output = run_path(root)
    arguments = command(root, release, record, output)
    env = environment(root, record)
    if "NOTIFY_SOCKET" in os.environ:
        env["NOTIFY_SOCKET"] = os.environ["NOTIFY_SOCKET"]
    os.execve(record["python"], arguments, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "rollback", "run"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--start", action="store_true")
    for name in ("bundle", "config", "python", "tls-key", "tls-cert"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3443)
    args = parser.parse_args()
    if args.command == "install" and any(getattr(args, name) is None for name in (
            "bundle", "config", "python", "tls_key", "tls_cert", "manifest_sha256")):
        raise ValueError("explicit_verified_slam_installation_inputs_required")
    previous = os.umask(0o077)
    try:
        result = {"install": install, "rollback": rollback, "run": run}[args.command](args)
        print(json.dumps(result), flush=True)
        return 0
    finally:
        os.umask(previous)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Lingxi Next SLAM deployment failed; existing maps and release files were retained.", file=sys.stderr)
        raise SystemExit(1)
