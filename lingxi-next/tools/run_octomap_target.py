"""Build and test OctoMap in a new private robot directory, without service changes."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex

from prepare_navfn import ROOT, digest
from prepare_octomap import SOURCES
from run_glim_point_target import connect, execute, private_directory, remote_digest, require_absent


def run(args):
    source, output = args.source.resolve(strict=True), args.output.absolute()
    if (digest(source / "octomap-source-manifest.json") != args.source_manifest_sha256
            or args.user == "root" or output.exists() or output.is_symlink()
            or not output.is_relative_to(ROOT / ".local")
            or output.parent.resolve(strict=True) != output.parent
            or re.fullmatch(r"octomap-[a-z0-9-]{1,48}", args.name) is None
            or re.fullmatch(r"[0-9a-f]{64}", args.navfn_sha256) is None):
        raise ValueError("verified_sources_navfn_and_new_private_octomap_run_required")
    manifest = json.loads((source / "octomap-source-manifest.json").read_bytes())
    lock = json.loads((source / "robot-navigation/octomap.lock.json").read_bytes())
    if (manifest["schemaVersion"] != 1 or manifest["purpose"] != "octomap_sources_not_navigation_acceptance"
            or manifest["octomapVersion"] != lock["version"] or set(manifest["files"]) != set(SOURCES)):
        raise ValueError("complete_octomap_source_manifest_required")
    for name, entry in manifest["files"].items():
        file = source / name
        if (file.resolve(strict=True) != file or not file.is_file() or file.stat().st_size != entry["bytes"]
                or digest(file) != entry["sha256"]):
            raise ValueError("unchanged_octomap_source_preparation_required")
    output.mkdir(mode=0o700)
    client = connect(args.host, args.user, args.known_identity)
    try:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(30)
            home = PurePosixPath(sftp.normalize("."))
            parent = home / ".cache/lingxi-next"
            uid = sftp.stat(str(home)).st_uid
            navfn = PurePosixPath(args.navfn_library)
            if uid <= 0 or not navfn.is_absolute() or ".." in navfn.parts or parent not in navfn.parents:
                raise ValueError("ordinary_user_and_existing_private_navfn_library_required")
            private_directory(sftp, parent, uid)
            if remote_digest(sftp, navfn, maximum=16 * 1024**2)["sha256"] != args.navfn_sha256:
                raise ValueError("unchanged_actual_target_navfn_library_required")
            for name, sha256 in lock["dependencies"].items():
                path = PurePosixPath("/usr/lib/aarch64-linux-gnu") / name
                if remote_digest(sftp, path, maximum=16 * 1024**2)["sha256"] != sha256:
                    raise ValueError("pinned_system_octomap_libraries_required")
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
                    raise ValueError("identical_uploaded_octomap_sources_required")
            environment = ["env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(home), "LANG=C.UTF-8",
                "PYTHONNOUSERSITE=1", "PYTHONDONTWRITEBYTECODE=1", "OMP_NUM_THREADS=1",
                "LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/opt/ros/humble/lib:/opt/ros/humble/lib/aarch64-linux-gnu"]
            operations = [("configure", ["/usr/bin/cmake", "-S", str(stage / "source/robot-navigation/native-octomap"),
                    "-B", str(stage / "build"), "-DCMAKE_BUILD_TYPE=Release"]),
                ("build", ["/usr/bin/cmake", "--build", str(stage / "build"), "--parallel", "2"])]
            for label, command in operations:
                status, data = execute(client, shlex.join(["timeout", "--signal=TERM", "--kill-after=5s", "180s",
                    "/usr/bin/nice", "-n", "10", *environment, *command]))
                (output / ("private-" + label + ".log")).write_bytes(data)
                if status:
                    raise RuntimeError("octomap_target_" + label + "_failed")
            library = stage / "build/liblingxi_octomap.so"
            native = remote_digest(sftp, library, maximum=16 * 1024**2)
            status, data = execute(client, shlex.join(["timeout", "--signal=TERM", "--kill-after=5s", "90s",
                "/usr/bin/nice", "-n", "10", *environment, "/usr/bin/python3", "-B",
                str(stage / "source/tools/check_octomap_native.py"), "--library", str(library),
                "--sha256", native["sha256"], "--lock", str(stage / "source/robot-navigation/octomap.lock.json"),
                "--navfn-library", str(navfn), "--navfn-sha256", args.navfn_sha256,
                "--output", str(stage / "report.json")]))
            (output / "private-check.log").write_bytes(data)
            sftp.get(str(stage / "report.json"), str(output / "report.json"))
            report = json.loads((output / "report.json").read_bytes())
            if (status or report["result"] != "native_synthetic_occupancy_passed" or report["machine"] != "aarch64"
                    or report["nativeSha256"] != native["sha256"] or report["loadedDependenciesSha256"] != lock["dependencies"]
                    or report["navfnSha256"] != args.navfn_sha256 or report["testsRun"] != 18
                    or any(report[key] != 0 for key in ("failures", "errors", "skipped"))
                    or any(report[key] is not False for key in ("sensorsSubscribed", "motionCommandsSent", "navigationReady"))):
                raise RuntimeError("complete_actual_arm64_octomap_synthetic_checks_required")
            sftp.get(str(library), str(output / library.name))
            if digest(output / library.name) != native["sha256"]:
                raise ValueError("identical_collected_octomap_library_required")
            collection = {"sourceManifestSha256": args.source_manifest_sha256, "nativeSha256": native["sha256"],
                "remoteWork": str(stage), "report": report, "servicesChanged": False, "vendorFilesChanged": False}
            (output / "collection.json").write_text(json.dumps(collection, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(collection))
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "user", "known-identity", "name", "source-manifest-sha256", "navfn-library", "navfn-sha256"):
        parser.add_argument("--" + name, required=True)
    for name in ("source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    previous = os.umask(0o077)
    try:
        run(args)
    finally:
        os.umask(previous)
