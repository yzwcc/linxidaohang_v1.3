# 灵犀导航 V1.3.9 r5

完整版本：`1.3.9-complete-20261019-r5`。导航主包：`1.0.23-20261019`。

机器人使用同目录 `lingxi-v1.3.9-complete-20261019-r5-deployment.tar.gz` 与 `.sha256`。ZIP 内容相同，便于 Windows 查看。安装步骤见 [完整部署说明](builder/docs/V1.3.9-完整部署说明.md)，本地验证见 [验证记录](验证记录.md)。

最终归档及 Linux 解压复验均已通过，准确哈希与结果见 [交付校验结果](交付校验结果.md)、`archive-audit.json` 和 `extracted-validation.json`。归档、依赖和缓存保留在本地并由 `.gitignore` 排除，GitHub 保存源码、安装器、测试和校验记录。

`source/` 固定本次 V1.3.9 源码，`builder/` 保存修复后的安装器、测试及打包审计工具。主仓库顶层的历史源码不作为本次构建输入。

本次功能复核的修复、完整能力清单和实机边界见 [r5 功能复核报告](builder/docs/V1.3.9-r5-功能复核报告.md)。旧 r4 归档保留，源码与记录以当前 r5 为准；r4 原始源码记录在 Git 提交 `e899dcd`。

## 重建顺序

在 Windows 工作站准备 Python、Node、JDK 17、Maven 和 WSL；保留经过校验的 2026-10-15 r3 交付目录作为第三方依赖输入。

```text
source/server: npm ci --ignore-scripts --no-audit --no-fund
source/server: npm test
source/server: npm run build
python -X utf8 builder/check_java.py
python -X utf8 builder/check_sdk_contract.py
source/navigation: python -X utf8 -m pytest -q test
PYTHONPATH=source/navigation: python -X utf8 -m pytest -q builder/functional_task_check.py
python -X utf8 -m unittest discover -s builder -p test_*.py
WSL root: python3 -m unittest discover -s builder -p test_*.py
python -X utf8 builder/build_delivery.py stage
```

准备真实验证记录及 `validation-results.json`，核对暂存包后执行 `finalize` 和 `audit_delivery.py`。构建器拒绝覆盖已有暂存目录或归档；重建时先保留原目录并明确使用新的工作目录。不要重新运行 `prepare_source.py` 覆盖已修复源码。

`builder/check_packaged.py` 对暂存或解压后的主包运行回归，`builder/verify_extracted.sh` 在 Linux root 下解压最终完整包并验证安装生命周期，测试仅使用临时根目录和模拟服务。浏览器测试使用 `source/server/test/consoleVisual.mjs`，可通过 `PLAYWRIGHT_MODULE` 指定已安装 Playwright 的入口，并通过 `VISUAL_OUTPUT_DIR` 指定证据目录。

未完成机器人实机验收。具体边界见验证记录，不能将本地测试等同于现场功能全部正常。
