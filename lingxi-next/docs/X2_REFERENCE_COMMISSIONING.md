# 灵犀 X2 Ultra V1.1.5 联调

目标机 SoC1 为 `release-lx2501_3_t2d5-soc1-v1.1.5`。AIMDK 安装清单为 `v1.1.0-rc10`。ROS 接入以目标机安装的接口定义、服务类型和传感器数据为准。

参考仓库固定在 `90bbc8096c23a5660fc6c5e26dd99ec1ed79da11`。联调服务在私有 Unix Socket 上提供 FAST-LIO 点迹、保存与放弃、栅格读取、GICP 重定位和 Nav2 路径预览。网页和横屏 APK 通过配对后的 HTTPS/WSS 使用这些接口。联调初始配置关闭运动，建图移动由现场人员使用外置遥控器。[参考仓库](https://github.com/Mikeaaaa14/Lingxi-navigation)

## 依赖和部署

ARM64 核心联调包提供产品 Web、API、参考服务适配、AgentSDK 进程和商品化之前的安装、回滚脚本。参考仓库、适配目标机的原生 ROS overlay、Python 3.10 环境、标定和机器人数据单独准备。

1. 在 `/opt` 下准备只读的参考 Git checkout，校验完整提交 ID，保留原仓库许可证和第三方声明。独立构建 `fast_lio`、`robot_nav_recorder`、`robot_nav_costmap`、`pointcloud_to_laserscan`，以及 `integrations/grid_export` 的 `grid_export_native`、`nav_ground_native`。以目标 ROS 2 Humble 和已安装的 AIMDK overlay 为依赖。Python 环境使用参考提交的 `requirements.txt`，并能加载这两个原生模块。FAST-LIVO2 和彩色相机处理不在此联调范围内。
2. 以参考 `config/app.yaml` 为基础生成 `/etc/lingxi-next/reference.yaml`。使用 `motion_enabled: false`、`nav2_enabled: true`、`navigation_mode: point`，并关闭 `visualization.enabled`、`color_mapping.enabled` 和 `mapping.loop_closure_enabled`。把 `data_dir` 指向 `/var/lib/lingxi-next/reference`，建图目录和导入/录制白名单设为该目录内的专用路径。建立 `lingxi-next` 所有、权限 `0700` 的数据目录。将 `mapping.fastlio_executable`、`mapping.fastlio_config`、`mapping.recorder_executable`、`mapping.calibration_files` 和 `localizer_config` 改为当前 ARM64 环境的绝对路径。按目标机校验 FAST-LIO 雷达—IMU 外参、入站话题 QoS、DDS 配置及初始站姿。
3. 在目标机按 `deploy/install.sh --commissioning --robot-ip IP` 安装产品。设置 `/etc/lingxi-next/robot.env` 中的 `LINGXI_AIMDK_SETUP`、`LINGXI_REFERENCE_SOURCE`、`LINGXI_REFERENCE_CONFIG`、`LINGXI_REFERENCE_CONFIG_SHA256`、`LINGXI_REFERENCE_OVERLAY`、`LINGXI_REFERENCE_PYTHON`、`LINGXI_REFERENCE_ROS2`，并设置 `LINGXI_REFERENCE_SOCKET=/run/lingxi-next-reference/control.sock`。保持 GLIM 和独立物理导航后端未选中。原生二进制和配置放在 systemd 服务账号可读且不依赖 `/home` 的路径。
4. 启动 `lingxi-next-reference-navigation.service`，检查日志后重启 `lingxi-next-api.service`。参考服务启动同提交的 Nav2 进程，使用当前产品的私有工作目录和内网 ROS 图。`deploy/diagnose.sh` 可检查服务状态。跨版本回滚前先结束建图并等待保存回执。若回到未包含参考服务的版本，先停止参考单元，再将 `LINGXI_REFERENCE_SOCKET` 留空。

参考仓库的版权人代码使用 PolyForm Noncommercial 1.0.0 及另行商业授权。用户已确认该仓库的商业复用和分发授权。实际分发引用仓库或其构建产物时，保留原 LICENSE、`Required Notice: Copyright (c) 2026 Mikeaaaa14 (https://github.com/Mikeaaaa14)`，并履行各组件的对应义务，包括 FAST-LIO 的 GPLv2、Patchwork++ 的 BSD 声明和 OctoMap 随附许可证。

## 点位与讲解迁移

先在新地图上重新确认每个点位的米制坐标和朝向。运行迁移脚本的预检查得到每张旧地图的点位 ID。然后提供以旧地图 ID 为键的 JSON，每项填写已保存的 `fastlio-…` 地图 ID，以及按旧点位 ID 索引的 `points`。每个点位填写 `x`、`y`、`yaw`，角度单位是弧度。提供 `--point-assignments FILE` 再执行一次预检查，确认点位和讲解数后在产品 API 停止期间加 `--apply`。脚本为这次迁移保留输入备份，讲解迁移后需复核才可运行。机器人上的旧地图文件原位保留。

## 运动联调

在现场记录 `McCommonState` 的消息类型、时间戳、稳定站立状态及遥控输入源行为。当前适配器读取 `/aima/mc/common/state`，按参考接口在 `SetMcInputSource` 请求中填写时间戳，并按 `response.header.code` 取服务返回码。`McCommonState` 的字段已与随附 SDK 对照，实际发布话题、超时和状态切换以目标机测量为准。完成实测外形、地图尺度和目标机标定后，才使用运动配置开展试验。

将该配置的 `motion_enabled` 设为 `true`，`navigation_mode` 设为 `point`，`lingxi_motion_idle_sources` 填现场测得的空闲输入源名称。`lingxi_motion_review` 指向 root 所有、服务可读且组和其他用户不可写的 JSON 文件。文件字段为 `firmware`、`aimdk`、`configSha256` 和 `maps`。`maps` 以活动地图的 `fastlio-…` ID 为键，对应值包含其 `metadata.json`、`map.yaml`、`map.pgm`、`localization.ply`、`localization.json` 的 SHA-256。更新 `LINGXI_REFERENCE_CONFIG_SHA256` 后重启参考服务。建图模式和导出的地图可以在运动关闭时先行验证。

导航页默认选定点，可在任务之间切换 Nav2。两种模式都要求同一运行代次内新鲜的 GICP 位姿、现场障碍点云、代价地图及 AIMDK 状态。暂停和停止会撤销输出，并等待 Nav2 的终态结果和停稳观测。异常退出后若数据目录仍有 `motion-in-progress`，先在现场确认停稳、旧控制源及控制进程均已退出，再归档该标记以恢复运动配置。运动试验必须配备现场操作人员和独立遥控急停。

可通过 `LINGXI_REFERENCE_ACTIONS=1` 和 `lingxi-next-actions.service` 接入 AIMDK 白名单动作、表情、灵创资源。该单元与 `lingxi-next-aimdk.service` 共用本机端口，启用前检查并停用同端口的旧桥接单元。AgentSDK 的部署继续使用 `lingxi-next-agent.service`，真实语音会话、打断和播放结束仍需在机器人上检查。讲解只有在本次导航的到达与停稳均确认后才推进。

## 验收范围

旧 X2 静态录包的前 10 帧原始 PointCloud2 和前 256 帧 IMU 已用隔离的 ROS Humble 反序列化。点云每点 32 字节，包含 `ring` UINT16 和 `timestamp` FLOAT64。相对纳秒转换后的最大点时间偏移为 95.181 ms。点云与 IMU 样本最大头时间戳间隔分别约 102.03 ms 与 10.54 ms。

在隔离的 x86_64 Humble 环境中构建了同一参考提交的 FAST-LIO、录制器、代价地图插件、`grid_export_native` 和 `nav_ground_native`。静态实录回放生成 341 帧配对的 `/Odometry`、`/cloud_registered_body` 和 `/fast_lio/gravity`，并录下机身 `/tf` 与 `/tf_static`。离线工具 `tools/replay_reference_mapping.py` 需要独立的仅环回网络命名空间。输入录包可以只读挂载。

参考 FAST-LIO 配置与该录包的 LiDAR–IMU TF 的矩阵元素最大偏差为 0.01198，超过参考导出器的核验阈值。用 `--use-recorded-calibration` 从同一录包的静态 TF 生成仅供回放使用的外参并重新采集后，地面几何检查通过。Patchwork++/OctoMap 导出 366 × 250、分辨率 0.05 m 的栅格和 30,864 个带法向的定位点。现场仍须核对目标机的安装外参。

在新的 Python 进程中重新加载 PLY，以同一录包的一帧扫描验证 GICP 跟踪匹配。给初值增加 0.08 m 和 −0.04 m 的平面偏移，匹配位姿距录包推导值约 0.003 m，点云配准 RMSE 为 0.024 m，内点比例为 98.18%。该结果测量的是同录包内的匹配一致性。现场独立重定位和尺度测量仍需验收。该次离线试验使用 x86_64 Open3D 0.19.0，参考 ARM64 环境依从固定提交的依赖版本。

旧 X2 动态录包也使用同次静态 TF 提取的外参完成离线回放。该环境以 `--rate 0.5 --use-recorded-calibration` 重放时保留了全部 24,446 条 IMU 样本的头时间戳，最大相邻间隔为 39.796 ms。1 倍速试验中，FAST-LIO 收到 90.754 ms 的 IMU 间隔后退出。半速试验保留原有 50 ms 输入阈值，输出 1,219 帧配对的里程计、机身点云及重力。点云、重力和足踝 TF 的地面几何核验通过，导出了 824 × 688、分辨率 0.05 m 的栅格和 279,167 个带法向的定位点。新进程重载后，对同一录包中分散采样的 5 帧施加相同初值偏移，GICP 配准 RMSE 为 0.025–0.033 m，内点比例为 98.27%–98.59%，平面位姿距录包推导值 0.003–0.015 m。实时吞吐和独立场景地图精度仍须在目标机上验证。

在隔离环境的直线路线仿真中，定点控制器在障碍进入、位姿缺失时停止输出非零速度，恢复后完成 2 m 目标，仿真到点误差 0.188 m。Nav2 使用参考的 SmacPlanner2D、RPP 和代价地图插件，在合成地图上生成 41 个路径位姿，收到障碍点云时发布零速度，清障后恢复并返回成功。实际仿真停位距原始请求点 0.251 m，超过该试验的 0.24 m 检查线。`FollowPath` 回执反映路径终点的判定，产品网关仍需按原始请求点与朝向核验到达和停稳。两项仿真均使用合成位姿与障碍数据。

待验收项目包括目标 ARM64 启动、实测移动建图、独立进程重载、按实测点校验地图朝向和比例、两种控制器的安全输出、障碍处置、遥控接管、急停与停稳、讲解与语音真实回调、Android 证书导入和横屏实机操作。定点控制和 Nav2 需要分别通过有现场人员和遥控急停的测试。参考网关的直接速度输出、Nav2 取消接受、定点模式的纯里程计续行均无法提供产品要求的停稳与定位证据。
