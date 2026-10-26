# 灵犀 X2 R18 建图网页增量包

完整部署手册见同目录 `DEPLOYMENT-GUIDE.md`。该手册可直接交给现场同事执行，包含上传、AIMDK 校验、安装、启动、建图、保存、故障排查和现场验收步骤。

目标版本：V1.3.1 R18，使用 AIMDK v1.1.0。网页运行在机器人本机 `:3000`，同一局域网的手机/电脑访问；网页只采集和保存地图，机器人移动由外置遥控器控制。`20261026-r18-mapfit17` 还包含机器人端 ROS 实时点云累积、手机/电脑扫描预览、R18 桥接 `18082` 端口兼容、较长的手动导航接口等待时间，以及纯平面 yaw 原点地图的轴对齐重采样。

网页读取机器人导航桥接对激光雷达、IMU、里程计、SLAM 和 AIMDK 建图服务的状态。开始/停止建图只向 AIMDK `/integrated_command` 发布 `start_mapping` / `stop_mapping:<地图名>`，保存后通过 `GetStoredMapByName` 读取 occupancy grid 并导入现有数字地图目录。纯平面 yaw 原点会按原分辨率重采样为轴对齐栅格，保存源原点与 yaw 供追溯；障碍单元优先保留。roll/pitch、无效四元数、非水平原点和超过上限的结果会明确拒绝。扫描预览由 ROS 节点订阅胸前雷达与 `/slam/lidar_odom`，用 TF 转到 SLAM 坐标系后向网页提供累计点云和轨迹。网页不会发布 PNC、速度或底盘移动命令。

## 前置条件

- 智元 X2，Ubuntu 22.04 ARM64、ROS 2 Humble。
- 厂商已按支持流程安装 AIMDK v1.1.0 overlay，且 `install/setup.bash` 可由 `agi` 账号读取。
- 该 overlay 中能导入 `aimdk_msgs.msg.PncTaskRequest` 与 `aimdk_msgs.srv.GetStoredMapByName`，并提供 `/integrated_command` 建图订阅端。
- 机器人已有 V1.3.1 本机控制台，安装根目录默认为 `/opt/lingxi-nav-console`；导航数据目录默认为 `/home/agi/robot_local_navigation_v3`。

用户提供的 SDK artifacts 是开发接口/库归档，不是完整机器人 overlay；本增量包不会安装或覆盖 AIMDK。

## 安装

在 Windows PowerShell 上传归档及 SHA 文件后 SSH 到机器人：

```powershell
$RobotIp = "机器人当前IP"
$Package = "sparkrobot-lingxi-r18-mapping-1.1.0-20261026-r18-mapfit17.tar.gz"
scp ".\$Package" "agi@${RobotIp}:/home/agi/"
scp ".\$Package.sha256" "agi@${RobotIp}:/home/agi/"
ssh "agi@${RobotIp}"
```

在机器人 `agi` 账号执行：

```bash
cd /home/agi
sha256sum -c sparkrobot-lingxi-r18-mapping-1.1.0-20261026-r18-mapfit17.tar.gz.sha256
tar -xzf sparkrobot-lingxi-r18-mapping-1.1.0-20261026-r18-mapfit17.tar.gz
cd sparkrobot-lingxi-r18-mapping
sha256sum -c SHA256SUMS
sudo env ROBOT_AIMDK_SETUP=/机器人实际路径/aimdk-v1.1.0/install/setup.bash ./install.sh
```

本次增量包不会安装或覆盖 AIMDK。安装器会先验证 setup 文件可读、`PncTaskRequest` 和 `GetStoredMapByName` 接口可导入，并校验 payload manifest；验证未通过时会在创建安装目标目录前退出。网页服务安装在 `/opt/lingxi-nav-console`，导航 bridge 使用 `18082` 端口；保留已有 `.env` 的凭据、地图、点位、讲解任务和统一导航配置，并在更新前备份。

安装器会先校验 AIMDK 接口，再备份网页、Node 运行时、导航 Python 包和脚本；它保留已有 `.env` 的凭据、地图、点位、讲解任务和统一导航配置，仅更新服务版本和 AIMDK setup 路径。备份位于 `/opt/lingxi-nav-console/backups/`。

## 启动建图

安装完成后，手机/电脑连接与机器人相同的局域网，打开：

```text
http://机器人IP:3000/console/
```

在控制台点击“一键启动全部服务”（应以 `agi` 用户启动导航 bridge，避免 root 与 `agi` 的 ROS DDS domain 不一致），确认机器人端 `lingxi_nav_bridge` 在 `127.0.0.1:18082` 在线后进入“建图采集”：

1. 刷新传感器状态，确认雷达点云与 IMU 发布者存在且近 3 秒确实收到数据、建图命令订阅端和地图存储服务就绪。SLAM 里程计可以在开始后出现。
2. 输入新地图名称并点击“开始采集”。
3. 使用外置遥控器低速移动机器人完成场地覆盖；不要在网页上寻找移动控制，网页没有运动命令。
4. 点击“停止采集并保存地图”，等待 AIMDK 地图服务确认。若保存查询超时，按钮变为“重试地图保存确认”，不会重复发送停止命令。
5. 确认地图出现在最近地图列表后，打开地图检查方向、比例和已知位置，再配置导航点。

## 检查与恢复

```bash
sudo systemctl status lingxi-nav-console.service
curl -fsS http://127.0.0.1:3000/health
sudo -u agi env ROBOT_AIMDK_SETUP=/机器人实际路径/aimdk-v1.1.0/install/setup.bash /home/agi/robot_local_navigation_v3/scripts/robot_local_navigation_v3.sh status
curl -fsS http://127.0.0.1:18082/mapping/status
```

网页健康接口的 `version` 应为 `1.1.0-20261026-r18-mapfit17`；安装器会在重启后核对健康端点返回的版本，不接受仅端口可访问但实际仍运行旧版本的情况。其他产品目录里的旧预览实例或 v140 服务不能作为 R18 部署验收。

本次离线测试不能代替 X2 实机验收。首次部署必须确认传感器帧、地图保存延迟、导入后的地图方向及现场已知位置，尤其检查旋转原点重采样后的墙体与占用区域。导航桥接不使用已经废弃的 AimDK setup 默认路径；本次使用的 `/integrated_command`、地图查询和 PNC 接口均来自显式配置的 AIMDK v1.1.0 overlay。

## 本次交付验证范围

- 服务端 Node 测试：55 项通过；TypeScript 构建通过。
- 导航与建图 Python 全量测试：135 项通过，4 个子测试通过；包括 90° 与任意 yaw 原点、已知占用格世界坐标精度、灰度占用保留、地图目录发现闭环、重采样尺寸上限和 roll/pitch 拒绝；Python 编译和 Shell 语法检查通过。
- 安装器负向测试：缺少 AIMDK setup 时明确拒绝，隔离目标目录未创建。
- 打包审计：归档与 payload manifest / SHA256SUMS 一致；ARM64 Node ELF64 架构及可执行权限通过；未包含 Windows 原生模块或 `.env`/私钥。
- 浏览器检查：1440×1000 桌面、390×844 手机无横向溢出；建图视图 axe 扫描 0 项违规、3 项对比度结果需人工复核。
- 尚未完成实机验收：当前未连接 X2，不能据此宣称机器人已能开始扫描或保存成功。需在机器人局域网现场确认雷达/IMU 实时数据、AIMDK 建图订阅与地图服务、实时点云及轨迹、停止保存、地图发现和已知位置/方向。
