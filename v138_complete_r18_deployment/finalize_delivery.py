"""Publish r18 reports only when Windows and Linux verified the final hash."""
import json
from pathlib import Path

from build_complete import DOCS, NAME, NEW, digest

HERE = Path(__file__).resolve().parent


def main():
    target = HERE / "dist" / (NAME + ".tar.gz")
    expected = digest(target.read_bytes())
    reports = {}
    for platform in ("Windows", "Linux"):
        raw = (HERE / "qa" / (platform.lower() + ".json")).read_bytes()
        value = json.loads(raw)
        assert value["status"] == "passed" and value["tested_archive_sha256"] == expected
        assert value["runtime_changes"] == ["navigation/robot_local_navigation_v3/near_goal_arrival.py"]
        assert value["java_and_console_identical_to_r17"] is True
        assert value["physical_robot"] == "not_run"
        reports[platform] = value
        (HERE / "dist" / ("验证结果-" + platform + ".json")).write_bytes(raw)
    windows, linux = reports["Windows"], reports["Linux"]
    assert windows["r17_heading_negative_control"] == "2 of 8 failed as expected"
    assert linux["r17_ssh_negative_control"] == "4 of 5 failed as expected"
    assert len(windows["java_regression"]) == 17 and "191 passed" in windows["python_regression"]
    assert windows["server_regression"]["numPassedTests"] == 74
    assert linux["native_and_full_adapter_tests"] >= 104
    assert len(linux["lifecycle"]) == 8 and len(linux["outer_orchestration"]) == 8
    text = f"""# V1.3.8 r18 交付验证结果

日期：2026-10-19。内部运行版本 `{NEW}`。

本轮严格复查发现两处真实缺陷，已修复后重新生成完整包：新机 SOC2 SSH 主机指纹路径不一致；
显式关闭朝向检查时近目标到达分支永不完成。旧 r17 的独立复现分别出现四项和两项预期失败，
同一套测试在最终 r18 交付归档运行通过。

完整包：`{target.name}`

SHA-256：`{expected}`

大小：{target.stat().st_size:,} 字节。

| 验证项目 | 结果 |
| --- | --- |
| 最终外部 SHA、外层/组件/原生辅助程序全覆盖校验 | 通过 |
| Java JAR、网页运行产物与 r17 | 字节一致 |
| Java 源码重建与 JAR 对比 | {windows['java_source_runtime_classes']} 个类一致 |
| 导航/讲解静默、动作回执、模型故障、唤醒/拒识、SDK 通道 | 17 组 Java 测试通过 |
| Python 导航及讲解状态机 | 191 项通过 |
| 网页服务 | 74 项 Node 测试通过 |
| 真实 Node→Python HTTP 任务保存/重读 | 通过 |
| SOC2 首次配置、重复配置、自定义密钥、主机指纹变化、运行账户失败 | 5 项通过 |
| Linux 原生配置与安装/回滚故障测试 | {linux['native_and_full_adapter_tests']} 项通过 |
| 完整入口编排与失败传播 | 8 个场景通过 |
| 首次组件安装及 r11～r17 升级/重装/回滚/配置保留 | 8 个场景通过 |
| Linux 跨用户存储权限、语音子进程、AIMDK 失败恢复 | 通过 |
| Bash 脚本与教程命令语法 | {linux['bash_scripts']} 份脚本、{linux['guide_blocks']} 个代码块通过 |
| ARM64 Java/Node、JAR CRC、Python 编译 | 通过 |
| AIMDK 和知识库组件 | 与 r17 一致 |

业务载荷仅修改 `near_goal_arrival.py`；安装脚本仅修改 `setup_soc2_access.sh`。
相关内置辅助程序的载荷指纹与源码同步更新，版本元数据与三份教程同步。有效累计增量保留，
失败的 1.0.9 仍排除。正常配置的朝向限制、停车速度、定位新鲜度、连续稳定时间未放宽。

没有发现本轮离线回归覆盖范围内的其他确定性问题。未连接客户机器人、未使用客户平台凭据，
真实认证、运动、麦克风和声音仍待现场验收。尤其“开始讲解”的厂家底层错误声音，
目前只有映射配置处理和协议验证，不能把离线通过当作已经实机消音。

完整部署和已编译 AIMDK 新机教程均随包提供；直接安装完整包，无需叠加旧增量。
两份 JSON 报告绑定上述最终归档 SHA。包内外三份文档已逐字节核对。
"""
    for path in (HERE / "交付验证结果.md", HERE / "dist/交付验证结果.md"):
        path.write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps(dict(archive=str(target), sha256=expected, bytes=target.stat().st_size,
                          windows="passed", linux="passed", docs=list(DOCS)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
