from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zipfile
import build_delivery as build

def digest(data):
    return hashlib.sha256(data).hexdigest()

def require(condition, reason):
    if not condition:
        raise AssertionError(reason)

def verify_sums(files, name):
    sums = files[name]
    require(b"\r" not in sums, f"{name}: non-LF checksum file")
    entries = {}
    for line in sums.decode("utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        require(relative in files, "Checksum file missing: " + relative)
        require(digest(files[relative]) == expected, "Checksum mismatch: " + relative)
        entries[relative] = expected
    require(set(entries) == set(files) - {name}, f"{name}: unlisted files")

def tar_files(path):
    with tarfile.open(path, "r:gz") as f:
        records = [m for m in f if m.isfile()]
        top = PurePosixPath(records[0].name).parts[0]
        require(len({m.name for m in records}) == len(records), "Duplicate TAR names")
        result = {}
        for m in records:
            n = PurePosixPath(m.name)
            require(n.parts[0] == top and not n.is_absolute() and ".." not in n.parts,
                    "Unsafe TAR member")
            require(not m.issym() and not m.islnk(), "Unexpected TAR link")
            result[n.relative_to(top).as_posix()] = f.extractfile(m).read()
            if m.name.endswith(".sh") or "/runtime/java/bin/" in m.name or m.name.endswith("/runtime/node"):
                require(m.mode & 0o111, "Missing executable mode: " + m.name)
        return result

def audit_inner(path):
    files = tar_files(path)
    verify_sums(files, "SHA256SUMS")
    manifest = json.loads(files["payload-manifest.json"])
    entries = {n["path"]:n for n in manifest["files"]}
    actual = {n.removeprefix("payload/") for n in files if n.startswith("payload/")}
    require(set(entries) == actual, "Payload manifest incomplete")
    for n,item in entries.items():
        require(digest(files["payload/" + n]) == item["sha256"], "Payload mismatch: " + n)
    checks = {
        "payload/navigation/robot_local_navigation_v3/lingxi_nav_bridge.py": [
            b"_task_loop_count", b"_task_wait_interaction_active", b"_on_voice_interruption_resume",
            b"_task_navigation_task_id", b"_handle_task_navigation_failure",
            b"NavigationEventType.BLOCKED", "请大家让让我".encode(),
        ],
        "payload/navigation/robot_local_navigation_v3/navigation_client.py": [
            b"BlockedNavigationDetector", b"_blocked_detector.update", b"_effective_navigation_profile",
            b"allow_timeout_arrival_recovery", b"widened_post_entry_fallback", b"claim_bounded_retry",
        ],
        "payload/navigation/robot_local_navigation_v3/map_label_server_v3.py": [
            b'"loop_count": payload.get("loop_count", 1)', b"refreshRobotPose",
        ],
        "payload/navigation/robot_local_navigation_v3/models.py": [b"DENSE_CROWD_MAX_FORWARD_SPEED = 0.30"],
        "payload/console/dist/app.js": [b"loop_count"],
        "payload/console/dist/public/app.js": [b"loop_count", b"command_topic"],
        "install.sh": [b'ln -sT "${TARGET}"', b'20-wake-gate-', b'navigation["dense_crowd"]'],
    }
    for name, markers in checks.items():
        require(name in files, "Missing feature file: " + name)
        for marker in markers:
            require(marker in files[name], "Missing feature marker in " + name + ": " + repr(marker))
    for name in ("payload/runtime/node", "payload/runtime/java/bin/java"):
        elf = files[name][:20]
        require(elf[:4] == b"\x7fELF" and int.from_bytes(elf[18:20], "little") == 183,
                "Runtime is not Linux ARM64: " + name)
    jar = files["payload/java/robot-local-navigation-java-all.jar"]
    require(digest(jar) == build.JAVA_SHA, "Java baseline mismatch")
    with zipfile.ZipFile(io.BytesIO(jar)) as f:
        require(f.testzip() is None, "Java ZIP corruption")
        for n in ("RetrieverClient", "WakeSessionGate", "Soc2WakeEvidenceProvider", "VoiceGate"):
            require(f"com/agibot/aiem/nav/{n}.class" in f.namelist(), "Java class missing: " + n)
    profile = json.loads(files["templates/unified_nav_config.json"])
    require(profile["navigation"]["dense_crowd"]["enabled"], "Fresh template profile disabled")
    require(profile["navigation"]["dense_crowd"]["max_forward_speed"] == .30, "Wrong profile speed")
    require(profile["app"]["doubao_api_key"] == "", "Real key in template")
    # Verify the package contains the merged working sources, not older patch payloads.
    nav = build.V139 / "robot_local_navigation_v3/robot_local_navigation_v3-new-auto-return-interaction-end"
    for p in (nav / "robot_local_navigation_v3").glob("*.py"):
        rel = "payload/navigation/robot_local_navigation_v3/" + p.name
        require(files[rel] == p.read_bytes().replace(b"\r\n", b"\n"), "Stale runtime source: " + p.name)
    for p in (build.V139 / "server/dist").glob("*.js"):
        require(files["payload/console/dist/" + p.name] == p.read_bytes().replace(b"\r\n",b"\n"),
                "Stale console output: " + p.name)
    # Own text is normalized. Third party dependencies and runtime licenses may
    # legitimately retain upstream line endings.
    patterns = [
        re.compile(rb"sk-[A-Za-z0-9_-]{20,}"), re.compile(rb"AKLT[A-Za-z0-9_-]{12,}"),
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ]
    for n,data in files.items():
        require(not n.endswith((".pem", ".key")) and Path(n).name not in
                {"secrets.env", "key.txt", ".env", "console.env", "runtime.env"}, "Sensitive file: " + n)
        if "/node_modules/" not in n and "/runtime/" not in n and Path(n).suffix in {
            ".sh", ".py", ".js", ".ts", ".json", ".md", ".java", ".xml", ".service", ".properties",
        }:
            require(b"\r\n" not in data, "CRLF owned text: " + n)
            for pattern in patterns:
                require(not pattern.search(data), "Suspected secret: " + n)
    print(f"INNER: {len(files)} files, payload {len(entries)} entries; hashes/features/source/ARM64/secret checks passed")
    return len(files)

def audit_stage():
    stage = build.DELIVERY / build.NAME
    files = {p.relative_to(stage).as_posix():p.read_bytes() for p in stage.rglob("*") if p.is_file()}
    manifest = json.loads(files["bundle-manifest.json"])
    require(manifest["version"] == build.VERSION, "Wrong outer version")
    require(manifest["navigation_version"] == build.NAV_VERSION, "Wrong inner version")
    for n,expected in manifest["components"].items():
        require(digest(files["components/" + n]) == expected, "Component hash mismatch: " + n)
    install = files["install_all.sh"]
    control = files["control_all.sh"]
    for n,data in (("install_all.sh",install),("control_all.sh",control)):
        require(build.VERSION.encode() in data and build.NAV_VERSION.encode() in data,
                "Installer/control version mismatch")
        require(build.OLD_VERSION.encode() not in data and b"1.0.20-20260923" not in data,
                "Stale version in executable")
    for marker in (b"release_config.py", b"backup_state.py", b"--keep-config", b"pnc_policy/control.sh",
                   b"systemctl restart sparkrobot-lingxi-kb.service"):
        require(marker in install, "Missing configuration/install operation: " + repr(marker))
    for name in ("release_config.py", "backup_state.py", "verify_release.py",
                 "pnc_policy/apply_policy.py", "pnc_policy/policy.json", "pnc_policy/control.sh"):
        require("scripts/" + name in files, "Missing deployment helper: " + name)
    kb = files["components/sparkrobot-lingxi-kb-1.0.2-20260902.zip"]
    require(digest(kb) == "b8f73d925cc914b0440bc6478424f7759f9472cc9c61d8ce06050c37ddf9cf30",
            "KB baseline mismatch")
    require(digest(files["components/aimdk-aarch64-1bde262f-artifacts.7z"]) ==
            "f13147a175bc3fb621fb118698aae0353c097adda38bba640f9a39c01433cc98", "AIMDK mismatch")
    audit_inner(stage / "components" / (build.NAV_NAME + ".tar.gz"))
    print(f"OUTER STAGE: {len(files)} files; components/installers/config effects/version references passed")

def audit_archives():
    stage = build.DELIVERY / build.NAME
    expected = {p.relative_to(stage).as_posix():digest(p.read_bytes()) for p in stage.rglob("*") if p.is_file()}
    tar_path = build.DELIVERY / (build.NAME + ".tar.gz")
    files = tar_files(tar_path)
    verify_sums(files, "SHA256SUMS.txt")
    require({n:digest(b) for n,b in files.items()} == expected, "Final TAR differs from stage")
    zip_path = build.DELIVERY / (build.NAME + ".zip")
    with zipfile.ZipFile(zip_path) as f:
        require(f.testzip() is None, "Final ZIP corruption")
        require(len(f.namelist()) == len(set(f.namelist())), "Duplicate ZIP members")
        zipped = {str(PurePosixPath(n).relative_to(build.NAME)):digest(f.read(n)) for n in f.namelist()}
        require(zipped == expected, "Final ZIP differs from TAR/stage")
    for p in (tar_path, zip_path):
        require(p.with_suffix(p.suffix + ".sha256").read_text().split()[0] == build.sha(p),
                "Final sidecar mismatch")
    print("FINAL TAR/ZIP: every file identical; all outer and inner checksums passed")

if __name__ == "__main__":
    import sys
    audit_stage()
    if "--archives" in sys.argv:
        audit_archives()
