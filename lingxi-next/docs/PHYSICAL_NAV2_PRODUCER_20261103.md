# 独占 Nav2 生产者离线验证

日期：2026-11-03。源代码里程碑，晚于现有 0.1.22 安装包，未重建安装包或 APK。
未连接机器人、调用厂商服务或发布物理运动指令，不属于实机导航验收。

## 实现范围

- `physical_nav_bridge.py` 已装配 `OwnedNav2Backend`，不再使用根命名空间的共享 Action。
  显式停止及本源新状态确认后，才启动未领取目标的预热 epoch。未就绪时拒绝 start/resume，
  不排队、重放旧目标。每次继续使用新 epoch、ROS 节点、Action 客户端及速度读取器。
- 每个 epoch 在私有目录生成参数、行为树、启动脚本及 SHA-256 清单。
  仅启动本源 planner、RPP controller、BT navigator、velocity smoother 和 lifecycle manager，
  不启动厂商节点，不制造传感器、TF 或定位数据。控制链为
  `raw_cmd_vel -> velocity_smoother -> observed_cmd_vel -> AIMDK 适配器`。
- 物理参数使用实际时间、显式 topic/frame 和保存地图坐标系中的里程计/地形。
  全局/局部 costmap 由本 epoch 独占，更新和发布均为 10 Hz，保留 300 ms 证据截止线。
  默认障碍物 Z 范围仍是 0.1-1.4 m；网页扫图仍显示点云、移动由外置遥控器完成。
- DDS 回调按最多 64 次/5 ms 的边界排空。TF 到达稍晚时只短暂保留此前已配准的
  点云/位姿对，保留其原采集时间和位姿，最多 300 ms；新消息不会给旧证据续期。
- 输出前增加显式变化率整形，采用实际下发间隔与 DDS 源间隔的较小值；最终仍由严格
  `command()` 复核。消息时效、序号、身份、速度上限或传感器证据失效仍拒绝并撤销输出。
- 暂停/故障先撤销速度源并请求零速，再取消 Action；取消 ACK 不等于停止。
  终止或等待 300 ms 后关闭 ROS 资源并终止整个独占进程组，600 ms 后可强制退出。
  只有全部生产进程退出和本任务新零速状态回执同时成立才交还输出所有权。
- 启动最多等待 30 秒；超时或进程崩溃进入故障，必须显式停止确认，不自动再次启动任务。
  资源退出分别尝试，某一 ROS 清理失败不会跳过其他资源或进程终止。
- 导航通过后台只读线程查询现有 GLIM 定位服务的 `navigation_snapshot`，ROS/输出线程
  不等待 Unix Socket。查询同时校验地图回执、points/profile 哈希、服务/运行/seed 代次、
  tracking 状态与带原始时间戳的机身诊断位姿。Socket 总期限 250 ms，快照期限 300 ms，
  定位帧无进展 500 ms 撤销授权；查询失败、重新 seed 或服务重启均要求显式重新确认地图。
- `activate_map()` 现可确认本导航进程固定地图与当前定位代次，不再无条件抛出未接入错误。
  它不会启动定位、替用户 seed 或加载其他地图；须先在现有定位页面选图并提交胸部雷达
  4×4 初值，持续跟踪后再确认。另一张地图需停止并重新配置/启动导航进程；平面
  `initialPose` 不适用于胸部雷达 seed，因此明确拒绝且不改变当前代次。
- 定位授权只是附加否决条件。按 `effectiveScanNs` 精确查找机身里程计历史并比较平面位置
  和朝向，不能用最近帧代替；匹配后仍需通过原有传感器、TF、地形、足迹和输出检查。
  不将诊断位姿提升为物理标定结果，定位服务的 readiness/physicalScaleValidated 仍为 false。

## 配置要求

保持 `LINGXI_INDEPENDENT_PRODUCT=0` 及服务未启用，直到完成目标机核对。
显式调试入口额外要求以下外部配置，不能直接拿测试值作为机器人实测值：

| 配置 | 约束 |
| --- | --- |
| `LINGXI_NAV2_PROFILE_FILE` | 直接绝对路径，完整 JSON，足迹哈希与保存地图回执一致 |
| `LINGXI_NAV2_RUN_ROOT` | 已存在、服务 UID 所有、权限 0700 的直接绝对目录，每 epoch 使用新子目录 |
| `LINGXI_MAP_ROOT` / `LINGXI_ACTIVE_MAP_ID` | 已验证保存地图目录及地图身份；不是定位成功证明 |
| `LINGXI_FOOTPRINT_FILE` | 与保存地图证据哈希一致的外部足迹文件 |
| `LINGXI_POINTCLOUD_TOPIC` / `LINGXI_ODOMETRY_TOPIC` / `LINGXI_TERRAIN_TOPIC` | 实际点云、保存地图坐标系位姿、带新采集时间的地形 topic |
| `LINGXI_MAP_FRAME` / `LINGXI_BASE_FRAME` / `LINGXI_SENSOR_FRAME` | 固定为 `lingxi_saved_map` / `base_link` / `lidar_chest_front`，须真实存在 |
| `LINGXI_TF_TOPIC` / `LINGXI_TF_STATIC_TOPIC` | 默认 `/tf` / `/tf_static`，可显式指定 |
| `LINGXI_NAVIGATION_SOCKET` | 沿用私有目录/Socket、同 UID 和受管产品配置要求 |
| `LINGXI_LOCALIZATION_SOCKET` | 当前 SLAM owner 的定位服务 Socket；绝对直接路径、父目录 0700、Socket 0600、同非 root UID；owner 重启后重新配置/启动导航 |

固定地图回执须为已通过新进程重载的 `map-ready.json` schema 2/3，并绑定 points/profile 哈希。
地图目录按回执身份解析，最多检查 2048 个直接目录条目；重复地图身份拒绝，不能靠目录名选图。
每次准备新 Nav2 epoch 前重新核对地图身份、定位绑定及足迹，不能沿用更换后的文件。

profile 字段必须完整且不得重复或增加未知字段：`schema=1`、`footprintSha256`、
`clearanceRadiusMetres`、`stoppingMarginMetres`、`forward`、`lateral`、`angular`、
`acceleration`、`deceleration`、`angularAcceleration`、`commandAge`、`firstCommandTimeout`。
当前只支持前进和转向，`lateral` 必须为 0；各速度/距离/时间分别使用 m/s、m、s，
角速度/角加速度使用 rad/s、rad/s²。测试配置不是自动启用模板。
costmap topic 从 epoch 推导，旧环境变量不能覆盖为共享 costmap。

## 验证结果

- 生产者单元测试：21 项通过，覆盖参数/清单、预热、显式停止、暂停/新 epoch 恢复、
  启动失败、旧回调隔离、定位授权及异常清理。
- Nav2 profile 回归：17 项通过；DDS 时间读取器：12 项通过；速度单测：18 项通过。
- Linux 普通用户 `test_localization*.py`：102 项中 96 项通过，6 项环境 opt-in 跳过。
  包含真实私有 Unix Socket 的服务/读取器往返、合成定位 worker 的丢失/恢复及有界退出；
  不表示真实 GLIM 算法、真实采集或物理标定通过。
- Linux 普通用户 `test_physical*.py`：84 项中 69 项通过，15 项 opt-in 测试跳过。
  这些 ROS opt-in 测试另在隔离 Humble 运行：传感器/tf2 6 项、Action/lifecycle 4 项、速度 5 项通过。
  精确原时间戳查询与过期拒绝使用真实 DDS/tf2；定位 veto 后迟到速度被拒绝且输出置零，
  恢复 veto 不自动重新 arm。
- 打包范围与原生源码引用闭包检查：7 项通过；后续独立安装包必须包含生产者模块与启动脚本。
- 实际 Humble Nav2 planner/controller/BT/smoother 进程组合测试连续 3 次通过
  （输出 `nav2_producer_20261103_o/p/q`）：每次暂停前至少 10 条、
  新 epoch 恢复后至少另 10 条非零有效指令进入内存 AIMDK 发布器，传感器停止后故障置零，
  旧进程组退出且不自动重启。传感器、地形、位姿和 AIMDK 发布器均为测试替身；
  机器人未连接，测试中的 `physicalMotionSent` 只表示内存非零发布尝试，不表示物理运动。
- 新增定位 guard 的实际 Nav2 组合测试连续 2 次通过（输出
  `localization_producer_20261103_c/d`）：确认地图前不预热，暂停后使用新 epoch，点云仍正常
  发布时单独撤销定位会置零并退出进程组；定位恢复不重启旧任务，显式停止/确认地图/新任务
  后才重新输出；随后传感器停止也置零退出。此组合中的定位快照和传感器仍为合成输入，
  未运行真实定位 Socket 或 GLIM；真实 Socket 往返由上述独立测试覆盖，不能合并声称实机
  端到端导航通过。两次组合分别约 13.1/11.5 秒，均只使用内存 AIMDK 发布器。

重跑入口，`ROOTFS` 必须为独立 Humble rootfs，`OUTPUT` 必须是未使用的新绝对输出路径：

```sh
sudo bash tools/nav2-offline-service-isolated.sh --verify-nav2-producer ROOTFS open OUTPUT
sudo bash tools/nav2-offline-service-isolated.sh --verify-physical-evidence ROOTFS open OUTPUT
sudo bash tools/nav2-offline-service-isolated.sh --verify-physical-action ROOTFS open OUTPUT
sudo bash tools/nav2-offline-service-isolated.sh --verify-physical-velocity ROOTFS open OUTPUT
```

开发中曾遇到一次第二代 controller configure 的 FastDDS 服务响应超时，未开始输出；
其后多次复测通过，但未证明 DDS 偶发超时根因已消除。生产逻辑保留 30 秒启动截止线和
故障后显式停止要求。另修复了 Nav2 smoother 首帧定时器相位造成的变化率拒绝，以及
costmap 发布节拍/回调积压造成的旧证据超时；未放宽速度或证据时限。

## 未完成项

尚未完成定位结果按原始采集时刻发布 ROS odometry/TF/点云的完整产品链路，当前 guard
只核对已有外部数据源，不能制造或补齐数据；定位 Socket 可读也不等于可导航。
现有定位服务中的地图加载/胸部雷达 seed 沿用独立页面流程，导航只确认当前固定地图和代次。
还需补齐该发布链路、ARM64 实际运行、现场传感器/TF/足迹核对、AIMDK 输入源及停止回执验证，
随后才能进行短距离导航、动态避障、讲解打断恢复等现场验收。
同域 DDS 图端点检查不是消息认证，不承诺抵御同 ROS 域的恶意发布者。
