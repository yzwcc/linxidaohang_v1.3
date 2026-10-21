"""Build the V1.3.8 r20 complete bundle from r19 plus cumulative fix 1.0.10."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "v138_complete_r15_deployment"))
from archive_utils import digest, json_data, normalize_text, put, read_archive
from archive_utils import tar_data, verify_sums, write_sums

BASE_NAME = "lingxi-v1.3.8-complete-20261021-r19-deployment"
BASE = ROOT / "v138_complete_r19_deployment" / "dist" / (BASE_NAME + ".tar.gz")
BASE_SHA256 = "6ba69a459b597bb6d8f8901989d2638fc82a9f0f0c4143a6c455f48f2c803b11"
INCREMENT_NAME = "sparkrobot-lingxi-v1.3.8-voice-control-silent-fix-1.0.10-20261021"
INCREMENT = (
    ROOT
    / "v138_voice_control_silent_fix_deployment"
    / "dist"
    / (INCREMENT_NAME + ".tar.gz")
)
INCREMENT_SHA256 = "c6bbc65814347b5fd3999295b3943142b8499ba59d7d5ea0d181d209cb6513d2"
NAME = "lingxi-v1.3.8-complete-20261021-r20-deployment"
VERSION = "1.3.8-complete-20261021-r20"
R18 = "1.0.19-20261019-r18"
R20 = "1.0.20-20261021-r20"
FIX_VERSION = "1.0.10-20261021"
HELPER = "scripts/native-tour-quiet/"
VOICE_SCRIPT = "scripts/robot_voice_bridge_control.sh"
JAR = "java/robot-local-navigation-java-all.jar"
BRIDGE = "navigation/robot_local_navigation_v3/lingxi_nav_bridge.py"


def replace_once(data: bytes | str, old: str, new: str, label: str) -> bytes | str:
    is_text = isinstance(data, str)
    before = old if is_text else old.encode("utf-8")
    if data.count(before) != 1:
        raise RuntimeError(f"Expected one {label} marker, found {data.count(before)}")
    replacement = new if is_text else new.encode("utf-8")
    return data.replace(before, replacement, 1)


def get_json(files, name):
    return json.loads(files[name][1].decode("utf-8"))


def set_json(files, name, value):
    put(files, name, json_data(value))


def add_source_differences(increment, runtime, helper):
    changed = []
    for name, (_, data) in increment.items():
        if name.startswith("source/java/main/"):
            suffix = name[len("source/java/main/") :]
            target = "source/java/src/main/java/" + suffix
            helper_target = "source/java/main/" + suffix
        elif name.startswith("source/java/test/"):
            suffix = name[len("source/java/test/") :]
            target = "source/java/src/test/java/" + suffix
            helper_target = name
        elif name.startswith("source/navigation/"):
            target = name
            helper_target = name
        else:
            continue
        if runtime.get(target, (None, None))[1] != data:
            put(runtime, target, data)
            changed.append(target)
        if helper.get(helper_target, (None, None))[1] != data:
            put(helper, helper_target, data)
    return sorted(changed)


def update_runtime(component, increment, inc_meta):
    expected = {
        "java/robot-local-navigation-java-all.jar",
        "navigation/robot_local_navigation_v3/navigation_client.py",
        "navigation/robot_local_navigation_v3/lingxi_nav_bridge.py",
        "navigation/robot_local_navigation_v3/tts_speaker.py",
        "navigation/robot_local_navigation_v3/tts_receipt.py",
    }
    if set(inc_meta["changed_runtime_files"]) != expected:
        raise RuntimeError("Increment changed_runtime_files does not match the reviewed release")
    if inc_meta["version"] != FIX_VERSION:
        raise RuntimeError("Unexpected cumulative increment version")
    if inc_meta["patched_jar_sha256"] != "61055b65c24e086d2fe9f4459e3050d893dee4ed2c8a20abf54b8a0f6511fdf2":
        raise RuntimeError("Unexpected cumulative Java payload hash")
    if inc_meta["payload_sha256"][BRIDGE] != "ded2c941c4f033b22d688f9266407ea8fc7578bc0a45f50d39a7d7391182115c":
        raise RuntimeError("Unexpected cumulative bridge payload hash")

    nav_package = get_json(component, "release-info.json")
    if nav_package.get("version") != R18:
        raise RuntimeError("r19 component does not contain the expected r18 runtime")

    for relative in sorted(expected):
        packaged = "payload/" + relative
        if packaged not in increment:
            raise RuntimeError("Increment is missing payload/" + relative)
        if digest(increment[packaged][1]) != inc_meta["payload_sha256"][relative]:
            raise RuntimeError("Increment payload hash mismatch: " + relative)
        put(component, "payload/" + relative, increment[packaged][1])

    source_changes = add_source_differences(increment, component, {})
    expected_source_changes = {
        "source/java/src/main/java/com/agibot/aiem/nav/LingxiNavAgentApp.java",
        "source/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py",
        "source/navigation/test/test_silent_control_receipts.py",
        "source/java/src/test/java/com/agibot/aiem/nav/ControlOwnershipFaultTest.java",
        "source/java/src/test/java/com/agibot/aiem/nav/ControlReplyRegressionTest.java",
        "source/java/src/test/java/com/agibot/aiem/nav/ControlReplyTextWireTest.java",
        "source/java/src/test/java/com/agibot/aiem/nav/ReferenceControlChannelTest.java",
        "source/java/src/test/java/com/agibot/aiem/nav/SilentStartControlTest.java",
        "source/java/src/test/java/com/agibot/aiem/nav/TourStartTakeoverTest.java",
    }
    if set(source_changes) != expected_source_changes:
        raise RuntimeError("Unexpected source snapshot delta: " + repr(source_changes))

    nav_package.update(
        version=R20,
        parent_release=R18,
        build="20261021-r20",
        merged_control_reply_fix=FIX_VERSION,
        merged_voice_control_silent_fix=FIX_VERSION,
        strict_audit_fixes=[
            *nav_package.get("strict_audit_fixes", []),
            "cumulative_voice_control_silent_fix_1_0_10",
        ],
    )
    set_json(component, "release-info.json", nav_package)
    manifest = get_json(component, "payload-manifest.json")
    manifest["control_reply_fix"] = FIX_VERSION
    manifest["arrival_tts_fix"] = FIX_VERSION
    manifest["voice_control_silent_fix"] = FIX_VERSION
    set_json(component, "payload-manifest.json", manifest)
    # Rebuild file entries from the actual payload tree so no stale hash survives.
    from archive_utils import update_payload_manifest

    update_payload_manifest(component)
    # Restore metadata fields after update_payload_manifest rewrites only files.
    manifest = get_json(component, "payload-manifest.json")
    manifest.update(
        control_reply_fix=FIX_VERSION,
        arrival_tts_fix=FIX_VERSION,
        voice_control_silent_fix=FIX_VERSION,
    )
    set_json(component, "payload-manifest.json", manifest)

    merged = get_json(component, "merged-increments.json")
    merged.update(
        runtime_release=R20,
        effective_increment=INCREMENT_NAME,
        effective_jar_sha256=inc_meta["patched_jar_sha256"],
        latest_cumulative_increment=FIX_VERSION,
        java_runtime_identical_to_shipped_1_0_8=False,
        runtime_identical_to_shipped_1_0_8=False,
        full_r20_fixes=[
            "cumulative_voice_control_silent_fix_1_0_10",
            "native_tour_helper_payload_synchronized_with_r20",
        ],
        increment_release=inc_meta,
    )
    existing = merged.get("all_post_r15_increments", [])
    if not any(item.get("package") == INCREMENT_NAME for item in existing):
        existing.append(
            {
                "package": INCREMENT_NAME,
                "sha256": INCREMENT_SHA256,
                "jar_sha256": inc_meta["patched_jar_sha256"],
                "bridge_sha256": inc_meta["payload_sha256"][BRIDGE],
                "version": FIX_VERSION,
                "cumulative_function": (
                    "Cumulative Java Agent and navigation bridge silent control replies; "
                    "TTS receipt and priority-conflict handling"
                ),
            }
        )
    merged["all_post_r15_increments"] = existing
    set_json(component, "merged-increments.json", merged)
    write_sums(component, "SHA256SUMS")
    verify_sums(component, "SHA256SUMS")
    return source_changes, nav_package


def patch_helper(outer, component, increment, inc_meta):
    helper = {
        name[len(HELPER) :]: entry
        for name, entry in outer.items()
        if name.startswith(HELPER)
    }
    if "release.json" not in helper or "arrival_runtime.py" not in helper:
        raise RuntimeError("r19 native-tour-quiet helper is incomplete")
    meta = json.loads(helper["release.json"][1].decode("utf-8"))

    # Keep the helper's transactional mapping behavior, but pin its runtime files
    # to the exact r20 payload so a later mapping repair cannot downgrade them.
    runtime_payloads = {
        JAR: "payload/java/robot-local-navigation-java-all.jar",
        "navigation/robot_local_navigation_v3/navigation_client.py":
            "payload/navigation/robot_local_navigation_v3/navigation_client.py",
        BRIDGE: "payload/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py",
        "navigation/robot_local_navigation_v3/tts_speaker.py":
            "payload/navigation/robot_local_navigation_v3/tts_speaker.py",
        "navigation/robot_local_navigation_v3/tts_receipt.py":
            "payload/navigation/robot_local_navigation_v3/tts_receipt.py",
    }
    for relative, payload_path in runtime_payloads.items():
        data = component[payload_path][1]
        put(helper, "payload/" + relative, data)
        meta["payload_sha256"][relative] = digest(data)
    for name, (_, data) in increment.items():
        if name.startswith("source/java/main/"):
            relative = "source/java/main/" + name[len("source/java/main/") :]
        elif name.startswith("source/java/test/"):
            relative = name
        elif name.startswith("source/navigation/"):
            relative = name
        else:
            continue
        if relative in helper:
            put(helper, relative, data)
    near_goal = "navigation/robot_local_navigation_v3/near_goal_arrival.py"
    if digest(component["payload/" + near_goal][1]) != meta["payload_sha256"][near_goal]:
        raise RuntimeError("r18 near-goal arrival payload changed unexpectedly")

    for relative, values in meta["python_accepted_sha256"].items():
        current = meta["payload_sha256"][relative]
        meta["python_accepted_sha256"][relative] = list(dict.fromkeys([*values, current]))
    old_r18_jar = "3140d03c2d81623ef2efb362400699c9e9dae7d4c09409a7431fff42bdb62372"
    meta["superseded_jar_sha256"] = list(
        dict.fromkeys([*meta.get("superseded_jar_sha256", []), old_r18_jar])
    )
    meta.update(
        patched_jar_sha256=inc_meta["patched_jar_sha256"],
        allowed_releases=[R20],
        full_bundle_adapter=VERSION,
        effective_increment=FIX_VERSION,
        control_reply_fix_version=FIX_VERSION,
        arrival_tts_fix_version=FIX_VERSION,
        voice_control_silent_fix=FIX_VERSION,
    )
    put(helper, "release.json", json_data(meta))

    full_setup = helper["full_setup.py"][1].decode("utf-8")
    full_setup = replace_once(full_setup, f'EXPECTED_RELEASE = "{R18}"',
                              f'EXPECTED_RELEASE = "{R20}"', "native helper release")
    full_setup = replace_once(full_setup, "full-r18-status.json", "full-r20-status.json",
                              "native helper status filename")
    full_setup = replace_once(full_setup, "full-r18-", "full-r20-", "native helper temp prefix")
    put(helper, "full_setup.py", full_setup.encode("utf-8"))

    arrival = helper["arrival_runtime.py"][1].decode("utf-8")
    arrival = replace_once(
        arrival,
        'new_manifest["control_reply_fix"] = self.meta["version"]',
        'new_manifest["control_reply_fix"] = self.meta.get("control_reply_fix_version", self.meta["version"])',
        "control reply manifest marker",
    )
    arrival = replace_once(
        arrival,
        'new_manifest["arrival_tts_fix"] = self.meta["version"]',
        'new_manifest["arrival_tts_fix"] = self.meta.get("arrival_tts_fix_version", self.meta["version"])',
        "arrival TTS manifest marker",
    )
    put(helper, "arrival_runtime.py", arrival.encode("utf-8"))

    readme = helper["README.md"][1].decode("utf-8")
    readme = readme.replace(R18, R20).replace("r18", "r20")
    put(helper, "README.md", normalize_text("README.md", readme.encode("utf-8")))

    for name, (_, data) in list(helper.items()):
        if name.endswith(".py"):
            compile(data, name, "exec")
        if name == "test_voice_validator.py":
            text = data.decode("utf-8")
            text = text.replace("r19-qa", "r20-qa").replace(
                "lingxi-v1.3.8-complete-20261021-r19-deployment",
                NAME,
            )
            helper[name] = (helper[name][0], text.encode("utf-8"))
        elif name == "test_full_setup.py":
            text = data.decode("utf-8")
            text = text.replace(R18, R20).replace("full-r18-status.json", "full-r20-status.json")
            text = text.replace(".full-r18-", ".full-r20-")
            helper[name] = (helper[name][0], text.encode("utf-8"))

    write_sums(helper, "SHA256SUMS")
    verify_sums(helper, "SHA256SUMS")
    for name, (entry, data) in helper.items():
        put(outer, HELPER + name, data, entry.mode)
    return meta


def update_outer(outer, nav_raw, inc_meta):
    old_component = next(
        name
        for name in outer
        if name.startswith("components/sparkrobot-lingxi-nav-internal-")
        and name.endswith("-" + R18 + ".tar.gz")
    )
    old_mode = outer[old_component][0].mode
    new_component = f"components/sparkrobot-lingxi-nav-internal-{R20}.tar.gz"
    outer.pop(old_component)
    put(outer, new_component, nav_raw, old_mode)

    install = outer["install_all.sh"][1].decode("utf-8")
    for old, new, label in (
        ('VERSION="1.3.8-complete-20261019-r18"',
         f'VERSION="{VERSION}"', "full installer version"),
        (f'NAV_VERSION="{R18}"', f'NAV_VERSION="{R20}"', "runtime release"),
        ("/home/run/lingxi-v1.3.8-complete-20261019-r18-work",
         f"/home/run/{NAME}-work", "installer work directory"),
    ):
        install = replace_once(install.encode("utf-8"), old, new, label).decode("utf-8")
    allowlist_line = next(
        line for line in install.splitlines()
        if "releases/1.0.19-20261019-r17" in line and "EXPECTED_RELEASE" in line
    )
    if f"/opt/sparkrobot/lingxi-nav/releases/{R18}" not in allowlist_line:
        replacement_line = allowlist_line.replace(
            '|"${EXPECTED_RELEASE}") ;;',
            f'|/opt/sparkrobot/lingxi-nav/releases/{R18}|"${{EXPECTED_RELEASE}}") ;;',
        )
        install = replace_once(
            install.encode("utf-8"), allowlist_line, replacement_line, "upgrade source allowlist"
        ).decode("utf-8")
    install = install.replace("r11-r18", "r11-r18 / r20")
    install = install.replace("V1.3.8 r18", "V1.3.8 r20")
    put(outer, "install_all.sh", normalize_text("install_all.sh", install.encode("utf-8")))

    old_control = outer["control_all.sh"][1].decode("utf-8")
    put(outer, "scripts/control-r18.sh", old_control.encode("utf-8"), 0o755)
    control = old_control
    control = replace_once(
        control.encode("utf-8"),
        'VERSION="1.3.8-complete-20261019-r18"',
        f'VERSION="{VERSION}"',
        "control bundle version",
    ).decode("utf-8")
    control = replace_once(
        control.encode("utf-8"),
        f'NAV_VERSION="{R18}"',
        f'NAV_VERSION="{R20}"',
        "control runtime version",
    ).decode("utf-8")
    put(outer, "control_all.sh", normalize_text("control_all.sh", control.encode("utf-8")))
    put(outer, "scripts/control-r20.sh", outer["control_all.sh"][1], 0o755)

    verify_script = outer["scripts/03-verify-after-web-start.sh"][1]
    verify_script = replace_once(
        verify_script,
        f"/opt/sparkrobot/lingxi-nav/releases/{R18}",
        f"/opt/sparkrobot/lingxi-nav/releases/{R20}",
        "web-start verification release",
    )
    put(outer, "scripts/03-verify-after-web-start.sh", verify_script)

    # Retire the r19 manuals in the package and ship the two reviewed r20 guides.
    for name in list(outer):
        if name.startswith("docs/V1.3.8-r19-"):
            outer.pop(name)
    guide_sources = sorted(HERE.joinpath("docs").glob("V1.3.8-r20-*.md"))
    if len(guide_sources) != 2:
        raise RuntimeError("Expected exactly two r20 deployment guides")
    for source, target in zip(
        guide_sources, ("deployment-guide.md", "precompiled-aimdk-guide.md")
    ):
        put(outer, "docs/" + target, normalize_text(target, source.read_bytes()))

    merged = json.loads(outer["merged-increments.json"][1].decode("utf-8"))
    merged.update(
        runtime_release=R20,
        full_bundle_version=VERSION,
        latest_cumulative_increment=FIX_VERSION,
        effective_increment=INCREMENT_NAME,
        effective_jar_sha256=inc_meta["patched_jar_sha256"],
        cumulative_voice_control_silent_fix=FIX_VERSION,
        full_r20_fixes=[
            "cumulative_voice_control_silent_fix_1_0_10",
            "native_tour_helper_payload_synchronized_with_r20",
        ],
    )
    increments = merged.setdefault("all_post_r15_increments", [])
    if not any(item.get("package") == INCREMENT_NAME for item in increments):
        increments.append(
            dict(
                package=INCREMENT_NAME,
                sha256=INCREMENT_SHA256,
                jar_sha256=inc_meta["patched_jar_sha256"],
                bridge_sha256=inc_meta["payload_sha256"][BRIDGE],
                version=FIX_VERSION,
                cumulative_function=(
                    "Cumulative Java Agent and navigation bridge silent control replies; "
                    "TTS receipt and priority-conflict handling"
                ),
            )
        )
    put(outer, "merged-increments.json", json_data(merged))

    manifest = json.loads(outer["bundle-manifest.json"][1].decode("utf-8"))
    manifest["version"] = VERSION
    manifest["navigation_release"] = R20
    manifest["components"].pop(old_component.rsplit("/", 1)[1], None)
    manifest["components"][new_component.rsplit("/", 1)[1]] = digest(nav_raw)
    sources = manifest.setdefault("upgrade_sources", [])
    if R18 not in sources:
        sources.append(R18)
    for fix in (
        "cumulative_voice_control_silent_fix_1_0_10",
        "native_tour_helper_payload_synchronized_with_r20",
    ):
        if fix not in manifest.setdefault("strict_audit_fixes", []):
            manifest["strict_audit_fixes"].append(fix)
    capabilities = manifest.setdefault("merged_capabilities", [])
    for item in (
        "cumulative_silent_voice_navigation_and_tour_control_replies",
        "asynchronous_tts_receipt_and_priority_conflict_handling",
    ):
        if item not in capabilities:
            capabilities.append(item)
    post_r15 = manifest.setdefault("merged_post_r15_increments", [])
    if not any(item.get("package") == INCREMENT_NAME for item in post_r15):
        post_r15.append(
            dict(
                package=INCREMENT_NAME,
                sha256=INCREMENT_SHA256,
                jar_sha256=inc_meta["patched_jar_sha256"],
                bridge_sha256=inc_meta["payload_sha256"][BRIDGE],
                version=FIX_VERSION,
            )
        )
    put(outer, "bundle-manifest.json", json_data(manifest))
    put(outer, "VERSION", f"LINGXI_COMPLETE_VERSION={VERSION}\n".encode("ascii"))
    put(
        outer,
        "README.md",
        (
            "# Lingxi V1.3.8 r20 Complete Deployment\n\n"
            f"Full bundle `{VERSION}`; runtime `{R20}`. "
            f"Includes cumulative voice-control silent fix `{FIX_VERSION}` and "
            "keeps the r19 native-tour validator fix synchronized with the r20 runtime.\n\n"
            "- `docs/V1.3.8-r20-完整部署说明.md`\n"
            "- `docs/V1.3.8-r20-已编译AIMDK新机部署说明.md`\n"
        ).encode("utf-8"),
    )
    readme_lines = outer["README.md"][1].decode("utf-8").splitlines()
    # The docs are also listed explicitly here so their names stay ASCII-safe.
    readme_lines = [
        line for line in readme_lines
        if not line.startswith("- `docs/V1.3.8-r20-")
    ]
    readme_lines.extend(
        ["", "- `docs/deployment-guide.md`", "- `docs/precompiled-aimdk-guide.md`"]
    )
    put(outer, "README.md", "\n".join(readme_lines).encode("utf-8"))
    write_sums(outer, "SHA256SUMS.txt")
    verify_sums(outer, "SHA256SUMS.txt")


def main():
    base_raw = BASE.read_bytes()
    if digest(base_raw) != BASE_SHA256:
        raise RuntimeError("r19 base archive SHA-256 mismatch")
    base_sidecar = BASE.with_name(BASE.name + ".sha256").read_text(encoding="ascii")
    if base_sidecar != f"{BASE_SHA256}  {BASE.name}\n":
        raise RuntimeError("r19 sidecar mismatch")
    outer = read_archive(base_raw, BASE_NAME)
    verify_sums(outer, "SHA256SUMS.txt")

    inc_raw = INCREMENT.read_bytes()
    if digest(inc_raw) != INCREMENT_SHA256:
        raise RuntimeError("cumulative increment SHA-256 mismatch")
    inc_sidecar = INCREMENT.with_name(INCREMENT.name + ".sha256").read_text(encoding="ascii")
    if inc_sidecar != f"{INCREMENT_SHA256}  {INCREMENT.name}\n":
        raise RuntimeError("increment sidecar mismatch")
    increment = read_archive(inc_raw, INCREMENT_NAME)
    verify_sums(increment, "SHA256SUMS")
    inc_meta = get_json(increment, "release.json")

    old_component_name = next(
        name
        for name in outer
        if name.startswith("components/sparkrobot-lingxi-nav-internal-")
        and name.endswith("-" + R18 + ".tar.gz")
    )
    component_root = "sparkrobot-lingxi-nav-internal-" + R18
    component = read_archive(outer[old_component_name][1], component_root)
    verify_sums(component, "SHA256SUMS")

    source_changes, release_info = update_runtime(component, increment, inc_meta)
    helper_meta = patch_helper(outer, component, increment, inc_meta)
    component_root_new = "sparkrobot-lingxi-nav-internal-" + R20
    component_raw = tar_data(component, component_root_new)
    update_outer(outer, component_raw, inc_meta)

    # The helper is an independent transactional updater. Its installation
    # payload must equal the just-built runtime payload byte-for-byte.
    for relative, expected in helper_meta["payload_sha256"].items():
        data = component["payload/" + relative][1]
        if digest(data) != expected:
            raise RuntimeError("native-tour helper is not synchronized: " + relative)
    if helper_meta["allowed_releases"] != [R20]:
        raise RuntimeError("native-tour helper release allowlist mismatch")
    if release_info["version"] != R20:
        raise RuntimeError("runtime release metadata mismatch")

    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / (NAME + ".tar.gz")
    output_raw = tar_data(outer, NAME)
    output.write_bytes(output_raw)
    output_sha = digest(output_raw)
    output.with_name(output.name + ".sha256").write_text(
        f"{output_sha}  {output.name}\n", encoding="ascii", newline="\n"
    )
    for source in HERE.joinpath("docs").glob("V1.3.8-r20-*.md"):
        (dist / source.name).write_bytes(source.read_bytes())
    (dist / "merged-increments.json").write_bytes(outer["merged-increments.json"][1])
    (dist / "bundle-manifest.json").write_bytes(outer["bundle-manifest.json"][1])
    report = {
        "bundle": output.name,
        "bundle_sha256": output_sha,
        "bundle_bytes": len(output_raw),
        "runtime_release": R20,
        "increment": INCREMENT_NAME,
        "increment_sha256": INCREMENT_SHA256,
        "java_jar_sha256": inc_meta["patched_jar_sha256"],
        "bridge_sha256": inc_meta["payload_sha256"][BRIDGE],
        "native_tour_helper_release_allowlist": helper_meta["allowed_releases"],
        "source_files_updated": source_changes,
        "hardware_validation": False,
    }
    (dist / "BUILD-VERIFICATION.json").write_bytes(json_data(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
