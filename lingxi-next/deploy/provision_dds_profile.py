"""Copy a pinned vendor DDS profile into the product, without changing its source."""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import stat


def profile_bytes(path, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("pinned_dds_profile_digest_required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 1024**2:
            raise ValueError("bounded_regular_dds_profile_required")
        data = stream.read(1024**2 + 1)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("verified_vendor_dds_profile_bytes_required")
    return data


def provision(source, destination, expected, gid):
    data = profile_bytes(source, expected)
    parent = destination.parent
    if (not parent.is_absolute() or parent.resolve(strict=True) != parent
            or not parent.is_dir() or parent.stat().st_uid != os.geteuid()
            or parent.stat().st_mode & 0o027 or source.resolve() == destination):
        raise ValueError("private_owned_product_dds_directory_required")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    except FileExistsError:
        profile_bytes(destination, expected)
        info = destination.lstat()
        if info.st_uid != os.geteuid() or info.st_gid != gid or stat.S_IMODE(info.st_mode) != 0o640:
            raise ValueError("existing_product_dds_permissions_mismatch")
    else:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchown(stream.fileno(), os.geteuid(), gid)
            os.fchmod(stream.fileno(), 0o640)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    if profile_bytes(source, expected) != profile_bytes(destination, expected):
        raise ValueError("unchanged_source_and_product_dds_profile_required")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("root_installation_required")
    expected = json.loads(args.evidence.read_text(encoding="utf-8"))["runs"]["start"]["ddsProfileSha256"]
    provision(Path("/agibot/software/entry/cfg/ros_dds_configuration.xml"),
              Path("/etc/lingxi-next/dds/ros_dds_configuration.xml"), expected,
              grp.getgrnam("lingxi-next").gr_gid)
    print("Product DDS profile matches verified vendor bytes; vendor permissions unchanged.")


if __name__ == "__main__":
    main()
