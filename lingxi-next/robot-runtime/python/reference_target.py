"""Read build and per-device motion-review inputs without opening any ROS interfaces."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat


FIRMWARE_PATH = Path("/agibot/software/firmware_version")
AIMDK_PATH = Path("/agibot/software/aimdk/version")
MACHINE_ID_PATH = Path("/etc/machine-id")
FIRMWARE = "release-lx2501_3_t2d5-soc1-v1.1.5"
MAP_FILES = frozenset(("metadata.json", "map.yaml", "map.pgm", "localization.ply", "localization.json"))
CONFIG_LIMIT = 128 * 1024


def protected_bytes(path, limit):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("canonical_motion_review_input_required")
    # Refuse symlinks and special files even if the configuration changes
    # between the canonical path check and the open.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode) or status.st_uid != 0 or status.st_mode & 0o022:
            raise ValueError("root_owned_read_only_motion_review_input_required")
        content = stream.read(limit + 1)
        final = os.fstat(stream.fileno())
        if (not 0 < len(content) <= limit or len(content) != status.st_size
                or (status.st_size, status.st_mtime_ns, status.st_ctime_ns)
                != (final.st_size, final.st_mtime_ns, final.st_ctime_ns)):
            raise ValueError("invalid_or_changing_motion_review_input")
    return content


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def target_fields():
    # The vendor may point the installed software path at a release directory.
    # Trust only the fixed local record names, not a user-provided version text.
    firmware = protected_bytes(FIRMWARE_PATH.resolve(strict=True), 256).decode("ascii").strip()
    aimdk = protected_bytes(AIMDK_PATH.resolve(strict=True), 256).decode("ascii").strip().removeprefix("aimdk ")
    machine = protected_bytes(MACHINE_ID_PATH, 128).strip()
    if (firmware != FIRMWARE or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-.][a-zA-Z0-9]+)*", aimdk)
            or not re.fullmatch(rb"[0-9a-f]{32}", machine) or machine == b"0" * 32):
        raise ValueError("unsupported_or_invalid_installed_x2_identity")
    return {"firmware": firmware, "aimdk": aimdk,
            "targetIdentitySha256": sha256(b"lingxi-x2-motion-review-v1\x00" + machine)}


def review_fields(config, fastlio):
    return {"schemaVersion": 1, **target_fields(),
            "configSha256": sha256(protected_bytes(config, CONFIG_LIMIT)),
            "fastlioConfigSha256": sha256(protected_bytes(fastlio, CONFIG_LIMIT))}


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_motion_review_field")
        result[key] = value
    return result


def load_motion_review(path, config, fastlio):
    fields = review_fields(config, fastlio)
    raw = protected_bytes(path, 1024 * 1024)
    review = json.loads(raw, object_pairs_hook=unique_keys)
    if (not isinstance(review, dict) or review.keys() != fields.keys() | {"maps"}
            or type(review["schemaVersion"]) is not int or any(review[k] != v for k, v in fields.items())):
        raise ValueError("motion_review_target_or_configuration_mismatch")
    maps = review["maps"]
    if not isinstance(maps, dict) or not 1 <= len(maps) <= 256:
        raise ValueError("motion_review_requires_reviewed_maps")
    for identity, assets in maps.items():
        if (not re.fullmatch(r"fastlio-[0-9a-f]{32}", identity) or not isinstance(assets, dict)
                or assets.keys() != MAP_FILES or any(not isinstance(digest, str)
                                                     or not re.fullmatch("[0-9a-f]{64}", digest) for digest in assets.values())):
            raise ValueError("invalid_motion_review_map_digests")
    return MotionReview(Path(path), sha256(raw), Path(config), Path(fastlio), fields, maps)


class MotionReview:
    def __init__(self, path, digest, config, fastlio, fields, maps):
        self.path = path
        self.digest = digest
        self.config = config
        self.fastlio = fastlio
        self.fields = fields
        self.maps = maps

    def matches_current_target(self):
        try:
            return (review_fields(self.config, self.fastlio) == self.fields
                    and sha256(protected_bytes(self.path, 1024 * 1024)) == self.digest)
        except (OSError, ValueError):
            return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--fastlio-config", required=True, type=Path)
    args = parser.parse_args()
    try:
        fields = review_fields(args.config, args.fastlio_config)
    except (OSError, ValueError):
        parser.exit(2, "X2 review inputs are missing, invalid, changed, or not administrator-controlled\n")
    # This intentionally is not a motion review: only a human can add map
    # review evidence after the target's supervised checks.
    print(json.dumps(fields, indent=2))


if __name__ == "__main__":
    main()
