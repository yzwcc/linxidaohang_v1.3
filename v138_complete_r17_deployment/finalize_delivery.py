"""Publish reports only after both platforms verify the exact final archive."""
import json
from pathlib import Path

from build_complete import DOCS, NAME, digest

HERE = Path(__file__).resolve().parent


def main():
    archive = HERE / "dist" / (NAME + ".tar.gz")
    expected = digest(archive.read_bytes())
    reports = {}
    for platform in ("Windows", "Linux"):
        raw = (HERE / "qa" / (platform.lower() + ".json")).read_bytes()
        report = json.loads(raw)
        assert report["status"] == "passed" and report["tested_archive_sha256"] == expected
        assert len(report["runtime_changes"]) == 6 and report["excluded_1_0_9"] is True
        assert report["cumulative_runtime_matches_1_0_10"] is True
        assert len(report["effective_increment_history"]) == 10
        assert report["physical_robot"] == "not_run"
        reports[platform] = report
        (HERE / "dist" / ("验证结果-" + platform + ".json")).write_bytes(raw)
    windows, linux = reports["Windows"], reports["Linux"]
    assert len(windows["java_regression"]) == 17
    assert "183 passed" in windows["python_regression"]
    assert windows["server_regression"]["numPassedTests"] == 74
    assert linux["native_and_full_adapter_tests"] >= 104
    assert len(linux["outer_orchestration"]) == 8
    assert len(linux["lifecycle"]) == 8
    for doc in DOCS:
        assert (HERE / doc).read_bytes() == (HERE / "dist" / doc).read_bytes()
    text = f"""# V1.3.8 r17 完整包交付验证结果

日期：2026-10-19。产品版本 V1.3.8；内部运行版本 1.0.19-20261019-r17。

完整包：`{archive.name}`

SHA-256：`{expected}`

大小：{archive.stat().st_size:,} 字节。

## 最终归档验证

| 项目 | 结果 |
| --- | --- |
| 外部 SHA、外层/导航组件/原生辅助程序全覆盖校验清单 | 通过 |
| Java JAR 与累计 1.0.8/1.0.10 运行字节 | 一致 |
| Java 源码重编译与 JAR 对比 | {windows['java_source_runtime_classes']} 个类一致 |
| 导航 Python 与累计补丁/包内源码 | 一致 |
| 网页 TypeScript/静态资源重建与运行 dist | 一致 |
| Java SDK、模型、路由、控制静音及 TTS 回归 | 17 组通过 |
| Python 导航回归 | 183 项通过 |
| Node 网页回归 | 74 项通过 |
| 真实 Node→Python HTTP 讲解任务落盘/重读 | 通过 |
| Linux 原生配置/增量事务/完整包适配器故障测试 | {linux['native_and_full_adapter_tests']} 项通过 |
| 完整安装/升级/预检/doctor 的原生阶段及失败传播 | 8 个场景通过 |
| Linux 首次组件安装、r11～r16 更新/重装/回滚与配置保留 | 8 个场景通过 |
| Java 服务账户访问、启动路径、动作回执与进程协调 | 通过 |
| Linux 地图存储权限、语音进程、AIMDK 失败恢复 | 通过 |
| ARM64 Java/Node 运行时、JAR CRC、Python 编译 | 通过 |
| Bash 脚本/文档命令语法 | {linux['bash_scripts']} 份脚本、{linux['guide_blocks']} 个教程代码块通过 |
| AIMDK 与知识库压缩组件 | 与 r16 完全一致 |

相对 r16 的业务载荷只改变应用 JAR 和 5 个导航/TTS Python 文件；运行文件总数 {windows['runtime_files']}。
所有修复直接合入运行组件，原生配置事务纳入完整安装入口，无需另行叠装增量。
失败的 1.0.9 运行改动未纳入；构建输入使用不可变交付归档，不读取含回归改动的候选源码。

## 实机边界

未连接客户机器人，未验证真实云认证、肢体运动或声学效果。1.0.8 导航静音依据用户历史反馈；
1.0.10 原生讲解映射处理仍需按教程验收。没有映射时明确报告不适用，不能据此声称错误声音已消失。

两份 JSON 报告记录原始检查结果并绑定本完整包 SHA。包内三份教程的字节已与外部交付文件核对。

## 复现

```text
python -X utf8 v138_complete_r17_deployment/build_complete.py
python -X utf8 v138_complete_r17_deployment/verify_delivery.py --report v138_complete_r17_deployment/qa/windows.json
python3 v138_complete_r17_deployment/verify_delivery.py --report v138_complete_r17_deployment/qa/linux.json --linux
python -X utf8 v138_complete_r17_deployment/finalize_delivery.py
```

Linux 安装验证在隔离根目录中以 root 执行，系统/设备边界使用替身。
"""
    for path in (HERE / "交付验证结果.md", HERE / "dist/交付验证结果.md"):
        path.write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps(dict(archive=str(archive), sha256=expected, bytes=archive.stat().st_size,
                          windows="passed", linux="passed"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
