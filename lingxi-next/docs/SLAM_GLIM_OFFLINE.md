# GLIM 真实惯性里程计离线诊断

日期：2026-10-28。状态：**完成本机真实点云/IMU 的里程计诊断，
支持继续验证 GLIM；不是完整建图、标定、重定位、实机性能或导航验收。**
本轮没有连接机器人，没有更改模式、输入源、AIMDK 或已发出的 0.1.18/0.1.19 包。

## 输入语义的新证据

`tools/diagnose_slam_sensors.py` 使用现有 rosbags 隔离环境，
重新校验全部采集文件，再完整读取标准消息，不启动 ROS 或 DDS。
原始报告留在本机 `~/.cache/lingxi-next/sensor-diagnostic-20261028-a/`。

- 随附 SDK 的 `Interface/hal/sensor.html` 明确声明点云距离单位为米、
  `timestamp` 为相对时间，并要求融合时使用 IMU→LiDAR 的静态 TF。
  它没有在本轮核对段落中明确 header 表示扫描起点还是终点。
- SDK 的 `src/examples/src/hal/echo_imu_data.cpp` 标注角速度 rad/s、
  加速度 m/s²，且明确可订阅雷达 IMU。这是厂商示例的单位声明，
  不是当前固件已经完成物理标定的证明。
- 静止 6,880 条和行走 24,446 条 IMU 姿态**全部为单位四元数**，
  即便行走期间腿部姿态已大幅变化。不能把这个字段当作有效航向/姿态观测；
  covariance 首项为 0 也没有消除这一限制。
- 腿部里程计的 angular twist 全部为 0，不能直接拿该字段作角速度真值。
  诊断改用同一 IMU 子坐标系的相邻姿态、约 0.1 秒有限旋转和 IMU 中点插值，
  不外推、不拟合时间偏移，也不将腿部里程计视为真值。
- 行走段去均值向量相关约 0.947；rad/s 假设的向量 RMSE 约 0.148，
  deg/s 缩放假设约 0.372。它支持按官方 SI 声明试验，不是校准合格证明。
  静止段缺少旋转激励，不因其较小 deg/s 残差而自动选择另一单位。
- 静止角速度均值约 `[0.07153, -0.00340, -0.00879]` 原始单位，
  显示明显的待核查常量分量。适配器没有自行减去这组均值；
  使用 GLIM 自身的 ROBUST 初始化和偏置估计。
- 两段数据各观察到一条数值一致的
  `lidar_chest_front → lidar_imu_chest_front` 静态边。
  按 TF 语义，其变换把 IMU 坐标变到 LiDAR 坐标，符合 GLIM 的
  `T_lidar_imu` 方向；精度没有得到外部标定验证。

归档和上述两个 SDK 成员的实际 SHA-256，以及诊断的白名单聚合，
见 `slam-glim-offline-evidence.json`。不依据字段名、四元数模长、
静态 TF 存在或相关性就批准标定。

## 试验实现与假设

`tools/glim-replay/offline.cpp` 提供仅供本机单进程单轮使用的 C ABI；
`tools/run_glim_offline_trial.py` 从已验证 rosbag 读取真实点云和 IMU，
经 ctypes 调用先前固定源码构建的 GLIM CPU 核心。
适配库实际配置、编译、链接成功，三套上游源码没有修改。
这不是 ROS/Humble 封装，也不是目标 ARM64 程序。

回放使用本机 WSL Ubuntu 24.04 / x86_64、Python 3.12.3、
rosbags 0.11.9、NumPy 2.5.4；固定的 GTSAM/gtsam_points/GLIM
提交与核心构建证据沿用 `SLAM_CPU_BUILD.md`。
全部轮次顺序运行，nice 10、线程上限 2，不与语音负载作并发验收。

明确审核的私有诊断 profile 采用厂商声明的 SI 缩放、实际观测的非单位外参，
并将“没有额外时间校正”作为试验条件，而不是测得偏移为零。
它显式标注 `timingHypothesesOnlyNotCalibration=true`，
不修改仍未完成的 `glim-sensor-profile.example.json`，不自动批准生产配置。
两种时间假设均在行走回放前列为试验范围：

1. `header_is_scan_start`：扫描基准采用 header，逐点相对纳秒转换为秒。
2. `header_is_scan_end`：扫描基准采用 header 减去本帧最大逐点偏移，
   再加各点偏移。它是候选解释，不是自动测时钟或从接收延迟倒推校正量。

每帧送入前，IMU 必须覆盖从假设扫描基准到末点的时间段，
并至少插入一条不早于末点的 IMU。开头约一秒的缓存点云早于 IMU 记录：
静止跳过 10 / 11 帧，行走跳过 11 / 12 帧；不补造历史 IMU。
之后 ROBUST 初始化还需约三秒，初始化未给出位姿时记录为空，
不使用单位位姿冒充估计成功。每一条原始扫描都有处理或跳过计数。

点云范围、采样和 GICP 等参数使用复制的上游 CPU 默认配置，
未按本轮结果寻优。预处理只传 XYZ 与真实逐点时间，
未使用 IMU 姿态、腿里程计、强度或 ring 作融合观测。
预处理执行上游下采样、排序与邻域计算，再运行惯性里程计；
IMU 噪声参数仍为上游默认值，未按本机标定。

只实例化 `CloudPreprocessor` 和 `OdometryEstimationBase` 的 CPU 模块；
**没有实例化子图/全局建图或回环模块**，生成配置中的映射开关不代表执行了建图。
记录的是在线 LiDAR 位姿，不是最终平滑轨迹或回环优化地图。
读完后调用 `get_remaining_frames()` 收尾，但不拿返回数量冒充地图保存成功。

## 实际结果

全部五轮报告、运行器来源快照、私有轨迹和原生日志均保留。
初始静止轮 `a` 之后只补充了运行来源检查、环境/库哈希及时间戳记录，
同配置复跑静止 `b`；四轮最终运行器的 SHA-256 相同。
静止复跑的小幅数值差异保留，不声明多线程随机采样链路逐位确定。

下表位移为估计值/原始坐标单位。虽已找到厂商“米”的声明，
仍没有外部尺寸或轨迹标定，不能把首末距离写成“闭环精度误差”。
耗时为本机原生调用阶段，不含点云解码及所有 Python 调度开销。

| 轮次 | 处理 / 跳过 / 初始化 / 位姿 | 里程计 P95 ms | 累计步长 | 首末距离 | 墙钟秒 |
|---|---|---:|---:|---:|---:|
| 静止起点假设，初始 a | 343 / 10 / 30 / 313 | 15.74 | 0.13021 | 0.02301 | 8.999 |
| 静止起点假设，复跑 b | 343 / 10 / 30 / 313 | 16.47 | 0.12894 | 0.02318 | 9.234 |
| 静止终点假设 | 342 / 11 / 31 / 311 | 16.24 | 0.14643 | 0.02141 | 9.188 |
| 行走起点假设 | 1221 / 11 / 31 / 1190 | 16.74 | 8.12167 | 0.66387 | 34.029 |
| 行走终点假设 | 1220 / 12 / 31 / 1189 | 18.71 | 9.78353 | 0.67353 | 35.965 |

两种行走假设的预处理 P95 分别约 4.69 / 4.62 ms；
预处理加里程计的逐帧最大值约 40.95 / 45.43 ms，
不能把两个阶段的 P95 相加当成总 P95。
峰值进程常驻内存约 238.8 / 240.9 MiB。
这些只说明本机离线 CPU 试验有继续验证价值，
**不保证机器人 10 Hz 实时性或语音并发首响目标**。

此前 KISS-ICP 行走累计步长约 66.21 / 30.70、首末约 5.10 / 4.85；
GLIM 的统计更接近腿里程计的约 9.38 / 0.705，但后者也不是外部真值。
而且 KISS、腿里程计与 GLIM 的统计覆盖区间、初始化和处理阶段不同，
**此表不是公共有效时段对齐后的精度/速度排名**。
GLIM 静止首末距离也没有优于 KISS 的约 0.00209，不能只选有利指标。
两种时间假设的累计步长相差约 1.66，说明时间语义仍然重要；
不能仅因一个数字更接近腿里程计就自动选定扫描终点假设。

结论是**支持继续验证 GLIM 惯性路线，暂不批准交付导航后端**。
仍需核实雷达/驱动版本和扫描基准、同步偏差、外参精度，
完成共同有效时段对照、地图几何检查、回环/保存重载与失定位恢复。
ARM64 目标加载、Humble 封装、语音并发、控制权/急停和实机导航仍未通过。

## 隐私、复现与检查

原始数据、位姿数组、实际外参 profile 和可能包含初始化位姿的日志只留本机私有目录；
目录 700，运行生成的报告/数组/日志/来源快照 600。
公开文件由 `tools/export_glim_trial_evidence.py` 从五份完成报告白名单导出，
核对采集清单、源码快照、适配库、轨迹、日志和 SDK 来源哈希，
拒绝覆盖已有证据，不包含点坐标、轨迹序列、实际外参或凭据。
归档路径均在 `~/.cache/lingxi-next/`，不进入 Git、APK、通用包或外部云服务。

构建入口（先准备已有固定核心构建环境和隔离 Python）：

```sh
python tools/run_glim_offline_trial.py build \
  --work "$CACHE/slam-cpu-20261028-b" \
  --output "$CACHE/new-glim-adapter"
```

回放必须使用该核心构建的显式库路径：

```sh
LD_LIBRARY_PATH="$CORE/prefix/lib:$CORE/sysroot/usr/lib/x86_64-linux-gnu" \
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
python tools/run_glim_offline_trial.py run \
  --manifest docs/slam-capture-evidence.json \
  --data-root "$CACHE/slam-datasets-20261028-a" \
  --diagnostic "$CACHE/sensor-diagnostic-20261028-a/report.json" \
  --profile /private/reviewed-diagnostic-profile.json \
  --adapter "$CACHE/new-glim-adapter" --work "$CORE" \
  --upstream "$CACHE/slam-sources-20261028/glim" \
  --output "$CACHE/new-glim-diagnostic" \
  --dataset dynamic --hypothesis header_is_scan_start
```

其中 profile 必须经逐项审核；不能用 `null`→0 或单位外参替换来通过检查。
输出必须为新目录。未验证的真实传感器模板仍会被配置生成器拒绝。

本轮适配工具测试：隔离 Linux 134 项通过，无跳过；
Windows 109 项通过，25 项 Linux/NumPy 专用测试明确跳过。
包含整数纳秒精度、四元数乘法方向/符号、缺失覆盖、两种假设保留、
初始化不伪造位姿、原生错误传播、SDK 声明检查及无控制接口边界。
真实回放与合成单测分别记录；没有补写前端、Java、Android 或实机验收通过记录。
