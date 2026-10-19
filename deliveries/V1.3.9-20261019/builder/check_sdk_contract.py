"""Verify motion expectations against documents read from the pinned SDK archive."""
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import build_delivery as build

SDK = build.BASE_OUTER / "components/aimdk-aarch64-1bde262f-artifacts.7z"
PREFIX = "aimdk-aarch64-1bde262f-artifacts/aimdk-aarch64-1bde262f-artifacts/"
PATHS = {
    "pairs": "docs/cn/_build/html/zh-cn/dev/Interface/control_mod/preset_motion.html",
    "enum": "src/aimdk_msgs/prebuilt_aarch64/share/aimdk_msgs/msg/McPresetMotion.msg",
    "service": "src/aimdk_msgs/prebuilt_aarch64/share/aimdk_msgs/srv/SetMcPresetMotion.srv",
}


class PairTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = self.cell = False
        self.rows, self.row, self.text = [], [], ""

    def handle_starttag(self, tag, attrs):
        if tag == "table" and dict(attrs).get("id") == "tbl-preset-motion":
            self.active = True
        if self.active and tag == "tr":
            self.row = []
        if self.active and tag == "td":
            self.cell, self.text = True, ""

    def handle_data(self, text):
        if self.cell:
            self.text += text

    def handle_endtag(self, tag):
        if self.active and tag == "td":
            self.row.append(self.text.strip())
            self.cell = False
        if self.active and tag == "tr" and self.row:
            self.rows.append(self.row)
        if tag == "table":
            self.active = False


def run():
    if build.sha(SDK) != "f13147a175bc3fb621fb118698aae0353c097adda38bba640f9a39c01433cc98":
        raise RuntimeError("SDK archive is not the pinned release")
    # Windows bsdtar reads 7z directly. Linux may supply it as bsdtar.
    tar = shutil.which("tar") if os.name == "nt" else shutil.which("bsdtar")
    if not tar:
        raise RuntimeError("bsdtar with 7z support is required for this archive check")
    data = {key: subprocess.run([tar, "-xOf", str(SDK), PREFIX + path], capture_output=True,
                               check=True, timeout=120).stdout for key, path in PATHS.items()}
    parser = PairTable()
    parser.feed(data["pairs"].decode("utf-8"))
    actual = {(int(row[1]), int(row[2])) for row in parser.rows}
    sys.path.insert(0, str(build.SOURCE / "navigation"))
    expected = runpy.run_path(str(build.SOURCE / "navigation/test/test_motion_wire_contract.py"))["DOCUMENTED"]
    for name, (motion, areas) in expected.items():
        if not all((motion, area) in actual for area in areas):
            raise RuntimeError("Motion differs from current vendor pair table: " + name)
    assert b"INTERACTION_SWEATHEART = 3004" in data["enum"]
    assert b"input_source" not in data["service"]
    build.write(build.DELIVERY / "sdk-contract.json", {
        "passed": True, "sdk_archive_sha256": build.sha(SDK),
        "vendor_sources": {PATHS[key]: hashlib.sha256(value).hexdigest() for key, value in data.items()},
        "offered_motion_types": len(expected), "vendor_pairs_checked": sum(len(areas) for _, areas in expected.values()),
        "stale_generated_sweetheart_enum": 3004, "current_documented_sweetheart_id": 1007,
        "pinned_service_has_input_source": False, "physical_motion_verified": False,
    })
    print("SDK_CONTRACT_PASS motions=" + str(len(expected)) + " pinned archive documents verified")


if __name__ == "__main__":
    run()
