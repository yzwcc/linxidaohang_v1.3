# V1.3.9 窄通道平衡避障策略增量包

## 目的

现场 A2 PNC 配置中，动态/局部 costmap 的 `inflation_radius` 为 `0.65 m`，静态地图层为 `1.0 m`。在机器人 footprint、行人碰撞距离、轨迹 clearance 和终点停车距离叠加后，窄通道或被人占位的点位可能被判定为不可通过。

本增量包提供一个可回滚的 `balanced-narrow` 策略：

- 动态、静态和局部 costmap 膨胀半径调整为 `0.50 m`。
- 全局静态地图 costmap 调整为 `0.65 m`。
- 行人最小距离调整为 `0.60 m`。
- 同步收缩 Ego 轨迹 clearance、局部目标拖拽距离、终点停车距离和 MPPI 近障碍减速距离。
- 保留机器人 footprint、障碍物层、碰撞检查和 failsafe，不使用 `NoAvoid` 控制器。
- 不修改 AIMDK 二进制、ROS 消息、地图、讲解任务、知识库、灵心或语音配置。

这不是绕过底层安全机制的“强行穿过”模式。真正的底层安全距离仍由 PNC 和机器人 footprint 负责；策略只减少过度保守的 costmap/轨迹参数。

## 安装

将以下三个文件放在机器人 `/home/run`：

```text
sparkrobot-lingxi-v1.3.9-narrow-corridor-policy-1.0.0-20260929.tar.gz
sparkrobot-lingxi-v1.3.9-narrow-corridor-policy-1.0.0-20260929.tar.gz.sha256
```

执行：

```bash
cd /home/run
sha256sum -c sparkrobot-lingxi-v1.3.9-narrow-corridor-policy-1.0.0-20260929.tar.gz.sha256
tar -xzf sparkrobot-lingxi-v1.3.9-narrow-corridor-policy-1.0.0-20260929.tar.gz
cd sparkrobot-lingxi-v1.3.9-narrow-corridor-policy-1.0.0-20260929
chmod +x install.sh control.sh
sudo bash ./install.sh
```

安装器会先确认当前 PNC 主配置指向 A2 `app2.yaml`，备份两个 YAML 文件，再修改参数。安装后只重启 `aima-drp-app` 的 watchdog 链路，让 PNC 重新加载配置，不会重启网页、Java Agent、语音或地图服务。

## 验收

```bash
sudo sparkrobot-lingxi-v1.3.9-narrow-corridor status
sudo sparkrobot-lingxi-v1.3.9-narrow-corridor doctor
```

也可以检查生效值：

```bash
grep -nE 'inflation_radius|minimum_pedestrian_distance|obs_clearance|dyna_obs_clearance|obstacle_boundary_clearance|stop_length|near_obstacle_slowdown_distance' \
  /agibot/software/nav/config/vectorflux_common/a2/app2.yaml \
  /agibot/software/nav/config/vectorflux_common/a2/mppi_controller.yaml
```

首次验证应在无人的空旷区域低速测试，再进入窄通道和人流区域。出现明显擦碰风险时立即按机器人急停，并执行回滚。

## 回滚

安装输出会给出实际备份目录，例如：

```text
/var/lib/sparkrobot/lingxi-v1.3.9-narrow-corridor/backups/20260929T000000Z.xxxxxx
```

执行：

```bash
sudo sparkrobot-lingxi-v1.3.9-narrow-corridor rollback \
  /var/lib/sparkrobot/lingxi-v1.3.9-narrow-corridor/backups/时间戳目录
sudo sparkrobot-lingxi-v1.3.9-narrow-corridor doctor
```

回滚只恢复本增量包备份的两个 PNC YAML 文件，不会回滚地图、任务数据或灵犀导航版本。

## 配置边界

本包针对当前机器人发现的路径：

```text
/agibot/software/nav/config/vectorflux/vectorflux_app_cfg.yaml
/agibot/software/nav/config/vectorflux_common/a2/app2.yaml
/agibot/software/nav/config/vectorflux_common/a2/mppi_controller.yaml
```

如果新机器使用的不是 A2 PNC 配置，安装器会拒绝修改；不要手工把 A2 参数写到其他项目配置中。
