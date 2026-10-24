# 灵犀 X2 R18 建图实时地图增量包

本增量包基于 V1.3.1 R18 与 AIMDK v1.1.0，只更新机器人本机导航控制台及 AIMDK 建图适配器。它不替换 AIMDK、不发送机器人移动命令，也不包含新的 Java Agent 或导航控制行为。

## 文件

`internal_deployment/dist/sparkrobot-lingxi-nav-internal-1.1.0-20261025-r18-live-mapfit3.tar.gz`

SHA-256：

`ea07b5763ea2d273a9e6c10d3a2fd5513398e1ef01c3d1c0c597382c46ea23d9`

请先将 tar 包复制到机器人，然后校验并解压：

```bash
sha256sum sparkrobot-lingxi-nav-internal-1.1.0-20261025-r18-live-mapfit3.tar.gz
mkdir -p ~/r18-live-mapfit3
tar -xzf sparkrobot-lingxi-nav-internal-1.1.0-20261025-r18-live-mapfit3.tar.gz -C ~/r18-live-mapfit3
cd ~/r18-live-mapfit3/sparkrobot-lingxi-nav-internal-1.1.0-20261025-r18-live-mapfit3
```

首次安装若机器人尚未配置灵心/SOC2 凭据，使用 `sudo ./install.sh --secrets ./secrets.env --setup-soc2`；不要把真实凭据放进压缩包。已有 R18 安装只升级程序时，安装器会保留机器人现有配置、地图和任务：

```bash
sudo ./install.sh
sudo sparkrobot-lingxi-internal start
sudo sparkrobot-lingxi-internal doctor
readlink -f /opt/sparkrobot/lingxi-nav/current
```

确认当前版本目录为 `1.1.0-20261025-r18-live-mapfit3`，并检查控制台健康状态：

```bash
curl -fsS http://127.0.0.1:3000/health
```

## 建图操作

1. 手机或电脑连接机器人所在局域网，访问 `http://机器人局域网IP:3000/console/`。
2. 打开建图工作区并检查 AIMDK、雷达、IMU、SLAM 里程计和建图接口状态均就绪。
3. 输入地图名称并开始建图。
4. 使用外置遥控器手动移动机器人完成环境覆盖；网页不会控制底盘移动。
5. 建图工作区中央实时显示胸前激光雷达点云，经 TF 转换到 `/slam/lidar_odom` 的地图坐标系；浏览器累积命中栅格并绘制机器人轨迹。该图是由实时传感器/SLAM位姿构建的网页预览，不是 AIMDK 保存地图文件；正式导航地图仍由停止建图后调用 `GetStoredMapByName` 导入。
6. 在网页停止并保存。停止命令通过 AIMDK `/integrated_command` 发布 `stop_mapping:<map_name>`，随后调用 `GetStoredMapByName` 确认地图并导入导航地图目录。
7. 确认网页返回保存成功后，保存地图会同步出现在现有地图/点位工作区；在那里刷新地图列表、检查地图和点位，再按现场流程验收定位与导航。建图与导航共用机器人本机控制台、SSH 目标、地图目录及地图管理界面，不另设第二个地图仓库。

当前接口不支持旋转的地图原点，若服务返回此类地图会拒绝导入以避免点位坐标错误。保存失败时先查看 `sudo sparkrobot-lingxi-internal logs console`，并在机器人上核对 AIMDK SLAM 服务状态；不要重复覆盖同一数字地图 ID 的不同内容。

实时二维扫描预览通过只读 SSH 订阅雷达 `PointCloud2` 与 `/slam/lidar_odom`，读取 TF 将点云变换到 odometry/map frame，服务端输出轻量坐标点，浏览器累积 5 cm 命中栅格、绘制 SLAM 轨迹与机器人朝向。仅活动建图会话采集；服务端合并并发帧读取并短暂缓存，限制每帧点数，前端也限制累计栅格与轨迹长度。雷达点云与里程计时间戳偏差超过 0.5 秒时拒绝绘制，避免把错位数据呈现为地图。网页不会发布 `/integrated_command` 以外的控制命令，也不会控制底盘。SDK v1.1.0 文档仅保证感知 `/perception/grid_map` 为高程图而未承诺实时二维占用图；本预览因此由点云与 SLAM 位姿生成，不冒称 AIMDK 的官方地图文件。实机需确认 TF frame 连通、胸前雷达 PointCloud2 字段和 SLAM odometry 时间戳一致。

## 页面设计参考

- GitHub `Kimkyuwon/ROS-SLAM-WebUI`：主画布呈现 SLAM 结果，运行状态与保存控件分区；本项目按此思路将地图预览放在建图状态与操作的主工作区，但控制逻辑仍使用 X2 AIMDK 原生接口。
- GitHub ROS 2 `sensor_msgs/PointCloud2` 与 TF2：采用标准点云字段读取及 source→map 坐标变换模式；GitHub ROS-SLAM-WebUI 用主视图突出 SLAM 实时结果。
- AIMDK SDK v1.1.0 `SLAM` 与 `数据可视化` 文档：确认 `/integrated_command`、保存后 `GetStoredMapByName`，以及 `/slam/lidar_odom`、雷达点云与 `/perception/grid_map` 高程接口。以上资料用于页面信息架构和消息处理，不代替 X2 实机验收。

## 回滚

安装器会保留已安装的历史 release。确认新版本异常时，可回滚到安装前的 R18 版本并重启：

```bash
sudo sparkrobot-lingxi-internal rollback
sudo sparkrobot-lingxi-internal start
readlink -f /opt/sparkrobot/lingxi-nav/current
```

## 验证边界

保存操作外层 SSH 等待为 180 秒；AIMDK 查询轮询最多约 137 秒，预留地图转换和 ROS 清理时间。离线回归覆盖原有地图保存转换以及本轮点云坐标变换逻辑。机器人实机 SSH、TF frame 连通、实时传感器与 odometry 时间同步、完整建图、地图重载与现场导航仍须在目标 X2 上验收。
