# 灵犀连续导航卡住增量修复包

版本：`1.0.7-20261024-r17`

本包修复“机器人到达一个点并执行动作后，下一导航任务已提交但底盘不走”的问题，并修复 `1.0.0` 可能改错共享日志目录权限、导致网页一键启动失败的问题。`1.0.2` 同时加入语音预置动作桥接和拒识校验；`1.0.5` 增加流式 TTS 原子派发、停止墓碑和抢占保护；`1.0.6-r17` 增加 V1.3.1 全局路径净空保护，避免参考线贴近静态障碍物后触发局部避障。

修复包不会下发测试导航或动作，只更新 9 个 Python 文件并重启导航 bridge。新增活动地图语音点位别名歧义清单及 TTS 流式序号/派发协议，安装前自动备份，启动失败自动恢复。流式回复默认关闭；AIMDK PlayTts 队列行为必须在目标机器人上验证后才能启用。

## 最短安装命令

```bash
cd /home/run
sha256sum -c sparkrobot-lingxi-consecutive-nav-fix-1.0.7-20261024-r17.tar.gz.sha256
tar -xzf sparkrobot-lingxi-consecutive-nav-fix-1.0.7-20261024-r17.tar.gz
cd sparkrobot-lingxi-consecutive-nav-fix-1.0.7-20261024-r17
sudo bash ./install.sh
sudo sparkrobot-lingxi-consecutive-nav-fix doctor
```

`doctor` 不会让机器人移动。现场双点连续导航测试必须由现场人员确认环境安全并可随时急停后再执行。

完整说明见 `docs/连续导航卡住修复包部署说明.md`。
