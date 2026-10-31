"""Build and test pinned NavFn in a new private robot directory; no service changes."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import xml.etree.ElementTree as ET

from prepare_navfn import ROOT, SOURCES, digest
from run_glim_point_target import connect, execute, private_directory, remote_digest, require_absent


def run(args):
    source = args.source.resolve(strict=True)
    output = args.output.absolute()
    if (digest(source / "navfn-source-manifest.json") != args.source_manifest_sha256
            or args.user == "root" or output.exists() or output.is_symlink()
            or not output.is_relative_to(ROOT / ".local")
            or output.parent.resolve(strict=True) != output.parent
            or re.fullmatch(r"navfn-[a-z0-9-]{1,48}", args.name) is None):
        raise ValueError("explicit_verified_source_and_new_private_navfn_target_run_required")
    manifest = json.loads((source / "navfn-source-manifest.json").read_bytes())
    lock = json.loads((source / "robot-navigation/navfn.lock.json").read_bytes())
    expected_names = {*SOURCES, *("upstream/" + name for name in lock["files"])}
    if (manifest["purpose"] != "navfn_sources_not_navigation_acceptance"
            or manifest["upstreamCommit"] != lock["commit"] or set(manifest["files"]) != expected_names):
        raise ValueError("complete_source_only_navfn_manifest_required")
    for name, entry in manifest["files"].items():
        file = source / name
        if (file.resolve(strict=True) != file or not file.is_file()
                or file.stat().st_size != entry["bytes"] or digest(file) != entry["sha256"]):
            raise ValueError("unchanged_navfn_source_preparation_required")
    output.mkdir(mode=0o700)
    client = connect(args.host, args.user, args.known_identity)
    try:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(30)
            home = PurePosixPath(sftp.normalize("."))
            parent = home / ".cache/lingxi-next"
            uid = sftp.stat(str(home)).st_uid
            if uid <= 0:
                raise ValueError("ordinary_target_user_required")
            private_directory(sftp, parent, uid)
            stage = parent / args.name
            require_absent(sftp, stage)
            sftp.mkdir(str(stage), mode=0o700)
            folders = {stage}
            for name, entry in manifest["files"].items():
                remote = stage / "source" / name
                for folder in reversed(remote.parents):
                    if folder == stage or stage in folder.parents:
                        if folder not in folders:
                            sftp.mkdir(str(folder), mode=0o700)
                            folders.add(folder)
                sftp.put(str(source / name), str(remote), confirm=True)
                sftp.chmod(str(remote), 0o600)
                if remote_digest(sftp, remote)["sha256"] != entry["sha256"]:
                    raise ValueError("identical_uploaded_navfn_source_required")
            version = ET.fromstring(sftp.open("/opt/ros/humble/share/rclcpp/package.xml").read(65536)).findtext("version")
            environment = ["env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(home), "LANG=C.UTF-8",
                "PYTHONNOUSERSITE=1", "PYTHONDONTWRITEBYTECODE=1", "CMAKE_PREFIX_PATH=/opt/ros/humble",
                "AMENT_PREFIX_PATH=/opt/ros/humble",
                "PYTHONPATH=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages",
                "LD_LIBRARY_PATH=/opt/ros/humble/lib:/opt/ros/humble/lib/aarch64-linux-gnu"]
            operations = [
                ("configure", ["/usr/bin/cmake", "-S", str(stage / "source/robot-navigation/native-navfn"),
                    "-B", str(stage / "build"), "-DCMAKE_BUILD_TYPE=Release",
                    "-DNAVFN_UPSTREAM=" + str(stage / "source/upstream"), "-DNAVFN_COMMIT=" + lock["commit"]]),
                ("build", ["/usr/bin/cmake", "--build", str(stage / "build"), "--parallel", "2"]),
            ]
            for label, command in operations:
                status, data = execute(client, shlex.join(["timeout", "--signal=TERM", "--kill-after=5s", "180s",
                                                         *environment, *command]))
                (output / ("private-" + label + ".log")).write_bytes(data)
                if status:
                    raise RuntimeError("navfn_target_" + label + "_failed")
            library = stage / "build/liblingxi_navfn.so"
            native = remote_digest(sftp, library, maximum=16 * 1024**2)
            status, data = execute(client, shlex.join(["timeout", "--signal=TERM", "--kill-after=5s", "90s",
                *environment, "/usr/bin/python3", "-B", str(stage / "source/tools/check_navfn_native.py"),
                "--library", str(library), "--sha256", native["sha256"], "--output", str(stage / "report.json")]))
            (output / "private-check.log").write_bytes(data)
            sftp.get(str(stage / "report.json"), str(output / "report.json"))
            report = json.loads((output / "report.json").read_bytes())
            if (status or report["result"] != "native_synthetic_planning_passed" or report["machine"] != "aarch64"
                    or report["nativeSha256"] != native["sha256"] or report["upstreamCommit"] != lock["commit"]
                    or report["skipped"] != 0 or report["testsRun"] != 13
                    or any(report[key] is not False for key in ("sensorsSubscribed", "motionCommandsSent", "navigationReady"))):
                raise RuntimeError("complete_actual_arm64_navfn_synthetic_checks_required")
            sftp.get(str(library), str(output / library.name))
            if digest(output / library.name) != native["sha256"]:
                raise ValueError("same_collected_navfn_library_required")
            collection = {"sourceManifestSha256": args.source_manifest_sha256, "nativeSha256": native["sha256"],
                          "rclcppVersion": version, "remoteWork": str(stage), "report": report,
                          "servicesChanged": False, "vendorFilesChanged": False}
            (output / "collection.json").write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(collection))
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity", "name", "source-manifest-sha256"):
        parser.add_argument("--" + name, required=True)
    for name in ("source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    old = os.umask(0o077)
    try:
        run(args)
    finally:
        os.umask(old)
