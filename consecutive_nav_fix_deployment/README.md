# 灵犀连续导航卡住增量修复包

版本：`1.0.1-20260907`

本包修复“机器人到达一个点并执行动作后，下一导航任务已提交但底盘不走”的问题，并修复 `1.0.0` 可能改错共享日志目录权限、导致网页一键启动失败的问题。

修复包不会下发测试导航，只更新 5 个 Python 文件并重启导航 bridge。安装前自动备份，启动失败自动恢复。

## 最短安装命令

```bash
cd /home/run
sha256sum -c sparkrobot-lingxi-consecutive-nav-fix-1.0.1-20260907.tar.gz.sha256
tar -xzf sparkrobot-lingxi-consecutive-nav-fix-1.0.1-20260907.tar.gz
cd sparkrobot-lingxi-consecutive-nav-fix-1.0.1-20260907
sudo bash ./install.sh
sudo sparkrobot-lingxi-consecutive-nav-fix doctor
```

`doctor` 不会让机器人移动。现场双点连续导航测试必须由现场人员确认环境安全并可随时急停后再执行。

完整说明见 `docs/连续导航卡住修复包部署说明.md`。
