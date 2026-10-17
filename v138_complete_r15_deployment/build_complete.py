"""Build the independent V1.3.8 r15 full delivery from the immutable r14."""
import argparse
import json
import os
from pathlib import Path
from archive_utils import digest, json_data, normalize_text, put, read_archive, tar_data, update_payload_manifest, verify_sums, write_sums

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OLD = "1.0.19-20261017-r14"
NEW = "1.0.19-20261017-r15"
BASE_NAME = "lingxi-v1.3.8-complete-20261017-r14-deployment"
NAME = "lingxi-v1.3.8-complete-20261017-r15-deployment"
VERSION = "1.3.8-complete-20261017-r15"
BASE_SHA = "ac7f93f20d1ed2155e394b4bf216d8717476925d571582632d84b4081d05f0b0"
DOCS = ("V1.3.8-r15-完整部署说明.md", "V1.3.8-r15-已编译AIMDK新机完整部署说明.md", "V1.3.8-r15-产品验收与修复说明.md")


def build(base):
    raw = base.read_bytes()
    assert digest(raw) == BASE_SHA, "r14 baseline archive mismatch"
    outer = read_archive(raw, BASE_NAME)
    verify_sums(outer, "SHA256SUMS.txt")
    old_component = "sparkrobot-lingxi-nav-internal-" + OLD + ".tar.gz"
    new_component = "sparkrobot-lingxi-nav-internal-" + NEW + ".tar.gz"
    nav = read_archive(outer["components/" + old_component][1], "sparkrobot-lingxi-nav-internal-" + OLD)
    verify_sums(nav, "SHA256SUMS")
    originals = {name: data for name, (_, data) in nav.items()}
    candidate = HERE / "candidate"
    for directory, directories, filenames in os.walk(candidate):
        directories[:] = sorted(n for n in directories if n not in {"node_modules", "dist", "target", "__pycache__", ".pytest_cache", "artifacts"})
        for filename in sorted(filenames):
            path = Path(directory) / filename
            relative = path.relative_to(candidate).as_posix()
            put(nav, relative, normalize_text(relative, path.read_bytes()))
            if relative.startswith("source/navigation/robot_local_navigation_v3/"):
                put(nav, "payload/" + relative[len("source/"):], normalize_text(relative, path.read_bytes()))
    for path in sorted((candidate / "source/server/dist").rglob("*")):
        if path.is_file():
            name = "payload/console/dist/" + path.relative_to(candidate / "source/server/dist").as_posix()
            put(nav, name, normalize_text(name, path.read_bytes()))
    put(nav, "payload/java/robot-local-navigation-java-all.jar", (HERE / "reference/java/robot-local-navigation-java-all.jar").read_bytes(), 0o644)
    for name, (_, data) in list(nav.items()):
        if name.startswith(("source/", "payload/", "docs/")):
            continue
        if name.endswith((".sh", ".json", ".md", ".service", ".env")):
            put(nav, name, data.replace(OLD.encode(), NEW.encode()))
    release = json.loads(nav["release-info.json"][1])
    release.update(version=NEW, parent_release=OLD, build="20261017-r15")
    put(nav, "release-info.json", json_data(release))
    for name in list(nav):
        if name.startswith("docs/"):
            del nav[name]
    for name in DOCS:
        put(nav, "docs/" + name, normalize_text(name, (HERE / name).read_bytes()))
    update_payload_manifest(nav)
    write_sums(nav, "SHA256SUMS")
    verify_sums(nav, "SHA256SUMS")
    component = tar_data(nav, "sparkrobot-lingxi-nav-internal-" + NEW)

    previous_control = outer["control_all.sh"][1]
    del outer["components/" + old_component]
    put(outer, "components/" + new_component, component)
    for path in sorted((HERE / "bundle").rglob("*")):
        if path.is_file():
            put(outer, path.relative_to(HERE / "bundle").as_posix(), path.read_bytes())
    for name, (_, data) in list(outer.items()):
        if name.startswith(("components/", "docs/", "tests/", "scripts/control-r")) or name == "bundle-manifest.json":
            continue
        if name.endswith((".sh", ".md", ".json")) or name == "VERSION":
            put(outer, name, data.replace(b"20261017-r14", b"20261017-r15").replace(b"V1.3.8 r14", b"V1.3.8 r15"))
    installer = outer["install_all.sh"][1].decode("utf-8")
    before = '/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261017-r13|"${EXPECTED_RELEASE}") ;;'
    after = '/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261017-r13|/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261017-r14|"${EXPECTED_RELEASE}") ;;'
    assert before in installer
    installer = installer.replace(before, after).replace("r11、r12、r13 或当前 r14", "r11、r12、r13、r14 或当前 r15")
    installer = installer.replace("bash /home/run/03-verify-after-web-start.sh", 'bash ${PACKAGE_DIR}/scripts/03-verify-after-web-start.sh')
    put(outer, "install_all.sh", installer.encode("utf-8"), 0o755)
    put(outer, "scripts/control-r14.sh", previous_control, 0o755)
    put(outer, "tests/test_functional_runtime.py", (HERE / "test_functional_runtime.py").read_bytes())
    put(outer, "tests/test_motion_confirmation.py", (HERE / "test_motion_confirmation.py").read_bytes())
    put(outer, "tests/test_voice_runtime.py", (HERE / "test_voice_runtime.py").read_bytes())
    put(outer, "tests/test_aimdk_install.py", (HERE / "test_aimdk_install.py").read_bytes())
    put(outer, "tests/test_task_http_persistence.py", (HERE / "test_task_http_persistence.py").read_bytes())
    put(outer, "tests/navigation/robot_local_navigation_v3/sdk_motion_controller.py",
        nav["payload/navigation/robot_local_navigation_v3/sdk_motion_controller.py"][1])
    for name in list(outer):
        if name.startswith("docs/"):
            del outer[name]
    for name in DOCS:
        put(outer, "docs/" + name, normalize_text(name, (HERE / name).read_bytes()))
    put(outer, "README.md", (
        "# 灵犀 V1.3.8 r15 完整部署包\n\n"
        "合并 r14 修复，并修复地图缓存误判在线、语音桥并发启动、过期 PID 和 AIMDK 安装事务。\n\n"
        "首次含 AIMDK 编译：docs/V1.3.8-r15-完整部署说明.md\n\n"
        "已编译 AIMDK 新机：docs/V1.3.8-r15-已编译AIMDK新机完整部署说明.md\n\n"
        "修复和验收范围：docs/V1.3.8-r15-产品验收与修复说明.md\n"
    ).encode("utf-8"))
    manifest = json.loads(outer["bundle-manifest.json"][1])
    manifest.update(version=VERSION, navigation_release=NEW, runtime_baseline="1.3.8-complete-20261017-r14", source_archive_sha256=BASE_SHA)
    manifest["components"].pop(old_component)
    manifest["components"][new_component] = digest(component)
    manifest["upgrade_sources"] = ["1.0.19-20261017-r11", "1.0.19-20261017-r12", "1.0.19-20261017-r13", OLD, NEW]
    manifest.setdefault("merged_capabilities", []).extend([
        "r15_uncached_map_service_health",
        "r15_idempotent_voice_start_process_identity_lock_http_health",
        "r15_aimdk_required_interfaces_and_restore_on_failed_switch",
    ])
    put(outer, "bundle-manifest.json", json_data(manifest))
    for name, (_, data) in list(outer.items()):
        if not name.startswith("components/"):
            put(outer, name, normalize_text(name, data))
    write_sums(outer, "SHA256SUMS.txt")
    verify_sums(outer, "SHA256SUMS.txt")
    output = tar_data(outer, NAME)
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / (NAME + ".tar.gz")
    target.write_bytes(output)
    target.with_name(target.name + ".sha256").write_text(digest(output) + "  " + target.name + "\n", encoding="ascii", newline="\n")
    changes = [name for name, (_, data) in nav.items() if originals.get(name) != data]
    (HERE / "qa/build-changes.json").write_bytes(json_data({"baseline_sha256": BASE_SHA, "archive_sha256": digest(output), "changed_files": sorted(changes)}))
    print("ARCHIVE=" + str(target), flush=True)
    print("SHA256=" + digest(output), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=ROOT / "v138_complete_r14_deployment/dist" / (BASE_NAME + ".tar.gz"))
    build(parser.parse_args().baseline)
