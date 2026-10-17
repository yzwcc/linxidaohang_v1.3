# V1.3.9 导航受阻礼貌提醒增量包

## 功能

机器人在普通导航或讲解任务的导航步骤中，连续没有有效平移位移时新增播报“请大家让让我”：首次停滞满 5 秒播报，之后每隔 5 秒最多播报一次；恢复移动、转向或进入到达区域后停止提醒。

原有“当前路线暂时无法到达”和“导航失败”提示保留，不做替换。该功能基于里程计停滞状态，不能证明障碍物一定是人，因此不会关闭 PNC 避障、footprint、碰撞检查或 failsafe。

## 安装

```bash
cd /home/run
sha256sum -c sparkrobot-lingxi-v1.3.9-blocked-reminder-1.0.0-20260929.tar.gz.sha256
tar -xzf sparkrobot-lingxi-v1.3.9-blocked-reminder-1.0.0-20260929.tar.gz
cd sparkrobot-lingxi-v1.3.9-blocked-reminder-1.0.0-20260929
chmod +x install.sh control.sh
sudo bash ./install.sh
```

安装器会备份四个导航 Python 文件，并调用机器人现有 `navigation_control.sh` 使 bridge 重新加载。该机器人控制脚本可能按原有逻辑同时重启地图和兼容语音网关；不会重启 Java Agent，不会修改网页、AIMDK、PNC 参数、地图数据或语音配置。

## 验收与回滚

```bash
sudo /usr/local/sbin/sparkrobot-lingxi-blocked-reminder status
sudo /usr/local/sbin/sparkrobot-lingxi-blocked-reminder doctor
sudo cat /var/lib/sparkrobot/lingxi-blocked-reminder/backups/last-backup
```

```bash
sudo /usr/local/sbin/sparkrobot-lingxi-blocked-reminder rollback \
  /var/lib/sparkrobot/lingxi-blocked-reminder/backups/时间戳目录
```

现场测试必须有人持急停，在安全距离用软障碍保持阻挡；约 5 秒应播报一次，继续阻挡时约每 5 秒播报一次，移开后停止。
