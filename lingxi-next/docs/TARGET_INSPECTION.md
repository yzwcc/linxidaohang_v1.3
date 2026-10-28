# 目标 X2 只读检查

日期：2026-10-28。目标由用户当次提供，经 SSH 连接，主机密钥与该机器人先前记录一致。
没有安装本产品、修改厂商配置、停止现有服务或发送建图、重定位、导航、动作和语音请求。
报告不包含登录凭据、设备序列号、地图名称、地图内容或云端凭据。

## 系统与依赖

- ARM64（aarch64），Ubuntu 22.04.5，ROS Humble。
- 旧导航管理台和 Java 语音服务正在运行，管理台占用 3000 端口。
  本产品尚未安装，后续必须安排端口/服务切换，不能直接覆盖旧服务。
- `/home/agi/aimdk` 和 `/agibot/software/aimdk` 的源码版本文件均写
  `aimdk v0.0.0-g14fe2e8`，但这不能单独判定已编译消息依赖的版本。
- 用户提供的 faf4c945 SDK 目录包含预编译的 `aimdk_msgs`：
  `src/aimdk_msgs/prebuilt_aarch64/share/aimdk_msgs/local_setup.bash`。
  已以 root 在独立进程中直接加载该脚本并确认 `GetStoredMapByName` 类型可导入。
  该 SDK 根目录当次权限为 0707、属主/组为 agi；run 属于 agi 组，
  因此不能直接遍历该目录。没有更改权限。
- 本次探测在独立进程中加载 ROS 和旧部署内的隔离 AIMDK v1.1.0 消息依赖；
  仅用于核对消息类型，没有读取、复制或修改旧版业务代码。
  新产品的正式部署不得依赖会随旧版清理的 release 路径。
- `GetStoredMapByName`、`SetMcInputSource`、`GetMcPresetMotionState`、
  `TaskMasterState`、`PncTaskRequest` 和 `PncTaskResponse` 类型可以加载。

## 实时扫描

真实数据来自：

```text
/aima/hal/sensor/lidar_chest_front/lidar_pointcloud_down_sampling
sensor_msgs/msg/PointCloud2
```

以 run 账号只读订阅 6.01 秒收到 12 帧。末帧坐标系为 `lidar_chest_front`，
25,430 点，`height=1`、`point_step=32`、`row_step=813760`，
XYZ 字段为 FLOAT32；另有 intensity、ring 和 timestamp。
只记录布局和计数，没有保存或传出点坐标。

同次收到 TF 数据，但未取得地图至机体/雷达的完整变换链。
`/slam/lidar_odom`、`/slam/localization/odometry`、`/slam/mapping/odometry`
在后续发布端检查中均为零发布端。话题名字出现在图中不等于有实时数据。
当前只能确认传感器坐标的真实点云可接收，不能认定地图坐标累计、
机器人箭头、轨迹和导航定位已验收；不得伪造 TF 或用栅格代替扫描。

## 状态订阅修复

发现目标上的两个发布端均为 BEST_EFFORT / TRANSIENT_LOCAL：

```text
/task_master/state
/aima/pnc/pnc_task_response
```

0.1.17 桥接要求 RELIABLE，无法匹配这些 BEST_EFFORT 发布端。
0.1.18 将这两项订阅改为 BEST_EFFORT / TRANSIENT_LOCAL，保留控制命令
原有 QoS，不改动消息、关联 ID、状态确认与失效处理。
也核对了原生 PNC 请求接收端，其要求 BEST_EFFORT / VOLATILE，
与现有请求发布端一致，无需修改。

真实只读对照：RELIABLE 订阅在先前 6 秒窗口未收到任务状态；
BEST_EFFORT 订阅在后续 5.08 秒窗口收到 25 条任务状态。
当次 `main_state=0`、`sub_state=0`、`slam_state=0`。
没有派发导航，故不把导航响应的发布端发现当成到达/失败回执验收。
本地新增构造器回归测试验证这两项订阅的 QoS 及控制命令 QoS 不变；
测试先在旧实现失败，修复后通过。

## 尚未就绪

同一 ROS Domain 0 的当次发现结果：

| 接口 | 结果 |
|---|---|
| `/integrated_command` | 零接收端 |
| `/relocalization_pose` | 零接收端 |
| `/aimdk_5Fmsgs/srv/GetStoredMapByName` | 未发现服务端 |
| `/aimdk_5Fmsgs/srv/GetStoredMap` | 发现另一种服务；当前 v1.1.0 消息包没有该类型，不可仅改服务名替代 |
| 原生 PNC 请求/响应 | 已发现发布/接收端，未执行导航 |
| 只读地图登记 | 可读取 `map.db`；`map_id` / `map_name` 为 TEXT，存在 `grid_map_info.txt`；未保存业务内容 |

编译有消息定义不等于机器人运行时已开放该版本的全部接口。
需由现场或厂商确认当前原生软件支持及启用 AIMDK v1.1.0 地图接口的条件，
再检查定位/TF、保存重载与原生导航。不能通过改名调用不同类型服务、
绕过状态检查、写地图数据库或替换厂商系统来宣称适配成功。

仍待现场执行：地图保存/重载、导航与急停、讲解打断、
语音回调/云端、Android 真机和声学首响/误唤醒验收。
