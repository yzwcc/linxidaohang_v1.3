"""Read one SDK-documented factory file over identity-checked, read-only SFTP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from run_slam_target_trial import connect
from verify_slam_capture import sha256


ROOT = Path(__file__).resolve().parents[1]
FACTORY_PATH = "/agibot/data/param/protected/factory_calib/factory_calibration_params.yml"
MAXIMUM_BYTES = 2 * 1024 * 1024


def read_snapshot(sftp):
    """Two complete reads reject changing input; never follow a file symlink."""
    before = sftp.lstat(FACTORY_PATH)
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAXIMUM_BYTES:
        raise ValueError("bounded_regular_factory_file_required")
    copies = []
    for _ in range(2):
        with sftp.open(FACTORY_PATH, "rb") as source:
            copies.append(source.read(MAXIMUM_BYTES + 1))
        current = sftp.lstat(FACTORY_PATH)
        if (not stat.S_ISREG(current.st_mode) or current.st_size != before.st_size
                or current.st_mtime != before.st_mtime or len(copies[-1]) != before.st_size):
            raise ValueError("factory_file_changed_during_read")
    if copies[0] != copies[1]:
        raise ValueError("factory_file_content_changed_during_read")
    return copies[0]


def run(args):
    output = args.output.resolve()
    if (output.exists() or not output.is_relative_to((ROOT / ".local").resolve())
            or args.user == "root"):
        raise ValueError("new_private_output_and_ordinary_robot_user_required")
    output.mkdir(mode=0o700, parents=True)
    report = {
        "schemaVersion": 1, "purpose": "private_factory_file_capture_not_calibration_acceptance",
        "result": "running", "readerSourceSha256": sha256(__file__),
        "boundaries": {key: False for key in (
            "robotFilesWritten", "permissionsChanged", "rosNodeStarted", "motionCommandsSent",
            "modeOrInputSourceChanged", "deploymentChanged", "calibrationValidated", "rawDataPublished",
        )},
    }
    client = None
    try:
        client = connect(args.host, args.user, args.known_identity)
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(10.)
            data = read_snapshot(sftp)
        destination = output / "private-factory-calibration.yml"
        with destination.open("xb") as stream:
            stream.write(data)
        destination.chmod(0o600)
        report.update(result="two_identical_read_only_factory_copies_captured_not_calibration_acceptance",
                      privateFileSha256=hashlib.sha256(data).hexdigest(), privateFileBytes=len(data))
    except BaseException as error:
        report.update(result="failed_or_unavailable", errorType=type(error).__name__)
        raise
    finally:
        if client is not None:
            client.close()
        with (output / "report.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        print(json.dumps({"result": run(args)["result"]}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
