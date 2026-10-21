"""Build the r18-compatible cumulative voice-control silent-reply package."""
from __future__ import annotations

import json
import io
from pathlib import Path
import sys
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "v138_complete_r15_deployment"))
from archive_utils import digest, json_data, normalize_text, put, read_archive
from archive_utils import tar_data, verify_sums, write_sums

BASE = ROOT / "v138_tour_start_reply_fix_deployment" / "dist"
BASE_NAME = "sparkrobot-lingxi-v1.3.8-tour-start-reply-fix-1.0.9-20261019"
BASE_SHA256 = "0f986edb06e3cfd2bfce8809893f85a41684ce9114a01fd0f7d2ef50dcef138e"
NAME = "sparkrobot-lingxi-v1.3.8-voice-control-silent-fix-1.0.10-20261021"
VERSION = "1.0.10-20261021"
R18 = "1.0.19-20261019-r18"
PACKAGE_DIR = HERE / (BASE_NAME)
BRIDGE = "navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"
BRIDGE_SOURCE = "source/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"
BRIDGE_TEST = "source/navigation/test/test_silent_control_receipts.py"
NEAR_GOAL = "navigation/robot_local_navigation_v3/near_goal_arrival.py"
NEAR_GOAL_SOURCE = "source/navigation/robot_local_navigation_v3/near_goal_arrival.py"
R19_ARCHIVE = ROOT / "v138_complete_r19_deployment" / "dist" / (
    "lingxi-v1.3.8-complete-20261021-r19-deployment.tar.gz"
)


def main() -> None:
    source_archive = BASE / (BASE_NAME + ".tar.gz")
    raw = source_archive.read_bytes()
    if digest(raw) != BASE_SHA256:
        raise RuntimeError("1.0.9 cumulative base archive hash mismatch")
    files = read_archive(raw, BASE_NAME)
    verify_sums(files, "SHA256SUMS")
    meta = json.loads(files["release.json"][1])
    if meta.get("version") != "1.0.9-20261019":
        raise RuntimeError("Unexpected cumulative base version")
    if meta.get("patched_jar_sha256") != "61055b65c24e086d2fe9f4459e3050d893dee4ed2c8a20abf54b8a0f6511fdf2":
        raise RuntimeError("Unexpected cumulative Java payload hash")

    r19_raw = R19_ARCHIVE.read_bytes()
    r19_root = "lingxi-v1.3.8-complete-20261021-r19-deployment"
    with tarfile.open(fileobj=io.BytesIO(r19_raw), mode="r:gz") as archive:
        component_name = next(
            name for name in archive.getnames()
            if name.startswith(r19_root + "/components/")
            and name.endswith("-1.0.19-20261019-r18.tar.gz")
        )
        component_raw = archive.extractfile(component_name).read()
    r18_files = read_archive(
        component_raw,
        "sparkrobot-lingxi-nav-internal-1.0.19-20261019-r18",
    )
    r18_jar = r18_files["payload/java/robot-local-navigation-java-all.jar"][1]
    if digest(r18_jar) not in meta["superseded_jar_sha256"]:
        raise RuntimeError("The r18 JAR is not an accepted cumulative baseline")
    for relative in set(meta["python_baselines"]) - {NEAR_GOAL}:
        actual = digest(r18_files["payload/" + relative][1])
        accepted = set(meta["python_accepted_sha256"][relative])
        if actual not in accepted and actual != meta["payload_sha256"][relative]:
            raise RuntimeError("r18 Python baseline is not accepted: " + relative)
    near_goal = r18_files["source/" + NEAR_GOAL][1]

    bridge_path = PACKAGE_DIR / BRIDGE_SOURCE
    test_path = PACKAGE_DIR / BRIDGE_TEST
    bridge = normalize_text(bridge_path.name, bridge_path.read_bytes())
    bridge_test = normalize_text(test_path.name, test_path.read_bytes())
    compile(bridge, BRIDGE, "exec")
    compile(bridge_test, BRIDGE_TEST, "exec")

    files["payload/" + BRIDGE] = (
        files["payload/" + BRIDGE][0],
        bridge,
    )
    files[BRIDGE_SOURCE] = (files[BRIDGE_SOURCE][0], bridge)
    files[BRIDGE_TEST] = (files[BRIDGE_TEST][0], bridge_test)
    files[NEAR_GOAL_SOURCE] = (files[NEAR_GOAL_SOURCE][0], near_goal)
    files.pop("payload/" + NEAR_GOAL, None)
    files["install.py"] = (
        files["install.py"][0],
        normalize_text("install.py", (PACKAGE_DIR / "install.py").read_bytes()),
    )

    meta["package"] = NAME
    meta["version"] = VERSION
    meta["allowed_releases"] = [R18]
    meta["release_base"] = "V1.3.8 complete r19 / runtime r18"
    meta["payload_sha256"].pop(NEAR_GOAL)
    meta["python_baselines"].pop(NEAR_GOAL)
    meta["python_accepted_sha256"].pop(NEAR_GOAL)
    meta["payload_sha256"][BRIDGE] = digest(bridge)
    meta["python_accepted_sha256"][BRIDGE] = list(dict.fromkeys(
        meta["python_accepted_sha256"][BRIDGE] + [
            "8f92daff611d6409df95ef42e046dd8f361b1fbc533ca34a047f785050520ee1",
        ]
    ))
    meta["changed_runtime_files"] = sorted(meta["payload_sha256"])
    meta["cumulative_through"] = "tour-start-reply-fix-1.0.9-20261019"
    meta["voice_control_failure_tts"] = "log_only_except_automatic_return"
    meta["hardware_validation"] = False
    put(files, "release.json", json_data(meta))

    for old_name in tuple(files):
        if old_name.endswith(".md") and "/" not in old_name:
            files.pop(old_name)
    for name in ("DEPLOYMENT.md", "VALIDATION.md"):
        put(files, name, normalize_text(name, (HERE / name).read_bytes()))

    write_sums(files, "SHA256SUMS")
    verify_sums(files, "SHA256SUMS")
    for name, (_, data) in files.items():
        if name.endswith(".py"):
            compile(data, name, "exec")
        if name.endswith((".py", ".sh", ".java", ".md", ".json")) or name == "SHA256SUMS":
            if b"\r" in data:
                raise RuntimeError("CRLF text in package: " + name)

    output_dir = HERE / "dist"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / (NAME + ".tar.gz")
    output.write_bytes(tar_data(files, NAME))
    output.with_name(output.name + ".sha256").write_text(
        digest(output.read_bytes()) + "  " + output.name + "\n",
        encoding="ascii",
        newline="\n",
    )
    print(json.dumps({
        "archive": str(output),
        "sha256": digest(output.read_bytes()),
        "bytes": output.stat().st_size,
        "runtime_release": R18,
        "java_jar_sha256": meta["patched_jar_sha256"],
        "bridge_sha256": meta["payload_sha256"][BRIDGE],
        "payload_files": sorted(meta["payload_sha256"]),
        "preserved_r18_near_goal_sha256": digest(near_goal),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
