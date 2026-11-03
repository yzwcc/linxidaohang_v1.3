# 同一扫描帧定位 ROS 输出离线验证

日期：2026-11-03。源代码里程碑，晚于现有 0.1.22 安装包，未重建安装包或 APK。
本轮未连接机器人、调用厂商服务或发送运动指令，不属于实机定位或导航验收。

## 实现范围

新增 `live_localization_ros.py`，作为现有实时定位采集组合的显式、被动诊断输出。
只有 live `LocalizationSession` 且提供显式 `basePose.metresPerMapUnit` 时才可使用。
默认行为不变，不自动启用输出、Nav2、运动服务或厂商 SLAM。

采集 sidecar 可显式增加以下字段；这只是现有完整配置的片段，不是可直接部署的配置：

```json
{
  "localizationRosOutput": {"mode": "private_saved_map_observations"}
}
```

该选项不要求启用地形输出；仍需通过原有采集库、源码完整性和实时平台校验。
配置、输出模块及其依赖源码哈希绑定于运行输入，关闭报告另记录输出生命周期与发布次数。
零次发布可表示被动采集正常关闭，不能解释为已有可用定位证据。

- 原生工作线程先接收实际 `RegisteredAcquisition`，再在同一帧机身投影后取样。
  核对会话、代次、帧号、算法时间、扫描头时间、尺度和同帧 map-from-sensor 位姿。
  不使用历史子图、最新 TF、默认零速或重新计算的 epoch 浮点时间作为替代。
- 时间戳直接取机身投影中的整数 `effectiveScanNs`。点云、里程计和两段 TF 使用
  同一个原始采集时间，不用发布定时器的时间给旧数据续期。
- 将已配准的扫描端点逆变换到该帧雷达坐标系，尺度只应用一次；输出数组只读。
  连续两次实际观测计算机身坐标系有限差分速度，首帧或观测间隔超过 300 ms 不输出。
- 复用现有 ROS context 和 executor，原生线程只写单槽 mailbox，不调用 ROS 发布接口。
  steady timer 每 20 ms 检查一次，同一帧最多发布一次；采集收件时间与 ROS 原始采集时间
  分别限制在 300 ms 内。过期、未来时间戳不改写、不重播。
- 暂停、定位丢失、待处理控制请求、停止或代次变化撤销旧输出源；恢复 tracking 或迟到帧
  不自动重开该源，需要新的受管运行实例。清理按逆序独立尝试，局部失败不跳过其他资源。

每次实例生成随机私有命名空间 `lingxi_offline_<32hex>`，不发布到根 `/tf`：

| Topic 后缀 | 类型与坐标 |
| --- | --- |
| `odometry` | `nav_msgs/Odometry`，`lingxi_saved_map` 到 `base_link`，速度为机身坐标 |
| `points` | `sensor_msgs/PointCloud2`，`lidar_chest_front`，XYZ FLOAT64、米 |
| `tf` | `tf2_msgs/TFMessage`，同帧 `lingxi_saved_map -> base_link -> lidar_chest_front` |

诊断协方差对角为 1e6，不是实测标定值。`physicalScaleValidated`、
`physicalMotionSent`、`navigationReady` 始终为 false。命名空间隔离不等于消息认证。

## 验证结果

| 检查 | 本轮结果 |
| --- | --- |
| 数值测试 `test_localization_output.py` | 9 项通过 |
| 隔离真实 Humble `test_localization_output_humble.py` | 5 项通过，3.460 秒，输出 `localization_output_20261103_d` |
| Linux `test_live*.py` | 116 项中 108 项通过、8 项 opt-in 跳过 |
| Linux `test_localization*.py` | 116 项中 105 项通过、11 项 opt-in 跳过 |
| Linux 科学 Python `test_slam_bundle.py` | 11 项通过、1 项无 Node 跳过；包含解压后 `python -I` 的 11 个真实模块加载 |
| Windows `test_slam_bundle.py` | 11 项通过、1 项无科学依赖跳过；补验 Python/JavaScript 源码清单一致 |
| `test_release_scope.py` | 7 项通过，包括发行源码引用闭包 |

各测试集合有重叠，不相加。Humble 使用真实 DDS、ROS 消息和 tf2 Buffer，
雷达、位姿与外参均为合成输入；没有运行真实 GLIM、真实传感器或 AIMDK 输出。
验证包含大 epoch 的整数纳秒、旋转雷达外参、尺度和坐标组合、有限差分速度、过期与未来
时间拒绝、冻结 ROS 时钟、旧代次拒绝、部分启动失败及清理失败后的资源回收。
较旧 Humble SciPy 不接受只读旋转矩阵输入，已在序列化处复制小矩阵并通过真实环境复测。

重跑入口，`ROOTFS` 为既有独立 Humble rootfs，`OUTPUT` 必须为未使用的新绝对路径：

```sh
python -B -m unittest discover -s tools/tests -p test_localization_output.py -v
sudo bash tools/nav2-offline-service-isolated.sh --verify-localization-output ROOTFS open OUTPUT
```

## 尚未完成

当前是显式诊断发布入口，不是已接通的完整产品导航数据源。既有地形发布器仍使用自己的
私有 map frame 与定时器发布时间，尚未与本输出统一；不能把它与当前定位点云直接拼成
已验收的物理导航输入。本输出命名空间也尚未与实际定位 Socket、真实 GLIM 和实际 Nav2
组成端到端测试。

接下来优先统一地形坐标与原始采集时间，验证组合链路，再构建新 ARM64 安装包并进行
目标机运行、尺度/足迹/TF、AIMDK 输出及停止回执核验，之后才开展短距离导航和避障验收。
网页扫图仍为点云，Z 默认范围仍为 0.1-1.4 m，扫图移动仍由外置遥控器完成。
