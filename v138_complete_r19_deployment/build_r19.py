"""Build the V1.3.8 r19 full bundle from the verified r18 archive.

The runtime release remains r18. This release fixes only the native-tour-quiet
preflight validator, which incorrectly expected the voice bridge control script
to be listed in payload-manifest.json even though it is a runtime script.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "v138_complete_r15_deployment"))
from archive_utils import digest, json_data, normalize_text, put, read_archive
from archive_utils import tar_data, verify_sums, write_sums

BASE_NAME = "lingxi-v1.3.8-complete-20261019-r18-deployment"
NAME = "lingxi-v1.3.8-complete-20261021-r19-deployment"
VERSION = "1.3.8-complete-20261021-r19"
RUNTIME_RELEASE = "1.0.19-20261019-r18"
HELPER_PREFIX = "scripts/native-tour-quiet/"
VOICE_SCRIPT = "scripts/robot_voice_bridge_control.sh"

OLD_CHECK = """        script = self.release / VOICE_SCRIPT
        manifest = json.loads(regular(self.release / MANIFEST))
        expected = next((item for item in manifest["files"] if item["path"] == VOICE_SCRIPT), None)
        if expected is None or digest(regular(script)) != expected["sha256"]:
            raise RuntimeError("语音 bridge 控制脚本清单不一致")
"""
NEW_CHECK = """        script = self.release / VOICE_SCRIPT
        if not script.is_file():
            raise RuntimeError("语音 bridge 控制脚本不存在：" + str(script))
        expected = self.meta.get("voice_bridge_control_sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise RuntimeError("缺少语音 bridge 控制脚本 SHA-256")
        if digest(regular(script)) != expected:
            raise RuntimeError("语音 bridge 控制脚本 SHA-256 不匹配")
"""


def patch_helper(outer, voice_sha256):
    helper = {
        name[len(HELPER_PREFIX):]: entry
        for name, entry in outer.items()
        if name.startswith(HELPER_PREFIX)
    }
    install = helper["install.py"][1].decode("utf-8")
    if OLD_CHECK not in install:
        raise RuntimeError("r18 helper 中未找到旧语音 bridge 清单校验逻辑")
    helper["install.py"] = (
        helper["install.py"][0],
        install.replace(OLD_CHECK, NEW_CHECK, 1).encode("utf-8"),
    )
    meta = json.loads(helper["release.json"][1])
    meta["voice_bridge_control_sha256"] = voice_sha256
    meta["validator_fix"] = "payload-manifest-independent-voice-bridge-check"
    meta["full_bundle_adapter"] = VERSION
    put(helper, "release.json", json_data(meta))
    put(helper, "test_voice_validator.py", (HERE / "test_voice_validator.py").read_bytes())
    write_sums(helper, "SHA256SUMS")
    verify_sums(helper, "SHA256SUMS")
    for name, entry in helper.items():
        put(outer, HELPER_PREFIX + name, entry[1], entry[0].mode)
    return meta


def write_docs(outer):
    source = outer["docs/V1.3.8-r18-完整部署教程.md"][1].decode("utf-8")
    source = source.replace("V1.3.8 r18", "V1.3.8 r19")
    source = source.replace("V1.3.8-r18", "V1.3.8-r19")
    source = source.replace("交付日期：2026-10-19", "交付日期：2026-10-21")
    source = source.replace(BASE_NAME, NAME)
    source += """

## r19 校验器修复

r19 的运行版本仍为 `1.0.19-20261019-r18`，完整继承 r18 的导航、语音、
讲解静默、到点判定、TTS 回执和 SOC2 修复。本次修复的是原生讲解配置阶段的
校验器：`robot_voice_bridge_control.sh` 属于运行目录脚本，不在
`payload-manifest.json` 的 payload 文件集合中；r18 校验器错误地要求它必须在
该清单中，导致 `native-tour-quiet` 在业务文件已经安装成功后仍然失败。

r19 改为使用 helper `release.json` 中记录的脚本 SHA-256 校验运行目录中的实际
脚本，并继续执行 bridge 状态检查。不会跳过校验，也不会修改 Java、AIMDK、
地图、密钥、知识库或业务语音逻辑。

安装后必须执行：

```bash
cd /home/run/lingxi-v1.3.8-complete-20261021-r19-deployment
bash ./install_all.sh preflight
sudo sparkrobot-lingxi-internal status
```
"""
    put(
        outer,
        "docs/V1.3.8-r19-完整部署教程.md",
        source.encode("utf-8"),
    )
    new_machine = outer["docs/V1.3.8-r18-已编译AIMDK新机部署教程.md"][1].decode("utf-8")
    new_machine = new_machine.replace("V1.3.8 r18", "V1.3.8 r19")
    new_machine = new_machine.replace("V1.3.8-r18", "V1.3.8-r19")
    new_machine = new_machine.replace(BASE_NAME, NAME)
    new_machine += """

## r19 说明

新机按本文完整安装，AIMDK 已编译时不重复编译 AIMDK。首次安装后执行
`bash ./install_all.sh preflight`，确认原生讲解校验阶段通过。
"""
    put(
        outer,
        "docs/V1.3.8-r19-已编译AIMDK新机部署教程.md",
        new_machine.encode("utf-8"),
    )
    outer.pop("docs/V1.3.8-r18-完整部署教程.md", None)
    outer.pop("docs/V1.3.8-r18-已编译AIMDK新机部署教程.md", None)


def main():
    archive = HERE.parent / "v138_complete_r18_deployment/dist" / (
        BASE_NAME + ".tar.gz"
    )
    raw = archive.read_bytes()
    outer = read_archive(raw, BASE_NAME)
    verify_sums(outer, "SHA256SUMS.txt")

    component_name = (
        "components/sparkrobot-lingxi-nav-internal-" + RUNTIME_RELEASE + ".tar.gz"
    )
    nav = read_archive(outer[component_name][1], "sparkrobot-lingxi-nav-internal-" + RUNTIME_RELEASE)
    verify_sums(nav, "SHA256SUMS")
    voice_sha256 = digest(nav[VOICE_SCRIPT][1])
    meta = patch_helper(outer, voice_sha256)
    write_docs(outer)

    merged = json.loads(outer["merged-increments.json"][1])
    merged.update(
        full_bundle_version=VERSION,
        runtime_release=RUNTIME_RELEASE,
        r19_fixes=["native_tour_quiet_voice_bridge_script_hash_validation"],
        r19_runtime_unchanged=True,
    )
    put(outer, "merged-increments.json", json_data(merged))
    put(
        outer,
        "VERSION",
        f"LINGXI_COMPLETE_VERSION={VERSION}\n".encode("ascii"),
    )
    manifest = json.loads(outer["bundle-manifest.json"][1])
    manifest.update(
        version=VERSION,
        navigation_release=RUNTIME_RELEASE,
        strict_audit_fixes=[
            *manifest.get("strict_audit_fixes", []),
            "native_tour_quiet_voice_bridge_script_hash_validation",
        ],
    )
    put(outer, "bundle-manifest.json", json_data(manifest))
    put(
        outer,
        "README.md",
        (
            "# 灵犀 V1.3.8 r19 完整安装包\n\n"
            "运行版本为 1.0.19-20261019-r18，完整继承 r18 已验证功能；"
            "r19 修复 native-tour-quiet 语音 bridge 脚本清单校验缺陷。\n\n"
            "- docs/V1.3.8-r19-完整部署教程.md\n"
            "- docs/V1.3.8-r19-已编译AIMDK新机部署教程.md\n"
        ).encode("utf-8"),
    )
    write_sums(outer, "SHA256SUMS.txt")
    verify_sums(outer, "SHA256SUMS.txt")

    dist = HERE / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / (NAME + ".tar.gz")
    output.write_bytes(tar_data(outer, NAME))
    output.with_name(output.name + ".sha256").write_text(
        digest(output.read_bytes()) + "  " + output.name + "\n",
        encoding="ascii",
    )
    for name in (
        "V1.3.8-r19-完整部署教程.md",
        "V1.3.8-r19-已编译AIMDK新机部署教程.md",
    ):
        (dist / name).write_bytes(outer["docs/" + name][1])
    (dist / "merged-increments.json").write_bytes(json_data(merged))
    (dist / "交付验证结果.md").write_text(
        f"""# V1.3.8 r19 交付验证结果

- 完整包：`{output.name}`
- 完整包 SHA-256：`{digest(output.read_bytes())}`
- 运行版本：`{RUNTIME_RELEASE}`
- 修复范围：`native-tour-quiet` 语音 bridge 控制脚本校验
- 语音 bridge 控制脚本 SHA-256：`{voice_sha256}`
- r18 运行文件、Java、AIMDK、地图、密钥和知识库逻辑：保持不变
- helper SHA256SUMS：已重新生成并通过
- 外层 SHA256SUMS.txt：已重新生成并通过
- payload-manifest 不包含 scripts 条目：已验证
- 真实机器人、云端和声学硬件验收：未在本工作站执行
""",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "archive": str(output),
                "sha256": digest(output.read_bytes()),
                "bytes": output.stat().st_size,
                "voice_bridge_control_sha256": voice_sha256,
                "runtime_release": RUNTIME_RELEASE,
                "helper_meta_version": meta["version"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
