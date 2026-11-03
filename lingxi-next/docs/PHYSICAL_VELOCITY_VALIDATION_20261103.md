# 物理导航速度链路离线验证

日期：2026-11-03。本次是晚于 0.1.22 的源代码里程碑，未更新部署压缩包或 APK，
未连接机器人、调用厂商服务或向机器人发送运动指令。测试中的 AIMDK 消息、服务响应和输出
发布器全部为内存替身，不是厂商接口的实机验收。

## 本轮实现

- `physical_velocity.py` 将订阅固定为 `/lingxi_nav_<epoch>/observed_cmd_vel`。
  任务不能复用 epoch，也不能把共享 `/cmd_vel` 的消息重新贴上新任务身份。
- `PhysicalTwistSource` 要求该话题只有一个同命名空间的 `velocity_smoother` 发布者，
  检查图端点 GID 连续性，拒绝非平面 Twist。专用订阅节点不加入 executor，
  用 Humble 原始 take 接口保留 DDS 源时间，不用回调到达时间延长消息寿命。
- `VelocityLimits` 是显式、不可变、与地图证据绑定的配置。超出前进/侧移/角速度或
  加减速度约束的消息会拒绝并置零，不静默裁剪为另一条运动指令；当前不允许倒退。
  这些限制不是机器人实测能力或现场净空的证明。
- `AimdkVelocityOutput.command` 在发布前复核当前任务、地图、传感器准入、AIMDK 输入源、
  消息时间与顺序。旧任务和已清退任务的迟到消息无效；过期控制流不能被新包复活。
- `ControllerVelocityChannel` 先撤销失效控制的权限再请求零速。任务终止、暂停和停止时
  关闭订阅源；只有源已静默、Action 已终止、AIMDK 零速状态回执有效，才完成清退。
  控制故障只能经显式且确认的停止解除，不因状态重新变好而自动恢复。
- `physical_motion_sent` 保留本进程曾尝试下发非零速度的记录；它不证明机器人实际移动。
  来自其他 AIMDK 输入源的状态会立即撤销此前保留的本源就绪状态。
- DDS 时间读取器移入 `robot-navigation/lingxi_navigation/velocity_source.py`；
  `tools/humble_velocity_source.py` 保留兼容导入，现有离线 Nav2 工具沿用同一实现。

## 验证结果

- Windows 定向 `test_physical*.py`：50 项通过；Linux Socket / opt-in ROS 测试跳过。
- WSL 普通用户 `test_physical*.py`：62 项通过；opt-in ROS 测试单独在隔离环境运行。
- DDS 时间转换与读取器单测：12 项通过。
- 隔离真实 Humble/FastDDS 速度测试：5 项通过，覆盖实际消息时间、过期队列、重复发布者、
  旧命名空间隔离，以及实际 Action/lifecycle DDS、速度消息、内存 AIMDK 输出的组合。
  组合测试还确认取消 ACK 不会提前完成清退。
- 隔离真实 Action/lifecycle 协议回归：4 项通过；真实 tf2/传感器证据回归：4 项通过。
- 现有真实 Nav2 `open` 离线回归通过，规划器/控制器实际运行，236 条速度消息，
  模拟最终位置约 1.854 m；`physicalMotionSent=false`、`navigationReady=false`。
  该场景验证时间读取器迁移未破坏既有离线工作流，不是新物理通道驱动 Nav2 或机器人。

定向复测命令（`ROOTFS` 必须是独立的 Humble rootfs，输出名不得复用）：

```sh
python3 -B -m unittest discover -s tools/tests -p 'test_physical*.py' -v
python3 -B -m unittest discover -s tools/tests -p test_humble_velocity_source.py -v
sudo bash tools/nav2-offline-service-isolated.sh --verify-physical-velocity ROOTFS open /new-velocity-check
sudo bash tools/nav2-offline-service-isolated.sh --verify-physical-action ROOTFS open /new-action-check
sudo bash tools/nav2-offline-service-isolated.sh --verify-physical-evidence ROOTFS open /new-evidence-check
```

## 仍需完成

生产入口 `build_runtime()` 未装配控制通道；其根命名空间 Action 客户端也不满足每任务
独占命名空间要求。需实际管理每任务 Nav2 生产进程与 Action 客户端的启动、就绪、清退和
退出，不能简单向当前构造函数补一个参数就宣称集成完成。缺少通道时物理导航准入关闭。

`FileMapActivator` 尚未执行 Nav2 地图加载或定位初值设置。还需接入已保存地图、
实测标定和足迹，并完成 ARM64 目标加载、机器人传感器/输入源验证及现场导航避障验收。

Humble Python 的逐消息元数据只有源/接收时间，没有逐包发布者 GID；图端点检查不是
DDS 身份认证，也不能证明消息在控制器中的计算时间。部署仍需隔离并独占任务生产者，
不能据此声称能够抵御同域恶意发布者。真实 Nav2 控制器通过新通道驱动物理输出尚未验收。
