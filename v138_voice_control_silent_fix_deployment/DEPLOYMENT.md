# V1.3.8 语音导航与讲解静默修复部署说明

增量版本：`1.0.10-20261021`  
适用基线：V1.3.8 完整包 r19，运行 release `1.0.19-20261019-r18`。

本包把已验证的 1.0.9 累计 Java Agent 控制接管逻辑和本次 bridge 静默修复合并交付。语音启动导航/讲解时，应用不播报成功或失败兜底；导航/讲解步骤失败和超时仅记录日志、继续现有状态处理。自动返航自身的提示、普通问答、到点讲解正文、暂停/继续/结束反馈和网页状态不在静音范围。

## 安装

把 `.tar.gz` 与同名 `.tar.gz.sha256` 上传到机器人 `/home/run`。先在网页结束导航、讲解和返航任务，确认机器人停稳；再执行：

```bash
cd /home/run &&
sha256sum -c sparkrobot-lingxi-v1.3.8-voice-control-silent-fix-1.0.10-20261021.tar.gz.sha256 &&
tar -xzf sparkrobot-lingxi-v1.3.8-voice-control-silent-fix-1.0.10-20261021.tar.gz &&
cd sparkrobot-lingxi-v1.3.8-voice-control-silent-fix-1.0.10-20261021 &&
sudo bash ./install.sh --check &&
sudo bash ./install.sh
```

如预检失败，停止操作并保留完整错误输出，不要手工复制 JAR 或绕过校验。安装器核对 r18 release、运行清单、SDK、服务启动路径和 Python 文件指纹；事务会备份 JAR、bridge、清单并恢复安装前的服务状态。

安装后核对：

```bash
sudo sparkrobot-lingxi-internal status
sudo sparkrobot-lingxi-internal doctor
sudo tail -n 100 -F /var/log/sparkrobot/lingxi-nav/java-agent.log /var/log/sparkrobot/lingxi-nav/lingxi_nav_bridge.log
```

分别复现语音导航命令和开始讲解命令。控制应照常执行；不得出现导航/讲解“成功”“失败”“抱歉”类底层结果语音。讲解到点正文和普通问答应继续播报。自动返航故障提示仍按原策略保留。

## 回滚

安装成功时终端会打印本次事务备份绝对路径。确认任务结束且机器人停稳后，替换下面的备份占位路径：

```bash
cd /home/run/sparkrobot-lingxi-v1.3.8-voice-control-silent-fix-1.0.10-20261021
sudo bash ./install.sh --rollback /var/lib/sparkrobot/lingxi-voice-control-silent-fix/backups/实际备份目录
```

回滚只接受本包事务创建且安装后文件仍未被其他更新改动的备份；若发生后续改动，安装器会拒绝覆盖。

本包保留 r18 自带的新版到点判定文件，不触碰地图、讲解任务、密钥、知识库和 `lingxi.callbacks`，也不会向机器人下发运动指令。未连接机器人；现场扬声器验收仍需部署后执行。
