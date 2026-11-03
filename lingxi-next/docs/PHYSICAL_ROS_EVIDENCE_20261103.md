# ROS 物理证据边界验证

日期：2026-11-03。范围仅为独立物理桥的传感器接入与有效期，不是实机导航验收。
这些源代码更改晚于 commissioning 0.1.22；现有安装归档和 APK 的身份不变。

## 已实现

- 接收真实 Humble `PointCloud2.data=array('B')` 与 `OccupancyGrid.data=array('b')`，
  拒绝多字节数组冒充消息字节。用户点云显示的默认 Z 范围仍为 0.1–1.4 m。
- 官方 tf2 缓存累计拆分到达的变换；静态 TF 支持发布早于订阅的 transient-local 交付。
- 分别在点云、里程计的纳秒采集时刻检查 map/base/sensor 变换，不依赖同包完整链。
- 依据源采集时间撤销过期数据，重复或倒序时间戳不续期。桥收到最旧必需采集时间，
  保留其更严格的 300 ms 新鲜度判断；轮询不会延长证据寿命。
- 未初始化时间、ROS/稳态时钟偏差超过 50 ms、回拨、TF 环路、父节点或静态几何变化
  锁住本次证据所有者；需排障后重建所有者，不通过轮询自动恢复。
- TF 单条消息上限 128 个变换，库存上限 256 个子坐标系；初始化中途失败会清理订阅。

## 检查结果

- Portable 传感器边界：14 项通过，包括采集年龄向物理桥传递、库存上限和停止清退。
- 原 ROS 解析/AIMDK 输出适配：8 项通过。
- WSL 普通用户 `test_physical*.py`：45 项通过、4 项 opt-in ROS 测试跳过。
- 定向 Vitest（physical-navigation、independent-product）：44 项通过，3 项 Linux-only 跳过。
- 隔离真实 ROS 2 Humble/FastDDS/tf2：4 项通过，覆盖晚到静态 TF、拆分动态链和不同
  采集时刻、缺少新 TF、DDS 旧数据/空闲过期，以及切换暂停 ROS 时钟后的锁定。

真实 ROS 测试只在 loopback 网络、独立 mount/IPC/PID 命名空间和只读源代码中运行；
没有 AIMDK 输出发布者，没有机器人、麦克风、云服务或实际运动 IO。

```sh
python3 -B -m unittest discover -s tools/tests -p test_physical_evidence.py -v
python3 -B -m unittest discover -s tools/tests -p test_physical_ros.py -v
# 使用已有的隔离 Humble rootfs；输出路径必须是新的。
sudo bash tools/nav2-offline-service-isolated.sh --verify-physical-evidence ROOTFS open NEW_OUTPUT
```

## 尚未接通

- Nav2 controller 非零速度尚未转发 AIMDK；现有输出类只有注册、arm 和零速清退。
- 地图激活类当前核验哈希证据，不执行 Nav2 地图加载或定位初值设置。
- 新鲜的地形栅格不能替代标定、地面支撑、尺度和安全距离核验。
- ARM64 目标试运行、真实机器人 topic/frame 核对和现场验收仍需执行。
- 本更新不自动启用可选物理桥，不表示机器人导航、避障、Android 硬件或语音延迟合格。
- Nav2 内部栅格不取代用户要求的实时点云建图显示。
