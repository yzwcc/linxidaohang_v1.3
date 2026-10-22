# V1.3.8 r20 讲解任务首点修复

针对讲解任务概率性跳过首点，修复步骤启动拒绝后的推进、旧回调串入新任务和并发启动竞态。

本包仅适配 `lingxi-v1.3.8-complete-20261021-r20-deployment`；
运行 release 必须为 `1.0.20-20261021-r20`，且 bridge/client 与已验证基线一致。
未连接现场机器人，本次交付为代码修复与离线回归结果，不能替代现场验收。

部署、检查及回滚请阅读 [部署说明](docs/部署说明.md)。

## 开发复现

需要本仓库已有的 r20 完整压缩包和 Python 3.10+，测试机需要 pytest。

```bash
python build.py --prepare-only
python -m pytest tests/test_first_step_race.py tests/test_r20_compatibility.py tests/test_patch_scope.py -q
python build.py
python -m unittest discover -s tests -p test_install.py -v
```

`build.py` 校验完整包 SHA256，并从发货包提取依赖；不使用工作区中未发布的导航源码。
Linux 安装事务测试应在 Linux/WSL 上执行。构建产物位于 `dist/`，不进入 Git。
