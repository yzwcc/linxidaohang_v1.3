# 灵犀 X2 V1.3.1 R18 建图网页部署手册

适用包：

```text
sparkrobot-lingxi-r18-mapping-1.1.0-20261026-r18-mapfit17.tar.gz
SHA-256: 41719b47814b91d36f61bd42d1aabd6b91967d8373f43b2f619a8bf8a81872be
```

目标环境：

- 智元灵犀 X2，Ubuntu 22.04 ARM64
- ROS 2 Humble
- V1.3.1 R18
- AIMDK v1.1.0 overlay
- 手机或电脑与机器人处于同一局域网

本包只部署网页、导航桥接和建图适配，不安装、不替换、不覆盖 AIMDK。`ROBOT_AIMDK_SETUP` 必须明确指向机器人上实际使用的新版 AIMDK `install/setup.bash`。

## 一、部署前准备

### 1. 准备文件

在 Windows 电脑准备以下两个文件：

```text
sparkrobot-lingxi-r18-mapping-1.1.0-20261026-r18-mapfit17.tar.gz
sparkrobot-lingxi-r18-mapping-1.1.0-20261026-r18-mapfit17.tar.gz.sha256
```

不要上传旧的 `mapfit16`、`mapfit15` 或旧 `sparkrobot-lingxi-internal` 包。

### 2. 确认机器人 IP

机器人本机执行：

```bash
hostname -I
```

记下局域网 IP，例如 `192.168.0.203`。手机、电脑和机器人必须连接同一局域网。

### 3. 确认新版 AIMDK overlay

SDK zip/artifacts 不是可直接传给安装器的 setup 文件。安装器需要机器人上已经安装完成的：

```text
/某个目录/aimdk-v1.1.0/install/setup.bash
```

在机器人上查找候选文件：

```bash
find /home/agi /opt -path '*/install/setup.bash' -type f 2>/dev/null
```

选择你们当前要使用的新版 AIMDK 路径，不要凭旧默认路径猜测。然后验证接口：

```bash
export AIMDK_SETUP=/实际的新AIMDK路径/install/setup.bash
source /opt/ros/humble/setup.bash
source "$AIMDK_SETUP"

python3 - <<'PY'
from aimdk_msgs.msg import PncTaskRequest
from aimdk_msgs.srv import GetStoredMapByName
print("AIMDK navigation/mapping interfaces: OK")
PY
```

必须看到：

```text
AIMDK navigation/mapping interfaces: OK
```

如果这里失败，不要改回旧 AIMDK，也不要继续安装；先让同事确认新版 overlay 的安装路径和接口完整性。

### 4. 处理旧服务占用

新版网页使用 `3000` 端口。如果上一版内部服务仍在运行，先检查：

```bash
sudo ss -ltnp | grep -E ':(3000|18082|8002)\b' || true
```

如果确认是旧版 `sparkrobot-lingxi-internal` 占用 3000，使用旧版自己的停止命令：

```bash
sudo sparkrobot-lingxi-internal stop
```

不要停止厂商 AIMDK、雷达、底盘或核心 SLAM 服务。新版服务安装完成后只保留一个 3000 端口网页实例。

## 二、上传部署包

在 Windows PowerShell 中执行，替换 `$RobotIp`：

```powershell
$RobotIp = "机器人IP"
$Package = "sparkrobot-lingxi-r18-mapping-1.1.0-20261026-r18-mapfit17.tar.gz"
$ShaFile = "$Package.sha256"

scp ".\$Package" "agi@${RobotIp}:/home/agi/"
scp ".\$ShaFile" "agi@${RobotIp}:/home/agi/"
ssh "agi@${RobotIp}"
```

## 三、在机器人上安装

以下命令在机器人 `agi` 账号执行：

```bash
cd /home/agi

sha256sum -c sparkrobot-lingxi-r18-mapping-1.1.0-20261026-r18-mapfit17.tar.gz.sha256

rm -rf /home/agi/r18-mapfit17-install
mkdir -p /home/agi/r18-mapfit17-install
tar -xzf sparkrobot-lingxi-r18-mapping-1.1.0-20261026-r18-mapfit17.tar.gz \
  -C /home/agi/r18-mapfit17-install

cd /home/agi/r18-mapfit17-install/sparkrobot-lingxi-r18-mapping
sha256sum -c SHA256SUMS
```

如果两个校验都显示 `OK`，执行安装。将路径替换成第二步确认的新版 AIMDK 路径：

```bash
sudo env \
  ROBOT_AIMDK_SETUP=/实际的新AIMDK路径/install/setup.bash \
  ./install.sh
```

安装器会执行以下保护：

- 校验 AIMDK setup 可读
- 校验 `PncTaskRequest` 和 `GetStoredMapByName`
- 校验 payload manifest
- 检查是否存在未完成的建图保存会话
- 备份原网页、导航 Python、Node runtime 和脚本
- 保留已有地图、点位、讲解任务、配置和凭据
- 重启 `lingxi-nav-console.service`
- 检查网页健康接口版本

安装目标：

```text
/opt/lingxi-nav-console
/home/agi/robot_local_navigation_v3
```

备份目录由安装器打印，通常为：

```text
/opt/lingxi-nav-console/backups/r18-aimdk-v1.1.0-YYYYMMDD-HHMMSS
```

## 四、安装后验收

### 1. 检查 systemd 服务

```bash
sudo systemctl status lingxi-nav-console.service --no-pager -l
```

状态应为 `active (running)`。

### 2. 检查网页版本

```bash
curl -fsS http://127.0.0.1:3000/health
```

返回内容中的版本必须是：

```text
1.1.0-20261026-r18-mapfit17
```

只看到端口能连接但版本仍是旧版，不算部署成功。

### 3. 检查导航桥接

```bash
sudo -u agi env \
  ROBOT_AIMDK_SETUP=/实际的新AIMDK路径/install/setup.bash \
  /home/agi/robot_local_navigation_v3/scripts/robot_local_navigation_v3.sh status

curl -fsS http://127.0.0.1:18082/nav/status
curl -fsS http://127.0.0.1:18082/mapping/status
```

如果服务尚未启动，可以用脚本启动基础桥接：

```bash
sudo -u agi env \
  ROBOT_AIMDK_SETUP=/实际的新AIMDK路径/install/setup.bash \
  /home/agi/robot_local_navigation_v3/scripts/robot_local_navigation_v3.sh up
```

通常网页上的“一键启动全部服务”会完成同样的启动流程。不要用 root 直接启动 ROS bridge，避免 root 与 `agi` 的 ROS DDS 环境不一致。

### 4. 检查传感器链路

```bash
source /opt/ros/humble/setup.bash
source /实际的新AIMDK路径/install/setup.bash
export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml
export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so

ros2 topic info -v \
  /aima/hal/sensor/lidar_chest_front/lidar_pointcloud

ros2 topic info -v \
  /aima/hal/sensor/lidar_chest_front/imu

timeout 8 ros2 topic echo --once --qos-reliability best_effort \
  /aima/hal/sensor/lidar_chest_front/lidar_pointcloud \
  sensor_msgs/msg/PointCloud2

timeout 8 ros2 topic echo --once --qos-reliability best_effort \
  /aima/hal/sensor/lidar_chest_front/imu \
  sensor_msgs/msg/Imu
```

开始建图前，网页必须能看到：

- 雷达点云发布者存在
- IMU 发布者存在
- 近 3 秒收到雷达和 IMU 数据
- AIMDK 建图命令订阅端存在
- `GetStoredMapByName` 服务就绪
- 导航任务为空闲

`/slam/lidar_odom` 可以在点击开始建图后才出现，因此它不是开始前的硬性阻断条件。

## 五、手机/电脑打开网页

浏览器打开：

```text
http://机器人IP:3000/console/
```

例如：

```text
http://192.168.0.203:3000/console/
```

网页是机器人本机服务，手机和电脑不需要安装 Node、ROS 或 AIMDK。

如果手机打不开但机器人本机 `curl` 正常，检查：

```bash
sudo ss -ltnp | grep ':3000'
ip -br address
sudo ufw status
```

不要把这个明文管理网页暴露到公网；它只应在机器人局域网使用。

## 六、开始建图

### 1. 开始前

- 确认外置遥控器可用
- 机器人处于安全、空旷区域
- 结束网页中的导航任务
- 确认网页建图状态中的 `导航任务已停止`
- 机器人先保持静止，等待传感器状态变为就绪

建图网页不会发布速度、PNC 或底盘移动命令。机器人移动完全由外置遥控器负责。

### 2. 网页操作

1. 点击“一键启动全部服务”。
2. 打开“建图采集”。
3. 点击“刷新传感器状态”。
4. 输入地图名称。
5. 点击“开始采集”。
6. 等待页面显示建图中。
7. 使用外置遥控器低速移动机器人，覆盖所有需要导航的区域。
8. 观察实时点云、机器人位置和轨迹。
9. 完成后点击“停止采集并保存地图”。
10. 等待地图保存确认。

地图名称只允许 1-48 位：

```text
A-Z a-z 0-9 _ -
```

例如：

```text
showroom_r18
floor1_20261026
```

不要使用空格、中文、斜杠或冒号。

### 3. 实时预览说明

开始后页面可能先显示“等待 SLAM 里程计与雷达坐标变换”。这是等待 `/slam/lidar_odom` 和 TF，不代表建图命令没有发送。

可在机器人上观察：

```bash
curl -fsS http://127.0.0.1:18082/mapping/preview
```

预览出现 `points`、`robot` 和 `robot_path` 后，网页实时地图已经开始累积。

### 4. 保存说明

停止后流程是：

1. 网页只发送一次 `stop_mapping:<地图名>`。
2. AIMDK 保存地图。
3. 网页通过 `GetStoredMapByName` 查询保存结果。
4. occupancy grid 导入数字地图目录。
5. 地图出现在最近地图列表。

如果保存查询暂时超时，按钮会变成“重试地图保存确认”。此按钮只重试查询，不会再次发送停止命令。

## 七、保存后确认地图

网页地图列表出现新地图后：

1. 打开新地图。
2. 检查墙体方向是否正确。
3. 检查比例尺是否合理。
4. 检查已知起点和障碍物位置。
5. 在地图上配置导航点。
6. 保存点位后再做一次导航测试。

机器人端可查看地图目录：

```bash
find /agibot/data/var/MapManagerModule -maxdepth 2 -type f \
  \( -name occupancy_map.png -o -name grid_map_info.txt -o -name lingxi_map_import.json \) \
  -print
```

导入地图目录通常包含：

```text
<map_id>/occupancy_map.png
<map_id>/grid_map_info.txt
<map_id>/lingxi_map_import.json
```

带 yaw 的纯平面地图会按原分辨率重采样为轴对齐地图，并在 `lingxi_map_import.json` 中记录源 yaw。roll、pitch、无效四元数、非水平原点和超大地图会被拒绝，不会生成错误点位坐标。

## 八、常见问题

### 1. 安装器提示必须设置 ROBOT_AIMDK_SETUP

原因：没有传入新版 AIMDK `install/setup.bash`，或路径不可读。

处理：

```bash
ls -l /实际的新AIMDK路径/install/setup.bash
sudo env ROBOT_AIMDK_SETUP=/实际的新AIMDK路径/install/setup.bash ./install.sh
```

### 2. 安装器提示缺少接口

手动验证：

```bash
source /opt/ros/humble/setup.bash
source /实际的新AIMDK路径/install/setup.bash
python3 -c 'from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import GetStoredMapByName; print("OK")'
```

不要用旧 AIMDK 的 Python 路径拼接 `PYTHONPATH`，也不要用旧版本服务文件覆盖新版包。

### 3. 网页显示机器人服务超时

检查：

```bash
sudo systemctl status lingxi-nav-console.service --no-pager -l
curl -fsS http://127.0.0.1:3000/health
curl -fsS http://127.0.0.1:18082/nav/status
curl -fsS http://127.0.0.1:18082/mapping/status
```

再检查日志：

```bash
tail -n 200 /home/agi/robot_local_navigation_v3/logs/lingxi_nav_bridge.log
tail -n 200 /home/agi/robot_local_navigation_v3/logs/map_label_server_v3.log
sudo journalctl -u lingxi-nav-console.service -n 200 --no-pager
```

如果 3000 被旧版占用，先停止旧版服务，再重启新版：

```bash
sudo sparkrobot-lingxi-internal stop
sudo systemctl restart lingxi-nav-console.service
```

### 4. 建图按钮不能开始

查看：

```bash
curl -fsS http://127.0.0.1:18082/mapping/status | python3 -m json.tool
```

重点看：

- `lidar_publishers`
- `imu_publishers`
- `lidar_data_available`
- `imu_data_available`
- `mapping_command_subscribers`
- `aimdk_v1_1_0_ready`
- `navigation_idle`
- `diagnosis`

网页不会在传感器数据过期、导航仍运行、AIMDK 建图订阅端不存在时发送开始命令。

### 5. 开始后没有实时地图

先等待 `/slam/lidar_odom` 和 TF 出现：

```bash
timeout 8 ros2 topic echo --once --qos-reliability best_effort \
  /slam/lidar_odom nav_msgs/msg/Odometry

curl -fsS http://127.0.0.1:18082/mapping/preview | python3 -m json.tool
```

如果雷达/IMU 有数据但长期没有里程计和 TF，需要检查厂商 SLAM 节点，不要在网页端重复点击开始。

### 6. 停止后保存超时

先重试网页中的“重试地图保存确认”，不要重复点击“停止采集”。

然后查看：

```bash
cat /home/agi/robot_local_navigation_v3/mapping-session.json
curl -fsS http://127.0.0.1:18082/mapping/status | python3 -m json.tool
tail -n 200 /home/agi/robot_local_navigation_v3/logs/lingxi_nav_bridge.log
```

只要状态仍为 `saving` 或 `save_timeout`，不要重新安装包；安装器会保护未完成的保存会话。

### 7. 手动导航显示服务超时

建图期间不要同时启动导航。先确认：

```bash
curl -fsS http://127.0.0.1:18082/nav/status | python3 -m json.tool
```

完成建图并确认地图保存后，再停止建图会话、选择地图并启动导航。导航和建图不能共用一个活动会话。

## 九、停止服务

正常停止网页桥接：

```bash
sudo -u agi env \
  ROBOT_AIMDK_SETUP=/实际的新AIMDK路径/install/setup.bash \
  /home/agi/robot_local_navigation_v3/scripts/robot_local_navigation_v3.sh stop
```

停止本机控制台服务：

```bash
sudo systemctl stop lingxi-nav-console.service
```

停止网页不会停止厂商雷达、底盘或核心 AIMDK 服务。

## 十、重新启动

```bash
sudo systemctl start lingxi-nav-console.service
sudo systemctl status lingxi-nav-console.service --no-pager -l
curl -fsS http://127.0.0.1:3000/health
```

然后浏览器重新打开：

```text
http://机器人IP:3000/console/
```

## 十一、备份与回滚边界

每次安装前都会创建备份目录，例如：

```text
/opt/lingxi-nav-console/backups/r18-aimdk-v1.1.0-20261026-153000
```

备份包含网页资源、Node runtime、导航 Python 包、脚本和旧 `.env`。地图、点位、讲解任务和统一导航配置不会被安装器主动删除。

不要直接执行：

```bash
rm -rf /opt/lingxi-nav-console
rm -rf /home/agi/robot_local_navigation_v3
```

如需回滚，先停止 `lingxi-nav-console.service`，保留当前备份和日志，再按照现场运维流程恢复对应备份版本。不要用旧 AIMDK 文件覆盖新版 AIMDK。

## 十二、现场验收清单

- [ ] 包 SHA-256 校验通过
- [ ] `SHA256SUMS` 校验通过
- [ ] 新 AIMDK setup 接口导入通过
- [ ] 网页 `/health` 返回 `1.1.0-20261026-r18-mapfit17`
- [ ] `lingxi-nav-console.service` 为 active
- [ ] `18082/nav/status` 可访问
- [ ] `18082/mapping/status` 可访问
- [ ] 雷达点云发布者存在并有新数据
- [ ] IMU 发布者存在并有新数据
- [ ] AIMDK 建图命令订阅端存在
- [ ] 地图存储服务就绪
- [ ] 网页能从手机打开
- [ ] 开始建图只发送一次 `start_mapping`
- [ ] 外置遥控器移动机器人时网页实时显示点云/轨迹
- [ ] 停止建图只发送一次 `stop_mapping:<地图名>`
- [ ] 地图保存确认成功
- [ ] 新地图出现在地图列表
- [ ] 地图方向、比例和已知位置正确
- [ ] 至少一个导航点保存成功
- [ ] 用新地图完成一次低速导航验证

离线测试不能替代 X2 现场验收。只有完成以上现场清单后，才能确认真实雷达、SLAM、地图保存和导航链路均正常。
