"""Upload a synthetic-only trial to a new user directory and run a bounded probe."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import time

from slam_target_probe import sha256, verify_bundle


def matches_identity(host_keys, identity, key):
    known = host_keys.lookup(identity)
    return known is not None and known.get(key.get_name()) == key


def connect(host, user, identity):
    import paramiko

    class SameKnownRobot(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            if not matches_identity(client.get_host_keys(), identity, key):
                raise paramiko.SSHException("robot_host_identity_does_not_match_known_robot")

    client = paramiko.SSHClient()
    client.load_host_keys(str(Path.home() / ".ssh/known_hosts"))
    client.set_missing_host_key_policy(SameKnownRobot())
    password = getpass.getpass("Robot SSH password (memory only): ")
    try:
        client.connect(host, username=user, password=password, allow_agent=False, look_for_keys=False,
                       timeout=8, auth_timeout=12, banner_timeout=12)
        if not matches_identity(client.get_host_keys(), identity,
                                client.get_transport().get_remote_server_key()):
            raise paramiko.SSHException("connected_robot_does_not_match_explicit_identity")
        return client
    except BaseException:
        client.close()
        raise
    finally:
        password = None


def target_directory(home, name):
    if not re.fullmatch(r"lingxi-next-validation-arm64-[a-z0-9-]+", name):
        raise ValueError("explicit_new_validation_directory_name_required")
    root = PurePosixPath(home)
    if not root.is_absolute() or ".." in root.parts or str(root) in ("/", "/root"):
        raise ValueError("ordinary_user_home_required")
    return root / name


def probe_command(remote):
    return shlex.join([
        "timeout", "--signal=TERM", "--kill-after=5s", "120s",
        "env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(remote.parent), "LANG=C.UTF-8",
        "LD_LIBRARY_PATH=" + str(remote / "lib"), "OMP_NUM_THREADS=2", "OPENBLAS_NUM_THREADS=2",
        "python3", str(remote / "slam_target_probe.py"), "--root", str(remote),
    ])


def run(args):
    bundle = args.bundle.resolve(strict=True)
    manifest = verify_bundle(bundle)
    if args.output.exists():
        raise ValueError("existing_target_report_is_preserved")
    if args.user == "root":
        raise ValueError("ordinary_robot_user_required")
    client = connect(args.host, args.user, args.known_identity)
    report = {"purpose": "isolated_arm64_robot_loading_trial", "result": "running",
              "bundleManifestSha256": sha256(bundle / "bundle-manifest.json"),
              "sourceCommits": manifest["sourceCommits"]}
    try:
        with client.open_sftp() as sftp:
            remote = target_directory(sftp.normalize("."), args.directory)
            sftp.mkdir(str(remote), mode=0o700)  # Existing directories are never reused.
            names = sorted(manifest["files"]) + ["bundle-manifest.json"]
            directories = sorted({
                str((remote / name).parent) for name in names if (remote / name).parent != remote
            })
            for directory in directories:
                sftp.mkdir(directory, mode=0o700)
            for name in names:
                sftp.put(str(bundle / name), str(remote / name), confirm=True)
                mode = 0o700 if manifest["files"].get(name, {}).get("executable") else 0o600
                sftp.chmod(str(remote / name), mode)
            report["uploadedFiles"] = len(names)
            print(json.dumps({"phase": "synthetic_bundle_uploaded", "files": len(names)}), flush=True)
            channel = client.get_transport().open_session(timeout=8)
            try:
                channel.exec_command(probe_command(remote))
                output, errors = bytearray(), bytearray()
                deadline = time.monotonic() + 140
                while True:
                    for ready, receive, data in (
                        (channel.recv_ready, channel.recv, output),
                        (channel.recv_stderr_ready, channel.recv_stderr, errors),
                    ):
                        if ready():
                            block = receive(65536)
                            if len(data) + len(block) > 1024 * 1024:
                                raise RuntimeError("unexpected_probe_output_volume")
                            data.extend(block)
                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("bounded_remote_probe_did_not_return")
                    time.sleep(0.05)
                status = channel.recv_exit_status()
            finally:
                channel.close()
            report["probeExitCode"] = status
            report["privateProbeOutputSha256"] = hashlib.sha256(output + errors).hexdigest()
            if status:
                report["privateProbeOutput"] = (output + errors).decode("utf-8", errors="replace")
                raise RuntimeError("target_probe_failed")
            target = json.loads(output.decode())
            if (target.get("result") != "arm64_modules_loaded_and_synthetic_program_executed"
                    or target["bundleManifestSha256"] != report["bundleManifestSha256"]
                    or not target["checks"] or any(value is not True for value in target["checks"].values())
                    or any(value is not False for value in target["boundaries"].values())):
                raise ValueError("target_probe_contract_mismatch")
            report["targetProbe"] = target
            report["result"] = "target_cpu_loading_and_synthetic_execution_passed_not_slam_acceptance"
    except BaseException as error:
        report.update(result="failed_or_interrupted", errorType=type(error).__name__)
        raise
    finally:
        client.close()
        args.output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
        args.output.chmod(0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity", "directory"):
        parser.add_argument("--" + name, required=True)
    for name in ("bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2))


if __name__ == "__main__":
    main()
