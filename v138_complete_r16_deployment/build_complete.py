"""Merge the shipped cumulative control fix into the immutable r15 full bundle."""
import argparse
import io
import json
from pathlib import Path
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "v138_complete_r15_deployment"))
from archive_utils import digest, json_data, normalize_text, put, read_archive, tar_data, update_payload_manifest, verify_sums, write_sums

OLD = "1.0.19-20261017-r15"
NEW = "1.0.19-20261019-r16"
BASE_NAME = "lingxi-v1.3.8-complete-20261017-r15-deployment"
NAME = "lingxi-v1.3.8-complete-20261019-r16-deployment"
VERSION = "1.3.8-complete-20261019-r16"
BASE_SHA = "9d56d4718c4e5d103fa8ececd5110e0fd93cb98bc10a66360f35b4ffa0012b68"
FIX_NAME = "sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.5-20261019"
FIX_SHA = "ad64b0fdcfba2ce905efbb902180ce356351e44fb36bc4563cc3942c246e158d"
DOCS = ("V1.3.8-r16-完整部署说明.md", "V1.3.8-r16-已编译AIMDK新机完整部署说明.md",
        "V1.3.8-r16-累计修复与验收说明.md")
FEATURES = (
    "控制确认按真实回执分配，避免执行成功又追加失败；重复回调不重复执行",
    "动作底层拒绝和确认超时不再转为低层失败播报，保留日志与真实状态",
    "迟到回调归属、空/损坏/未知回执、HTTP 故障处理与急停保护",
    "打招呼意图补偿、原生唱歌跳舞协同、残句和无关自我介绍过滤",
    "SDK 控制回复完成帧和本机播报通道修复",
    "按 V1.3.1 实际通道修正导航/讲解 SDK 中断、完成顺序和重复文本",
)


def sidecar(path, data):
    assert path.with_name(path.name + ".sha256").read_bytes() == (
        digest(data) + "  " + path.name + "\n").encode("ascii"), path


def build(base):
    raw = base.read_bytes()
    assert digest(raw) == BASE_SHA, "r15 baseline archive mismatch"
    sidecar(base, raw)
    outer = read_archive(raw, BASE_NAME)
    verify_sums(outer, "SHA256SUMS.txt")
    old_component = "sparkrobot-lingxi-nav-internal-" + OLD
    new_component = "sparkrobot-lingxi-nav-internal-" + NEW
    nav = read_archive(outer["components/" + old_component + ".tar.gz"][1], old_component)
    verify_sums(nav, "SHA256SUMS")
    original_payload = {n: data for n, (_, data) in nav.items() if n.startswith("payload/")}
    fix_dir = ROOT / "v138_control_reply_fix_deployment/dist"
    increments = []
    final = None
    for index in range(6):
        name = f"sparkrobot-lingxi-v1.3.8-control-reply-fix-1.0.{index}-" + ("20261019" if index == 5 else "20261018")
        path = fix_dir / (name + ".tar.gz")
        data = path.read_bytes()
        sidecar(path, data)
        if index == 5:
            assert name == FIX_NAME and digest(data) == FIX_SHA
        files = read_archive(data, name)
        verify_sums(files, "SHA256SUMS")
        metadata = json.loads(files["release.json"][1])
        assert metadata["product"] == "V1.3.8"
        assert metadata["changed_runtime_files"] == ["java/robot-local-navigation-java-all.jar"]
        assert metadata["baseline_jar_sha256"] == digest(original_payload["payload/java/robot-local-navigation-java-all.jar"])
        assert metadata["patched_jar_sha256"] == digest(files["payload/java/robot-local-navigation-java-all.jar"][1])
        assert metadata["sdk_sha256"] == digest(nav["payload/java/linksoul-agentsdk-1.4.0.jar"][1])
        increments.append({"package": name, "sha256": digest(data),
                           "jar_sha256": metadata["patched_jar_sha256"],
                           "cumulative_function": FEATURES[index]})
        final = files
    metadata = json.loads(final["release.json"][1])
    assert {p["jar_sha256"] for p in increments[:-1]} == set(metadata["superseded_jar_sha256"])
    jar = final["payload/java/robot-local-navigation-java-all.jar"][1]
    with zipfile.ZipFile(io.BytesIO(jar)) as archive:
        assert archive.testzip() is None
    put(nav, "payload/java/robot-local-navigation-java-all.jar", jar, 0o644)
    # Source and tests come from the delivered increment, never the dirty worktree.
    for name, (_, data) in final.items():
        if name.startswith("source/") and name.endswith(".java"):
            target = "source/java/src/main/java/" + name[len("source/"):]
        elif name.startswith("tests/") and name.endswith(".java"):
            target = "source/java/src/test/java/" + name[len("tests/"):]
        else:
            continue
        put(nav, target, normalize_text(target, data), 0o644)
    merged = {"product": "V1.3.8", "runtime_release": NEW, "baseline_sha256": BASE_SHA,
              "all_post_r15_increments": increments, "effective_increment": FIX_NAME,
              "effective_jar_sha256": digest(jar), "increment_release": metadata,
              "separate_increment_install_required": False}
    put(nav, "merged-increments.json", json_data(merged), 0o644)
    for name, (_, data) in list(nav.items()):
        if name.startswith(("source/", "payload/", "docs/")) or name == "merged-increments.json":
            continue
        if name.endswith((".sh", ".json", ".md", ".service", ".env")):
            put(nav, name, data.replace(OLD.encode(), NEW.encode()))
    release = json.loads(nav["release-info.json"][1])
    release.update(version=NEW, parent_release=OLD, created_at="2026-10-19",
                   build="20261019-r16", merged_control_reply_fix="1.0.5-20261019")
    put(nav, "release-info.json", json_data(release))
    for name in list(nav):
        if name.startswith("docs/"):
            del nav[name]
    for name in DOCS:
        put(nav, "docs/" + name, normalize_text(name, (HERE / name).read_bytes()), 0o644)
    update_payload_manifest(nav)
    changed = sorted(n for n, (_, data) in nav.items()
                     if n.startswith("payload/") and original_payload.get(n) != data)
    assert changed == ["payload/java/robot-local-navigation-java-all.jar"]
    assert set(original_payload) == {n for n in nav if n.startswith("payload/")}
    write_sums(nav, "SHA256SUMS")
    verify_sums(nav, "SHA256SUMS")
    component = tar_data(nav, new_component)
    previous_control = outer["control_all.sh"][1]
    del outer["components/" + old_component + ".tar.gz"]
    put(outer, "components/" + new_component + ".tar.gz", component)
    for name, (_, data) in list(outer.items()):
        if name.startswith(("components/", "docs/", "scripts/control-r")) or name == "bundle-manifest.json":
            continue
        if name.endswith((".sh", ".md", ".json")) or name == "VERSION":
            put(outer, name, data.replace(b"20261017-r15", b"20261019-r16")
                .replace(b"V1.3.8 r15", b"V1.3.8 r16"))
    installer = outer["install_all.sh"][1].decode("utf-8")
    before = '/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261017-r14|"${EXPECTED_RELEASE}") ;;'
    after = '/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261017-r14|/opt/sparkrobot/lingxi-nav/releases/1.0.19-20261017-r15|"${EXPECTED_RELEASE}") ;;'
    assert before in installer
    installer = installer.replace(before, after).replace("r11、r12、r13、r14 或当前 r15",
                                                        "r11、r12、r13、r14、r15 或当前 r16")
    put(outer, "install_all.sh", installer.encode("utf-8"), 0o755)
    put(outer, "scripts/control-r15.sh", previous_control, 0o755)
    put(outer, "merged-increments.json", json_data(merged), 0o644)
    for name in list(outer):
        if name.startswith("docs/"):
            del outer[name]
    for name in DOCS:
        data = normalize_text(name, (HERE / name).read_bytes())
        put(outer, "docs/" + name, data, 0o644)
    put(outer, "README.md", (
        "# 灵犀 V1.3.8 r16 完整部署包\n\n"
        "包含 r15 完整基线及此后控制回复修复 1.0.0～1.0.5 的累计修复，"
        "不需要先安装旧版或另装增量。\n\n"
        "首次含 AIMDK 编译：docs/" + DOCS[0] + "\n\n"
        "已编译 AIMDK 新机：docs/" + DOCS[1] + "\n\n"
        "合并明细与验收：docs/" + DOCS[2] + "\n\n"
        "版本/校验来源：merged-increments.json\n"
    ).encode("utf-8"), 0o644)
    manifest = json.loads(outer["bundle-manifest.json"][1])
    manifest.update(version=VERSION, navigation_release=NEW, created_at="2026-10-19",
                    runtime_baseline="1.3.8-complete-20261017-r15", source_archive_sha256=BASE_SHA,
                    effective_control_reply_fix="1.0.5-20261019")
    manifest["components"].pop(old_component + ".tar.gz")
    manifest["components"][new_component + ".tar.gz"] = digest(component)
    manifest["upgrade_sources"] = ["1.0.19-20261017-r" + str(n) for n in range(11, 16)] + [NEW]
    manifest["merged_post_r15_increments"] = increments
    manifest["merged_capabilities"].extend([
        "r16_control_reply_fix_1_0_0_to_1_0_5_cumulative",
        "r16_model_native_performance_and_greeting_intent_reconciliation",
        "r16_silent_low_level_motion_failure_speech_real_diagnostics_preserved",
        "r16_control_callback_ownership_and_ack_validation",
        "r16_reference_v131_nav_tour_no_sdk_interrupt_single_reply_owner",
        "r16_sdk_completion_before_local_tts_and_completion_fault_isolation",
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
    target.with_name(target.name + ".sha256").write_bytes((digest(output) + "  " + target.name + "\n").encode("ascii"))
    for name in DOCS:
        (dist / name).write_bytes(outer["docs/" + name][1])
    (dist / "merged-increments.json").write_bytes(json_data(merged))
    print("ARCHIVE=" + str(target), flush=True)
    print("SHA256=" + digest(output), flush=True)
    print("MERGED=1.0.0,1.0.1,1.0.2,1.0.3,1.0.4,1.0.5", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=ROOT / "v138_complete_r15_deployment/dist" / (BASE_NAME + ".tar.gz"))
    build(parser.parse_args().baseline)
