"""Build r18 from immutable r17, changing only two audited implementation files."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "v138_complete_r15_deployment"))
from archive_utils import (digest, json_data, normalize_text, put, read_archive,
                           tar_data, update_payload_manifest, verify_sums, write_sums)

OLD = "1.0.19-20261019-r17"
NEW = "1.0.19-20261019-r18"
BASE_NAME = "lingxi-v1.3.8-complete-20261019-r17-deployment"
NAME = "lingxi-v1.3.8-complete-20261019-r18-deployment"
VERSION = "1.3.8-complete-20261019-r18"
BASE_SHA = "a5efc321951a4711a8b9656e32c83165c7ea54cfb25a4204d74c2df1080f5d1d"
FIX_NAME = "sparkrobot-lingxi-v1.3.8-native-tour-quiet-1.0.10-20261019"
FIX_SHA = "4f663936aecd297be378967dd1c16a623d6da7d1ede892c7ee5ae391c955afe6"
NEAR = "navigation/robot_local_navigation_v3/near_goal_arrival.py"
DOCS = ("V1.3.8-r18-完整部署教程.md", "V1.3.8-r18-已编译AIMDK新机部署教程.md", "严格复查说明.md")


def guide(data):
    text = data.decode("utf-8").replace("20261019-r17", "20261019-r18").replace("r17", "r18")
    text = text.replace("已有 r11～r16", "已有 r11～r17").replace("来源 r11～r15", "来源 r11～r17")
    text = text.replace("运行文件采用已交付 1.0.8 的准确字节，",
                        "Java 保持已交付 1.0.8 的准确字节，到点判定补充关闭朝向检查的兼容修复；")
    text = text.replace("运行文件采用已交付 1.0.8", "Java 采用已交付 1.0.8")
    text = text.replace("朝向满足配置且不超过 0.25rad",
                        "启用朝向检查时满足配置且不超过 0.25rad（显式关闭时不要求朝向）")
    text += ("\n## r18 复查修复\n\n"
             "修复新机 SOC2 主机指纹路径不一致：初次配置、后续原生检查与网页启动共用 "
             "`/var/lib/sparkrobot/lingxi-nav/ssh/soc2-known-hosts`，也遵循 runtime.env 的自定义路径；"
             "配置结束以 run 账户和严格主机校验验证通道。保留已有指纹，指纹变化时中止，不绕过检查。\n\n"
             "显式设置 `yaw_tolerance_rad=null` 时，近目标判定支持该合法配置，"
             "其余停车和定位证据要求不变。默认朝向配置不变。\n\n"
             "若已有 r17 只在原生阶段报 Host key verification failed，"
             "可以先解压 r18，使用新脚本修复通道，再保留配置升级：\n\n"
             "```bash\n"
             "cd /home/run/lingxi-v1.3.8-complete-20261019-r18-deployment\n"
             "sudo bash ./scripts/setup-soc2-access.sh\n"
             "bash ./install_all.sh upgrade\n"
             "sudo sparkrobot-lingxi-complete start\n"
             "bash ./install_all.sh doctor\n"
             "```\n\n"
             "已安装其他兼容版本且通道正常，按本文 upgrade 即可，不必重复配置密钥。"
             "r17 回退示例的运行版本为 `1.0.19-20261019-r17`，配套脚本为 `scripts/control-r17.sh`。\n")
    return text.encode("utf-8")


def main():
    archive = ROOT / "v138_complete_r17_deployment/dist" / (BASE_NAME + ".tar.gz")
    raw = archive.read_bytes()
    assert digest(raw) == BASE_SHA
    outer = read_archive(raw, BASE_NAME)
    verify_sums(outer, "SHA256SUMS.txt")
    old_guides = {name: data for name, (_, data) in outer.items() if name.startswith("docs/")}
    old_name = "sparkrobot-lingxi-nav-internal-" + OLD
    new_name = "sparkrobot-lingxi-nav-internal-" + NEW
    nav = read_archive(outer["components/" + old_name + ".tar.gz"][1], old_name)
    verify_sums(nav, "SHA256SUMS")
    old_payload = {name: data for name, (_, data) in nav.items() if name.startswith("payload/")}
    setup = normalize_text("setup_soc2_access.sh", (HERE / "setup_soc2_access.sh").read_bytes())
    near = (HERE / "near_goal_arrival.py").read_bytes()
    put(nav, "scripts/setup_soc2_access.sh", setup, 0o755)
    put(nav, "payload/" + NEAR, near, 0o644)
    put(nav, "source/" + NEAR, near, 0o644)
    optional_test = (HERE / "test_optional_arrival_heading.py").read_bytes()
    put(nav, "source/navigation/test/test_optional_arrival_heading.py", optional_test)
    put(outer, "tests/navigation/" + NEAR.removeprefix("navigation/"), near)
    put(outer, "tests/navigation/test/test_optional_arrival_heading.py", optional_test)
    put(outer, "tests/test_soc2_bootstrap.py", (HERE / "test_soc2_bootstrap.py").read_bytes())
    put(outer, "scripts/setup-soc2-access.sh", setup, 0o755)
    old_control = outer["control_all.sh"][1]
    for name, (_, data) in list(nav.items()):
        if not name.startswith(("payload/", "source/", "docs/")) and name != "merged-increments.json":
            put(nav, name, data.replace(OLD.encode(), NEW.encode()))
    for name, (_, data) in list(outer.items()):
        if (name.startswith(("components/", "docs/", "scripts/control-r", "scripts/native-tour-quiet/"))
                or name in {"bundle-manifest.json", "merged-increments.json"}):
            continue
        if name.endswith((".sh", ".md", ".json")) or name == "VERSION":
            put(outer, name, data.replace(b"r17", b"r18"))
    install = outer["install_all.sh"][1].decode("utf-8")
    marker = '|/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261019-r16|"${EXPECTED_RELEASE}")'
    assert marker in install
    install = install.replace(marker, '|/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261019-r16|'
                              '/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261019-r17|"${EXPECTED_RELEASE}")')
    install = install.replace("r15、r16 或当前 r18", "r15、r16、r17 或当前 r18")
    put(outer, "install_all.sh", install.encode("utf-8"), 0o755)
    put(outer, "scripts/control-r17.sh", old_control, 0o755)
    repair = {}
    prefix = "scripts/native-tour-quiet/"
    for name, entry in outer.items():
        if name.startswith(prefix):
            repair[name[len(prefix):]] = entry
    for name in ("full_setup.py", "test_full_setup.py", "README.md"):
        put(repair, name, repair[name][1].replace(b"r17", b"r18"))
    put(repair, "payload/" + NEAR, near)
    put(repair, "source/" + NEAR, near)
    put(repair, "source/navigation/test/test_optional_arrival_heading.py", optional_test)
    repair_meta = json.loads(repair["release.json"][1])
    repair_meta["allowed_releases"].append(NEW)
    repair_meta["payload_sha256"][NEAR] = digest(near)
    repair_meta["python_accepted_sha256"][NEAR].append(digest(near))
    repair_meta.update(full_bundle_adapter=VERSION, runtime_identical_to_1_0_8=False,
                       java_runtime_identical_to_1_0_8=True,
                       full_bundle_runtime_additions=["optional_heading_arrival"])
    put(repair, "release.json", json_data(repair_meta))
    write_sums(repair, "SHA256SUMS")
    verify_sums(repair, "SHA256SUMS")
    for name, (entry, data) in repair.items():
        put(outer, prefix + name, data, entry.mode)
    merged = json.loads(outer["merged-increments.json"][1])
    merged.update(runtime_release=NEW, parent_archive_sha256=BASE_SHA,
                  runtime_identical_to_shipped_1_0_8=False,
                  java_runtime_identical_to_shipped_1_0_8=True,
                  full_r18_fixes=["soc2_shared_host_key_bootstrap", "optional_heading_arrival"])
    for target in (nav, outer):
        put(target, "merged-increments.json", json_data(merged))
        for name in list(target):
            if name.startswith("docs/"):
                del target[name]
    for old_doc in ("V1.3.8-r17-完整部署教程.md", "V1.3.8-r17-已编译AIMDK新机部署教程.md"):
        # Read immutable docs from the original archive, after clearing old names.
        document = old_guides["docs/" + old_doc]
        name = old_doc.replace("r17", "r18")
        for target in (nav, outer):
            put(target, "docs/" + name, guide(document))
    for target in (nav, outer):
        put(target, "docs/严格复查说明.md", (HERE / "严格复查说明.md").read_bytes())
    release = json.loads(nav["release-info.json"][1])
    release.update(version=NEW, parent_release=OLD, created_at="2026-10-19",
                   build="20261019-r18", strict_audit_fixes=merged["full_r18_fixes"])
    put(nav, "release-info.json", json_data(release))
    update_payload_manifest(nav)
    changed = sorted(name for name, (_, data) in nav.items()
                     if name.startswith("payload/") and old_payload.get(name) != data)
    assert changed == ["payload/" + NEAR], changed
    write_sums(nav, "SHA256SUMS")
    verify_sums(nav, "SHA256SUMS")
    component = tar_data(nav, new_name)
    del outer["components/" + old_name + ".tar.gz"]
    put(outer, "components/" + new_name + ".tar.gz", component)
    manifest = json.loads(outer["bundle-manifest.json"][1])
    manifest.update(version=VERSION, navigation_release=NEW, source_archive_sha256=BASE_SHA,
                    strict_audit_fixes=merged["full_r18_fixes"])
    del manifest["components"][old_name + ".tar.gz"]
    manifest["components"][new_name + ".tar.gz"] = digest(component)
    manifest["upgrade_sources"].append(NEW)
    put(outer, "bundle-manifest.json", json_data(manifest))
    put(outer, "README.md", (
        "# 灵犀 V1.3.8 r18 完整安装包\n\n"
        "完整继承 r17 和此前有效增量；修复新机 SOC2 指纹衔接与关闭朝向检查时的近目标判定。\n"
        "Java 保留用户确认有效的 1.0.8 基线，排除失败的 1.0.9。\n\n"
        + "\n".join("- docs/" + name for name in DOCS)
        + "\n\n按外层 install_all.sh 安装；不需要叠装旧增量。实际声音仍需现场验收。\n"
    ).encode("utf-8"))
    write_sums(outer, "SHA256SUMS.txt")
    verify_sums(outer, "SHA256SUMS.txt")
    output = tar_data(outer, NAME)
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / (NAME + ".tar.gz")
    target.write_bytes(output)
    target.with_name(target.name + ".sha256").write_bytes(
        (digest(output) + "  " + target.name + "\n").encode("ascii"))
    for name in DOCS:
        (dist / name).write_bytes(outer["docs/" + name][1])
    (dist / "merged-increments.json").write_bytes(json_data(merged))
    print(json.dumps(dict(archive=str(target), sha256=digest(output), bytes=len(output),
                          changed_payload=changed), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
