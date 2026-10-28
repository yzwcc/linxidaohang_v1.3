# 目标 X2 只读检查

日期：2026-10-28。目标由用户当次提供，经 SSH 连接，主机密钥与该机器人先前记录一致。
没有安装本产品、修改厂商配置、停止现有服务或发送建图、重定位、导航、动作和语音请求。
报告不包含登录凭据、设备序列号、地图名称、地图内容或云端凭据。

## 当前开发方向

本文件下文记录原生方案的历史诊断。用户随后明确确认未选配厂商 SLAM，
并要求独立开源路线，先比较和适配验证，再决定全面重建。
原生授权错误仍为历史事实，但不再把开通厂商 SLAM 当作独立方案的前提。
新增真实原始点云、雷达 IMU、腿部里程计、传感器 TF 和速度接口的
只读检查见 OPEN_SOURCE_NAV_EVALUATION.md；这不代表自主行走或算法已验收。

## 系统与依赖

- ARM64（aarch64），Ubuntu 22.04.5，ROS Humble。
- 旧导航管理台和 Java 语音服务正在运行，管理台占用 3000 端口。
  本产品尚未安装，后续必须安排端口/服务切换，不能直接覆盖旧服务。
- `/home/agi/aimdk` 和 `/agibot/software/aimdk` 的源码版本文件均写
  `aimdk v0.0.0-g14fe2e8`，但这不能单独判定已编译消息依赖的版本。
- 用户提供的 faf4c945 SDK 目录包含预编译的 `aimdk_msgs`：
  `src/aimdk_msgs/prebuilt_aarch64/share/aimdk_msgs/local_setup.bash`。
  该目录 `version` 为 `aimdk v1.1.0-gfaf4c94`；这证明附件 SDK 版本，
  不证明正在运行的厂商进程已开放同版本接口。
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

## 原生配置交叉核对

- 附件内 `src/py_examples/py_examples/slam.py` 使用
  `/integrated_command`，QoS 为 RELIABLE / TRANSIENT_LOCAL；
  随包 SLAM 文档也列出 `/relocalization_pose` 与 `GetStoredMapByName`。
  随包 `quick_start/prerequisites.html` 明确要求 SDK 与机器人固件版本对应。
- 正在运行的 `slam` 使用 `/agibot/software/nav/config/slam/slam_config.yaml`。
  配置声明建图/定位点云与里程计输出，但声明不代表当前已创建发布端或开始建图。
  其 RPC 规则选用 MQTT；`map_manager` 的 RPC 规则另开放 MQTT、HTTP、ROS2。
  这些内部规则不等同于公开 SDK 的字符串命令和专用服务协议。
- 独立只读探测加载附件消息和厂商实际
  `/agibot/software/entry/cfg/ros_dds_configuration.xml`，确认其白名单内网接口存在。
  6 秒发现窗口内，公开建图/重定位接收端仍为零，三个 SLAM 里程计和建图点云
  发布端仍为零，任务状态发布端可发现。不能简单归因于用户切换了局域网 IP。
- 图中可发现 `aimdk.protocol.MappingService` 等内部服务，类型为
  `ros2_plugin_proto/srv/RosRpcWrapper`，以及旧 `GetStoredMap` 服务。
  仅记录接口元数据，没有调用、构造内部协议负载或将其当作 v1.1.0 公开接口替代。
- `task_manager.version` 为 `0.2.221`，文件记录提交时间为
  `2026-07-28 16:51:23`。这是组件版本，不是整机固件版本，不能据此断言固件过旧。
  后续检查已确认下节所述原生组件授权启动失败；仍需厂商确认授权恢复后的
  固件/SDK 接口配套，不自动升级或更改厂商配置。

## 后续确认的原生启动阻断

这是 0.1.19 打包后的补充检查，没有重新生成或覆盖该版本交付件。

- `/agibot/software/firmware_version` 为
  `release-lx2501_3_t2d5-soc1-v1.1.5`；`metadata.yaml` 也标记 `v1.1.5`，
  且清单仅含 SoC1。不能据此认定其他控制器或整机全部组件同版本。
  清单内 `aimdk` 来源 release 为 `v1.1.0-rc10`，与独立附件的
  `v1.1.0-gfaf4c94` 不是同一个构建标识。
- 原生 `/agibot/software/nav/bin/auto_integrated` 进程存在，使用原厂配置；
  ROS 图中也能发现对应节点。但其启动报告的订阅表为空，
  `/integrated_command` 和 `/relocalization_pose` 接收端仍为零。
- `auto_integrated.log` 与 `map_manager.log` 均出现
  `module can't start because license denied`，并包含 `SignatureInvalid(5)`。
  这是已确认的原生组件启动阻断，不是点云显示或本产品地图 ID 转换造成的。
  不能仅由该错误判断用户未购买功能或整机授权全部无效。
- 通过公开只读 `GetSystemState` 查询，响应状态为成功，当前状态为 `Business`。
  没有调用 `MigrateSystemState`。附件系统状态文档说明 `Develop_Nav`
  会停用厂商导航/建图/规划/感知，因此没有通过切换该模式尝试修复接口。
- 附件离线 SLAM 文档明确标记选装，并要求联系售后技术支持开通接口。
  本轮在线文档未能成功取回，此结论以附件离线文档为依据。

只读取启动诊断并输出脱敏错误类别，没有读取或修改授权文件，也未记录
授权 UUID、签名、密钥、授权文件路径、设备序列号或业务数据。
应先由厂商恢复相应组件的有效官方授权，再复核公开 SDK 接口是否就绪；
授权修复不自动证明 `GetStoredMapByName` 版本兼容、建图或导航已验收。
可交给售后的脱敏问题单见 VENDOR_HANDOFF.md。

## TEXT 地图 ID 兼容修复

只读检查确认 `map_id` 声明类型为 TEXT，当前检查的两行也实际返回字符串；
两个值均符合正十进制整数格式和 JavaScript 安全整数范围。
没有输出地图名称、原始 ID、坐标或其他业务字段。

0.1.18 读取器仅接受 Python 整数，会误拒绝这种登记。
0.1.19 仅将规范 ASCII 正十进制字符串转为精确整数，保留安全上限，
并按数值倒序读取；空值、前导零、正负号、空白、小数、指数、非 ASCII 数字、
二进制及超界值仍拒绝。SQLite 保持 `mode=ro` 与 `query_only`，不修改厂商数据库。
新增有效 TEXT ID 回归先在旧实现失败；修复后全部 39 项 Python 测试通过，
覆盖数值排序、精度上界和数据库字节不变。该修复不使缺失的地图服务自动就绪。

仍待现场执行：地图保存/重载、导航与急停、讲解打断、
语音回调/云端、Android 真机和声学首响/误唤醒验收。
