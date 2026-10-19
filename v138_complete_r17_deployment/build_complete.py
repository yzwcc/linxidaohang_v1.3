"""Build r17 only from verified immutable full and cumulative patch archives."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "v138_complete_r15_deployment"))
from archive_utils import (digest, json_data, normalize_text, put, read_archive,
                           tar_data, update_payload_manifest, verify_sums, write_sums)

OLD = "1.0.19-20261019-r16"
NEW = "1.0.19-20261019-r17"
BASE_NAME = "lingxi-v1.3.8-complete-20261019-r16-deployment"
NAME = "lingxi-v1.3.8-complete-20261019-r17-deployment"
VERSION = "1.3.8-complete-20261019-r17"
BASE_SHA = "1f17375157f06334ac95ec5836020136005dc178aba4be9de573080b30857069"
FIX_NAME = "sparkrobot-lingxi-v1.3.8-native-tour-quiet-1.0.10-20261019"
FIX_SHA = "4f663936aecd297be378967dd1c16a623d6da7d1ede892c7ee5ae391c955afe6"
DOCS = ("V1.3.8-r17-完整部署教程.md", "V1.3.8-r17-已编译AIMDK新机部署教程.md",
        "V1.3.8-r17-合并修复与验收.md")
ADDITIONAL_INPUTS = (
    ("v138_navigation_tour_fix_deployment", "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.6-20261019",
     "c65d76143ee4db799e23a06af2028798839f7091bf8ae645586a5c11c77bccae"),
    ("v138_arrival_tts_fix_deployment", "sparkrobot-lingxi-v1.3.8-arrival-tts-fix-1.0.7-20261019",
     "6fbe5bcac4834ede2bb1643570e20d38afa1129b020234c1396755d45b6e084a"),
    ("v138_silent_control_fix_deployment", "sparkrobot-lingxi-v1.3.8-silent-control-fix-1.0.8-20261019",
     "6a7b596ee572ddf4741635a50f21e8564585f697beae3f9990654a01e36fb07e"),
)


def immutable(path, expected, prefix):
    raw = path.read_bytes()
    assert digest(raw) == expected, str(path)
    assert path.with_name(path.name + ".sha256").read_bytes() == (
        expected + "  " + path.name + "\n").encode("ascii")
    return read_archive(raw, prefix)


def outer_installer(data):
    text = data.decode("utf-8").replace("20261019-r16", "20261019-r17")
    text = text.replace("V1.3.8 r16", "V1.3.8 r17")
    marker = "install_product() {\n"
    helper = '''native_tour_setup() {
  sudo python3 "${PACKAGE_DIR}/scripts/native-tour-quiet/full_setup.py" "$@"
}

'''
    assert text.count(marker) == 1
    text = text.replace(marker, helper + marker)
    before = '  local robot_ip\n  robot_ip="$(hostname -I'
    assert before in text
    text = text.replace(before, '  echo "[3/3] 检查双板原生讲解技能配置"\n'
                        '  native_tour_setup\n\n' + before)
    before = '  echo "[通过] V1.3.8 r17 升级完成'
    assert before in text
    text = text.replace(before, '  native_tour_setup\n' + before)
    before = '  echo "安装预检通过。'
    assert before in text
    text = text.replace(before, '  native_tour_setup --check\n' + before)
    before = '  echo "完整套件验收通过。'
    assert before in text
    text = text.replace(before, '  native_tour_setup --check\n' + before)
    before = '|/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261017-r15|"${EXPECTED_RELEASE}") ;;'
    assert before in text
    text = text.replace(before, '|/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261017-r15|'
                        '/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261019-r16|"${EXPECTED_RELEASE}") ;;')
    text = text.replace("r11、r12、r13、r14、r15 或当前 r16",
                        "r11、r12、r13、r14、r15、r16 或当前 r17")
    marker = '  repair-permissions)\n'
    assert marker in text
    text = text.replace(marker, '''  native-tour-quiet)
    shift; [[ $# -eq 0 ]] || die "native-tour-quiet 不接受参数"
    need_run; need_arm64; verify_bundle; native_tour_setup
    ;;
''' + marker)
    text = text.replace("|preflight|doctor|repair-permissions",
                        "|preflight|doctor|native-tour-quiet|repair-permissions")
    return text.encode("utf-8")


def main():
    outer = immutable(ROOT / "v138_complete_r16_deployment/dist" / (BASE_NAME + ".tar.gz"),
                      BASE_SHA, BASE_NAME)
    verify_sums(outer, "SHA256SUMS.txt")
    fix = immutable(ROOT / "v138_native_tour_quiet_deployment/dist" / (FIX_NAME + ".tar.gz"),
                    FIX_SHA, FIX_NAME)
    verify_sums(fix, "SHA256SUMS")
    old_component = "sparkrobot-lingxi-nav-internal-" + OLD
    new_component = "sparkrobot-lingxi-nav-internal-" + NEW
    nav = read_archive(outer["components/" + old_component + ".tar.gz"][1], old_component)
    verify_sums(nav, "SHA256SUMS")
    before_payload = {n: data for n, (_, data) in nav.items() if n.startswith("payload/")}
    meta = json.loads(fix["release.json"][1])
    assert meta["runtime_identical_to_1_0_8"] is True
    for relative, expected in meta["payload_sha256"].items():
        data = fix["payload/" + relative][1]
        assert digest(data) == expected
        put(nav, "payload/" + relative, data, 0o644)
    # Replace complete snapshots, including tests, with the archive's effective source.
    for name in list(nav):
        if name.startswith("source/java/src/") and name.endswith(".java"):
            del nav[name]
    for name, (_, data) in fix.items():
        if name.startswith("source/java/main/"):
            put(nav, name.replace("source/java/main/", "source/java/src/main/java/", 1), data, 0o644)
        elif name.startswith("source/java/test/"):
            put(nav, name.replace("source/java/test/", "source/java/src/test/java/", 1), data, 0o644)
        elif name.startswith("source/navigation/"):
            put(nav, name, data, 0o644)
    for name, (_, data) in list(nav.items()):
        if name.startswith("payload/navigation/") and name.endswith(".py"):
            put(nav, name.replace("payload/navigation/", "source/navigation/", 1), data, 0o644)
    for name in list(outer):
        if name.startswith("tests/navigation/") and name.endswith(".py"):
            del outer[name]
    for name, (_, data) in fix.items():
        if name.startswith("source/navigation/"):
            put(outer, name.replace("source/navigation/", "tests/navigation/", 1), data, 0o644)
    increments = json.loads(outer["merged-increments.json"][1])
    history = list(increments["all_post_r15_increments"])
    for folder, archive_name, expected in ADDITIONAL_INPUTS:
        prior = immutable(ROOT / folder / "dist" / (archive_name + ".tar.gz"), expected, archive_name)
        verify_sums(prior, "SHA256SUMS")
        prior_meta = json.loads(prior["release.json"][1])
        assert prior_meta["sdk_sha256"] == meta["sdk_sha256"]
        assert prior_meta["patched_jar_sha256"] in meta["superseded_jar_sha256"]
        history.append(dict(package=archive_name, sha256=expected,
                            jar_sha256=prior_meta["patched_jar_sha256"], version=prior_meta["version"]))
    history.append(dict(package=FIX_NAME, sha256=FIX_SHA, jar_sha256=meta["patched_jar_sha256"],
                        version=meta["version"], runtime_identical_to_1_0_8=True))
    merged = {**increments, "runtime_release": NEW, "parent_archive_sha256": BASE_SHA,
              "all_post_r15_increments": history,
              "effective_increment": FIX_NAME, "effective_increment_sha256": FIX_SHA,
              "effective_jar_sha256": meta["patched_jar_sha256"],
              "increment_release": meta, "runtime_identical_to_shipped_1_0_8": True,
              "native_mapping_repair_integrated": True, "excluded_failed_runtime": "1.0.9",
              "separate_increment_install_required": False,
              "cumulative_additions": [
                  "1.0.6 saved-point navigation and tour routing",
                  "1.0.7 bounded near-goal arrival and truthful async TTS receipt",
                  "1.0.8 silent navigation/tour control replies",
                  "1.0.10 historical native tour mapping transaction on SOC1/SOC2"]}
    for name, (_, data) in list(nav.items()):
        if not name.startswith(("payload/", "source/", "docs/")) and name.endswith((".sh", ".json", ".env", ".service", ".md")):
            put(nav, name, data.replace(OLD.encode(), NEW.encode()))
    release = json.loads(nav["release-info.json"][1])
    release.update(version=NEW, parent_release=OLD, created_at="2026-10-19",
                   build="20261019-r17", merged_control_reply_fix="1.0.8-20261019",
                   native_tour_setup="outer install_all.sh / 1.0.10")
    put(nav, "release-info.json", json_data(release))
    put(nav, "merged-increments.json", json_data(merged))
    for name in list(nav):
        if name.startswith("docs/"):
            del nav[name]
    for doc in DOCS:
        put(nav, "docs/" + doc, normalize_text(doc, (HERE / doc).read_bytes()), 0o644)
    update_payload_manifest(nav)
    payload_manifest = json.loads(nav["payload-manifest.json"][1])
    payload_manifest.update(control_reply_fix="1.0.8-20261019",
                            arrival_tts_fix="1.0.7-20261019",
                            native_tour_fix="complete_installer_1.0.10-20261019")
    put(nav, "payload-manifest.json", json_data(payload_manifest))
    changes = sorted(n for n, (_, data) in nav.items() if n.startswith("payload/")
                     and before_payload.get(n) != data)
    assert changes == sorted("payload/" + p for p in meta["payload_sha256"]), changes
    write_sums(nav, "SHA256SUMS")
    verify_sums(nav, "SHA256SUMS")
    component = tar_data(nav, new_component)
    old_control = outer["control_all.sh"][1]
    old_install = outer["install_all.sh"][1]
    del outer["components/" + old_component + ".tar.gz"]
    put(outer, "components/" + new_component + ".tar.gz", component)
    for name, (_, data) in list(outer.items()):
        if (name.startswith(("components/", "docs/", "scripts/control-r"))
                or name in {"bundle-manifest.json", "merged-increments.json"}):
            continue
        if name.endswith((".sh", ".md", ".json")) or name == "VERSION":
            put(outer, name, data.replace(b"20261019-r16", b"20261019-r17")
                .replace(b"V1.3.8 r16", b"V1.3.8 r17"))
    put(outer, "install_all.sh", outer_installer(old_install), 0o755)
    put(outer, "scripts/control-r16.sh", old_control, 0o755)
    put(outer, "merged-increments.json", json_data(merged), 0o644)
    # Keep the original transaction implementation. Extend only compatibility
    # metadata; the full adapter insists on exact r17 payload bytes.
    repair = dict(fix)
    repair_meta = {**meta, "allowed_releases": [*meta["allowed_releases"], NEW],
                   "full_bundle_adapter": VERSION}
    put(repair, "release.json", json_data(repair_meta), 0o644)
    for filename in ("full_setup.py", "test_full_setup.py"):
        put(repair, filename, normalize_text(filename, (HERE / filename).read_bytes()), 0o644)
    put(repair, "README.md", (
        "# 完整包内置原生配置辅助程序\n\n"
        "请使用外层 install_all.sh install / upgrade / native-tour-quiet。"
        "full_setup.py 限定 r17 并验证已合入的准确运行字节，双板均无历史映射时明确报告不适用。\n\n"
        "install.py、native_tour_quiet.py、arrival_runtime.py、java_runtime.py 保留 1.0.10 "
        "已交付实现。原有部署说明为归档参考，完整安装以外层 r17 教程为准。\n\n"
        "原生事务回滚使用安装时打印的 install.py --rollback 实际事务文件命令；"
        "先恢复原生配置，再切换旧运行版本。\n"
    ).encode("utf-8"), 0o644)
    write_sums(repair, "SHA256SUMS")
    verify_sums(repair, "SHA256SUMS")
    for name, (entry, data) in repair.items():
        put(outer, "scripts/native-tour-quiet/" + name, data, entry.mode)
    for name in list(outer):
        if name.startswith("docs/"):
            del outer[name]
    for doc in DOCS:
        put(outer, "docs/" + doc, normalize_text(doc, (HERE / doc).read_bytes()), 0o644)
    put(outer, "README.md", (
        "# 灵犀 V1.3.8 r17 完整安装包\n\n"
        "累计合并 r16、1.0.6/1.0.7/1.0.8 的有效运行改动和 1.0.10 原生讲解映射处理。"
        "排除现场失败的 1.0.9 运行逻辑。新机不需要旧完整包或独立增量。\n\n"
        + "\n".join("- docs/" + doc for doc in DOCS)
        + "\n\n使用外层 install_all.sh 完整安装。单装 components 内部主包会跳过知识库与双板原生映射阶段。\n"
    ).encode("utf-8"), 0o644)
    manifest = json.loads(outer["bundle-manifest.json"][1])
    manifest.update(version=VERSION, navigation_release=NEW, created_at="2026-10-19",
                    source_archive_sha256=BASE_SHA, effective_control_reply_fix="1.0.8-20261019",
                    effective_native_tour_fix="1.0.10-20261019",
                    native_tour_mapping_requires_field_acceptance=True,
                    cumulative_patch_archive_sha256=FIX_SHA, excluded_failed_runtime="1.0.9")
    manifest["components"].pop(old_component + ".tar.gz")
    manifest["components"][new_component + ".tar.gz"] = digest(component)
    manifest["merged_post_r15_increments"] = history
    manifest["upgrade_sources"].append(NEW)
    manifest["installation_order"].insert(4, "configure_native_tour_mapping_soc1_soc2")
    manifest["merged_capabilities"].extend([
        "r17_saved_point_navigation_and_tour_routing",
        "r17_bounded_near_goal_arrival_with_odom_fallback",
        "r17_async_tts_receipt_and_bounded_priority_conflict_handling",
        "r17_voice_navigation_tour_start_silent_results",
        "r17_soc1_soc2_native_tour_mapping_transaction_and_guarded_rollback"])
    put(outer, "bundle-manifest.json", json_data(manifest))
    for name, (_, data) in list(outer.items()):
        if not name.startswith(("components/", "scripts/native-tour-quiet/")):
            put(outer, name, normalize_text(name, data))
    write_sums(outer, "SHA256SUMS.txt")
    verify_sums(outer, "SHA256SUMS.txt")
    output = tar_data(outer, NAME)
    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / (NAME + ".tar.gz")
    target.write_bytes(output)
    target.with_name(target.name + ".sha256").write_bytes(
        (digest(output) + "  " + target.name + "\n").encode("ascii"))
    for doc in DOCS:
        (dist / doc).write_bytes(outer["docs/" + doc][1])
    (dist / "merged-increments.json").write_bytes(json_data(merged))
    print(json.dumps(dict(archive=str(target), sha256=digest(output), bytes=len(output),
                          changed_runtime=changes), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
