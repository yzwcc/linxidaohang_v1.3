# 独立开源建图与导航适配评估

日期：2026-10-28。状态：**只读输入预检、逐点时间复核与离线配置合成检查已完成；
动态标定、算法构建/执行、重定位及自主行走尚未验证。**

## 本轮范围与架构变更

用户已明确确认目标 X2 未选配厂商 SLAM，要求比较多个开源项目，
先做适配验证，再决定全面重建。不再把购买或修复厂商 SLAM 授权作为独立路线的前提。
旧 V1.3.9/R19 只作行为验收和经验参考，不复制其业务实现。

现有 0.1.19 产品代码仍连接厂商原生建图/导航接口，**尚不能使用下述开源后端**。
本轮仅增加评估资料、只读采样工具、离线配置生成器和测试；
没有替换运行时、安装算法或重打交付包。
已交付的 0.1.18/0.1.19 归档和 APK 均未修改。

独立路线使用公开传感器与受支持的运动接口，自行承担地图、定位、路径规划和障碍处理；
保留厂商步态、平衡和硬件急停，不修改其授权机制或全身控制器。
不能把“保留厂商步态”写成“原生自主避障仍然有效”。

## 候选比较

以下是基于上游文档和本机输入证据的工程判断，不是同机算法跑分。
仓库、分支和本轮查询的完整提交 SHA 记录在 `slam-candidates.json`；
这些是评估基线，不是已锁定全部传递依赖的部署清单。

| 项目 | 用途及上游条件 | 本机匹配与风险 | 本轮排序 |
|---|---|---|---|
| GLIM + glim_ros2 | 三维激光惯性建图；官方 ROS 2 包；文档列出 Ubuntu 22.04、ARM64 和 Jetson Orin 测试环境；有 CPU/GPU 路径 | 本机 Humble、Orin NX、雷达 IMU 和静态外参路径有利；仍须核实逐点时间单位、标定、CUDA/依赖配套、内存和实时性。保存会话不等于已完成启动重定位 | 优先验证的三维建图候选，未定为最终产品 |
| FAST-LIO2（hku-mars/FAST_LIO） | 激光惯性里程计/建图；文档有 ARM 平台；要求同步 IMU、逐点时间及外参；核对的上游为 ROS 1/catkin | 算法计算效率值得对照，但不能直接作为本机 Humble 包；ROS 2 移植需单独审查，回环、持久化定位与任务层不能默认齐全 | 算法对照，不直接引入未审查分支 |
| LIO-SAM 官方 ros2 分支 | Humble/22.04；文档要求 ring、相对点时间、机械式雷达、有效姿态 IMU，建议至少 200 Hz | 本机有 ring 和约 197 Hz IMU，但雷达型号、姿态可信度、坐标及时间字段尚不满足已验证条件。四元数模长为 1 不证明真实姿态有效 | 有条件备选，不因消息长得相同就接入 |
| KISS-ICP | 激光里程计；官方 ROS 2 封装，可作不依赖 IMU 的对照 | 可帮助区分激光配准和惯性适配问题；仅有里程计不能代替完整回环、地图重载、重定位和导航；步行晃动下表现待测 | 轻量对照，不单独承诺完整导览 |
| SLAM Toolbox（humble） | 二维 SLAM、图保存/恢复与定位；输入 LaserScan 和有效 odom→base TF | 本机是三维胸部雷达和步行平台，需要先验证姿态补偿、平面投影和里程计；不能简单裁剪点云就假设等价平面激光 | 地面平整场景的二维备选 |
| Nav2（humble） | 导航框架，不是激光惯性建图算法；需定位/TF、传感器、规划器、控制器和底盘接入 | 可承担独立任务执行；须适配 AIMDK 速度消息、控制权、步行阈值与失效停车；不能直接把 `/cmd_vel` 连到真机 | 下游导航候选，等待建图定位与安全验证 |

### 许可证与交付

本轮核对到 GLIM、KISS-ICP 为 MIT，FAST_LIO 为 GPL-2.0，
LIO-SAM 的 ros2 基线为 BSD-3-Clause，SLAM Toolbox 为 LGPL-2.1。
Nav2 根 LICENSE 列出多种许可证，须逐包查看 `package.xml`，
不能把整个组合统一写成 Apache-2.0。
在固定的 glim_ros2 提交中，`package.xml` 声明 MIT，根目录没有独立 LICENSE 文件；
此处记录包声明，不将其写成已核验的根许可证正文。全部传递依赖仍需单独核对。

这是依赖筛选记录，不是法律结论。最终交付前建立逐包版本、许可证、
修改记录和 NOTICE 清单，并履行实际分发要求；不默认“开源”等于无条件闭源交付。

## 只读实机输入验证

使用 `tools/slam_preflight.py` 在独立进程中运行三次，每次约 8 秒；
前两次分别观察原始/下采样点云，第三次复核原始点云时间字段。
仅订阅标准消息并输出聚合统计，不发布 TF、速度或动作，不注册输入源、
调用服务或保存环境点坐标。独立检查了 ROS 接口图和系统资源。
SSH 主机密钥与此前同一机器人记录匹配，全部检查会话已关闭。

### 计算平台

- ARM64，Ubuntu 22.04.5、ROS 2 Humble，8 个逻辑 CPU。
- 设备模型报告 NVIDIA Jetson Orin NX；总内存约 15.3 GiB，当次可用约 9.7 GiB。
- `/usr/local/cuda-12.6` 及标准路径 `nvcc` 存在，L4T 为 R36.4.3。
  这些是文件/平台检查，不等于 GLIM GPU 编译或推理已成功。
- 当次 1 秒 CPU 忙碌比例约 35.3%；另一个时刻负载均值接近 8。
  短快照不是剩余算力保证，须与现有语音等服务并发测量。
- 有 CMake、g++、colcon 和 rosbag2。Docker CLI 存在，但本次无法连接 daemon，
  非交互 sudo 查询也未成功；没有为此启动 daemon 或修改用户组/服务。
  本机 WSL Ubuntu 24.04 为 x86_64，检查时缺少 Docker、CMake、ROS 和 GTSAM。
  因而本轮还没有匹配目标环境的可用隔离构建结果；
  这些限制不意味着 GLIM 必须依赖 Docker，也不构成编译失败的算法结论。

### 数据观测

| 输入 | 本轮观测 | 结论边界 |
|---|---|---|
| 原始 `/aima/hal/sensor/lidar_chest_front/lidar_pointcloud` | 58 帧，首末帧到达间约 10.00 Hz；每帧 25,249–25,328 点；采样 XYZ 均有限 | 采样窗口包含发现/预热，不能按 80−58 断言稳态丢失 22 帧；正式验证要单独计发现/预热和稳态窗口 |
| 下采样同名 `_down_sampling` 话题 | 17 帧，首末帧到达间约 2.01 Hz | 原始流更适合优先做里程计；前端可以独立限流，不能拿 2 Hz 显示流代替高频算法输入 |
| 点布局 | 小端，point_step=32；x/y/z FLOAT32，intensity FLOAT32，ring UINT16，timestamp FLOAT64 | 布局可解析不代表算法已兼容 |
| 逐点 timestamp | 前两轮观察到跨度约 9.47×10^7；第三轮 81 帧的全部采样均只匹配 `relative_nanoseconds` 假设 | 支持帧相对纳秒解释，而非绝对纳秒；仍不等于动态对齐/标定通过，不自动批准配置 |
| `/aima/hal/sensor/lidar_chest_front/imu` | 两轮约 196.97/196.79 Hz；无非递增 header；加速度模长约 9.84–9.86；角速度字段模长约 0.068–0.073 | 仍需核实角速度单位、偏置、时钟和运动响应；不能凭字段命名就认为厂商数据符合全部 SI 约定 |
| IMU 姿态 | 四元数模长为 1，未标记 covariance[0]=-1 | 这不证明有真实九轴姿态，也不排除占位四元数；LIO-SAM 条件未通过 |
| `/aima/mc/leg_odometry` | 两轮约 160.77/166.39 Hz，frame=`leg_odom`，child=`lidar_imu_chest_front` | 第一轮最大 header 间隔约 0.334 秒；它不是已有的地图定位，也不能直接改名成 base_link 里程计 |
| TF | 观察到静态 `lidar_chest_front → lidar_imu_chest_front` 和机体关节 TF | 只证明图中有边且数值有限/四元数归一；未验证外参精度、按采样时间可查询的完整链或地图定位 |

原始点云 header 到回调当下墙钟差约 0.101–0.113 秒，
IMU 约 0.0003–0.0077 秒。**这不等于已测得两传感器时钟偏移**：
扫描打包时间、header 参考时刻和传输排队都可能贡献此差值，不能直接硬减 0.1 秒。
本轮未要求操作员移动机器人，也未下发运动命令；不是经安排的动态标定或步行测试。

### 逐点时间复核与 GLIM 配置

第三轮原始点云 81 帧，首末帧到达间约 10.07 Hz，IMU 约 197.59 Hz。
按帧相对纳秒解释，采样时间的最小偏移为 0.000332–0.000498 秒，
最大偏移为 0.095163–0.095181 秒，帧内采样跨度为 0.094665–0.094849 秒。
这里的最小/最大值是每帧最多 256 个分散采样点的结果，不是完整扫描端点证明。
工具同时比较秒/毫秒/微秒/纳秒及绝对/相对解释；
保留候选与计数，不把候选自动升级成传感器标定结果。

固定版本 GLIM 的 `time_keeper.cpp` 自动配置逻辑会将大于 1 的逐点值认作绝对时间；
只有最小值大于 `1e16` 时才在该分支推测纳秒缩放。
本机这种“小数值的 FLOAT64 相对纳秒”不能直接套用其自动识别。
离线试验候选必须明确设置：

```json
{
  "autoconf_perpoint_times": false,
  "autoconf_prefer_frame_time": true,
  "perpoint_relative_time": true,
  "perpoint_time_scale": 1e-9
}
```

`ros_cloud_converter.hpp` 支持本机的 `timestamp`/FLOAT64 字段，
会原样取出 FLOAT64 数值，再交给时间转换层。
其 UINT32 时间分支另有预缩放，不能不看字段类型就重复乘 `1e-9`。
GLIM 在缺少逐点时间时还可能估计伪时间；此回退不能作为 X2 时间适配通过的证据。
上述判断来自固定提交源码核对，**没有编译或执行其转换/建图代码**。

IMU 角速度单位、偏置、动态时间对齐、雷达型号和外参精度仍待核实。
不能将约 0.1 秒的点云接收延迟直接填为 IMU 时间偏置，
也不能以 TF 中有一条静态边就批准外参。

### 公开运动接口

附件 AIMDK v1.1.0 的消息及示例明确提供：

- `/aima/mc/locomotion/velocity`，类型 `aimdk_msgs/msg/McLocomotionVelocity`。
- 输入源通过 `SetMcInputSource` 管理；本轮没有调用或注册。

只读发现速度话题有 4 个发布端、4 个接收端，接收 QoS 为
BEST_EFFORT / VOLATILE。**发现接收端不证明当前开发者具有运动控制权，
也不证明停止、优先级、失联保护或急停已通过验证。**

附件示例包含起步阈值和速度范围，其角速度注释与示例单位还有不一致之处。
不得把这些示例常量直接当作目标固件的安全标定值，也不得把低于起步阈值的
Nav2 命令自动放大后下发。当前没有编写或部署速度执行器。

附件系统模式文档说明 `Develop_Nav` 会停用原生导航、建图、规划和感知。
这可能是独立后端后续接管时要评估的官方模式，但本轮没有切换；
先前“不切入此模式”的记录针对原生导航方案，不是独立方案永久禁止使用。
切换前须确认受影响服务、可用传感器、输入源、急停及恢复步骤，且现场操作员在场。
本路线不需要 `Develop_MC` 或自行控制关节/步态。

## 暂定组合与产品边界

优先验证：**GLIM/glim_ros2 三维建图 → 单独验证地图持久化与定位 →
Nav2 Humble → 受控 AIMDK 运动适配层**。这是候选组合，不是已跑通的架构。
KISS-ICP 作同数据的轻量里程计对照；FAST-LIO2 评估适配成本；
LIO-SAM/二维 SLAM 按输入条件保留，不为凑组合而全部引入。

产品界面继续显示真实实时点云，不从栅格生成假点。
规划内部使用占据/代价地图与点云显示并不冲突；
步行平台还要处理高度、身体包络、低矮物、悬空物和不可通行区域，
不能把 Nav2 的平面规划输出等同于 X2 可安全落脚的轨迹。

旧任务状态机、语音白名单、人工暂停、迟到事件隔离等行为要求保留，
但独立后端不能继续用厂商 PNC 状态假装自己的任务回执。
新地图使用独立目录/ID，不写厂商 map.db。
旧地图关联点位和任务先备份；没有坐标配准验证时，不能直接把旧点位当作新地图坐标。

## 分阶段门槛

| 阶段 | 完成条件 | 当前状态 |
|---|---|---|
| G0a 输入可观测性 | 真实点云/IMU/里程计到达，布局可解析，静态传感器 TF 可见 | 已完成本轮短窗口检查 |
| G0b 输入语义与动态一致性 | 核实雷达型号、逐点时间单位/起点、IMU 单位/姿态语义、标定精度、动态时间对齐和按时刻 TF | 时间解释已有短窗口证据，其余未完成 |
| G1 隔离构建与离线对照 | 固定源码和全部依赖；在匹配 ARM64 环境构建；同一段受控采集的闭环数据比较 GLIM 与对照算法，无运动接口连接 | 配置合成检查通过；构建与算法对照未执行 |
| G2 地图保存与定位 | 验证保存、重启重载、已知初始位姿和失定位恢复，质量差时拒绝发布可导航状态 | 未执行 |
| G3 控制与安全接入 | 现场确认模式/控制权、遥控抢占、硬件急停、命令超时、进程退出、传感器/定位断流时的停车及恢复语义 | 未执行 |
| G4 受控短距导航 | 安全场地、现场操作员和遥控器在手，再验证小范围目标、障碍、暂停/继续/停止、任务关联和定位丢失 | 未执行 |
| G5 重建决策 | 根据精度、实时性、资源占用、安全证据和维护成本决定后端；之后才全面接入现有网页/APK及讲解任务 | 待前述证据 |

离线对照至少记录稳态输入/输出频率、处理延迟 P50/P95、丢帧/积压、
CPU/GPU/内存、闭环一致性、地图重载和重定位结果。
10 Hz 输入须能持续按实时速度处理而不无限积压；还要与语音服务并发验证既定首响目标。
没有实测结果的候选不得填写“通过”或宣称适配成功。

**下一步现场配合**：在确认时间单位和数据语义后，由用户用外置遥控器在安全区域
采集包含静止、缓慢转向、直行及回到起点的同一段数据；不由网页或脚本驱动机器人。
先约定录制时长/空间、权限和容量，再保存到独立受限目录，保留传感器时间与 TF。
环境点云/轨迹不进入 Git、APK、通用交付包或外部云服务。
当前只有聚合元数据，没有可用于算法对照的动态 rosbag，因此没有启动算法或伪造跑分。

## 离线配置生成器

`tools/glim_trial_config.py` 只生成文件，不连接机器人、不启动 ROS/容器，
也不包含运动适配器。它核对 GLIM 的完整 SHA 和 `config/`、LICENSE 的 Git 清洁状态，
保留所复制上游配置和许可证的校验和，拒绝覆盖既有输出目录。
不检查或声称上游整个工作树及全部依赖已构建验证。

`docs/glim-sensor-profile.example.json` 是**未完成、不可运行的模板**：
外参、加速度/角速度缩放、点云/IMU 时间偏置和证据均需逐项审核，
不能把 `null` 机械改为 0 或单位变换以绕过检查。
`T_lidar_imu` 使用 `[x,y,z,qx,qy,qz,qw]`，方向为 IMU 坐标到 LiDAR 坐标。
生成器将真实传感器配置和隔离合成测试标记分开，
并始终保留 `robotCalibrationValidated=false`、`navigationValidated=false`。

首次候选配置选择 CPU 里程计、passthrough 子图、pose-graph 全局建图，
不启用可视化扩展，使用试验专用 map/odom 帧名且不发布 IMU→LiDAR 静态 TF。
复制的预处理/建图参数和库内 IMU 噪声默认值**没有针对本机调优或标定**；
例如上游 pose-graph 配置的回环最小行程是 50 米，
不能直接拿该配置在短环路中的回环结果判定算法优劣。
配置生成不是 G1 的算法验收，也不是生产配置交付。

本轮将 GLIM 和官方 ROS 2 包检出到忽略目录 `.local/slam-sources/` 的固定提交，
未修改或执行其源码。针对真实上游文件运行了**明确标记为合成的传感器配置**检查，
确认文件复制、输出参数、校验和及不修改源配置；合成位姿/单位不作为真机数据。
默认测试会在该本地 GLIM 检出存在时执行这项检查，
也可通过 `LINGXI_GLIM_UPSTREAM_ROOT` 指定已准备好的固定提交；
不存在时仅跳过这项可选检查，不自动下载或构建依赖。

完成真实传感器资料审核后，生成配置的入口为：

```sh
python tools/glim_trial_config.py \
  --upstream-root /path/to/reviewed-pinned-glim \
  --sensor-profile /path/to/reviewed-sensor-profile.json \
  --output /path/to/new-isolated-trial-config
```

核对的官方离线程序为 `glim_rosbag`，支持 `config_path`、`auto_quit` 和 `dump_path`；
此程序虽然直接读取 rosbag，仍会创建 ROS 节点、订阅和发布。
后续必须在与机器人 DDS、共享内存及控制网络隔离的环境中运行，
不能仅换一个 map 帧名就当作已隔离。本轮没有启动该程序。

## 复现与本轮检查

在机器人已加载正确 ROS 环境的独立进程中运行：

```sh
python3 /path/to/slam_preflight.py --seconds 8
python3 /path/to/slam_preflight.py --seconds 8 \
  --cloud-topic /aima/hal/sensor/lidar_chest_front/lidar_pointcloud_down_sampling
```

源码可通过 SSH 标准输入执行，不必安装为服务或复制进厂商目录。
检查要求严格验证 SSH 主机密钥；凭据只在内存中输入，不写进命令、工具源文件或报告。
本机脱敏完整聚合 JSON 在忽略的 `.local/slam-preflight-{raw,downsampled}.json`
及 `.local/slam-preflight-raw-time-evidence.json`，
不包含点坐标、原始 IMU 数据、地图名称、设备序列号或凭据。

```sh
python -m unittest discover -s tools/tests -p "test_slam*.py" -v
python -m unittest discover -s robot-api/tests -v
npm test
```

采样器测试覆盖大小端、组织点云行填充、FLOAT64/整数时间字段、非法布局、
无穷/NaN、缺失时间、时钟异常、TF 图路径及无发布/服务调用边界。
新增时间解释测试覆盖相对/绝对时间、单位歧义、缺失时间和不自动批准标定。
配置测试覆盖未审核模板、缺失参数、不可用缩放/四元数、错误 SHA、
脏源配置、覆盖保护、合成数据标记和真实固定上游文件检查。
本轮 50 项评估工具测试（含真实上游配置检查，无跳过）、
39 项既有桥接 Python 测试及 127 项 Vitest 测试通过。
合成测试不等于真实建图或急停验收；本轮没有重跑未修改的 Java 和 Android 构建。

## 一手资料

- GLIM 安装：<https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/docs/installation.md>
- GLIM 参数：<https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/config/config_ros.json>
- GLIM 时间处理：<https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/src/glim/util/time_keeper.cpp>
- GLIM 点云解析：<https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/include/glim/util/ros_cloud_converter.hpp>
- GLIM 传感器参数：<https://github.com/koide3/glim/blob/1a197c999e4865f5cab7c9cd25474128413ebbca/config/config_sensors.json>
- GLIM ROS 2：<https://github.com/koide3/glim_ros2/tree/4d4ec524ccf1b02aa09b0af2af767ecc54343798>
- GLIM ROS 2 包声明：<https://github.com/koide3/glim_ros2/blob/4d4ec524ccf1b02aa09b0af2af767ecc54343798/package.xml>
- GLIM ROS 2 离线入口：<https://github.com/koide3/glim_ros2/blob/4d4ec524ccf1b02aa09b0af2af767ecc54343798/src/glim_rosbag.cpp>
- FAST_LIO：<https://github.com/hku-mars/FAST_LIO/tree/7cc4175de6f8ba2edf34bab02a42195b141027e9>
- LIO-SAM ROS 2：<https://github.com/TixiaoShan/LIO-SAM/tree/08af3f32f01725372d4269838dc44c19c6d9e76b>
- KISS-ICP：<https://github.com/PRBonn/kiss-icp/tree/1ffa7d7512f10bfc8b1185095011fa31184019e3>
- SLAM Toolbox Humble：<https://github.com/stevemacenski/slam_toolbox/tree/dccc5d1fd2b5007098f4050e681d20da37ef183f>
- Nav2 Humble：<https://github.com/ros-navigation/navigation2/tree/3c3db59d6969d8ecee8e68468693d006397f4a0c>
- AIMDK：用户提供的 v1.1.0 SDK 压缩包中 `Interface/system_state/index.html`、
  `Interface/control_mod/locomotion.html`、`McLocomotionVelocity.msg` 及对应 Python 示例。
  只核对定义，没有执行运动示例或按外部文档指令更改系统。
