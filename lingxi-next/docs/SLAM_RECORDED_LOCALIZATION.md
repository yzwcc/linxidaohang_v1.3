# 完整录制序列的有初值定位跟踪

日期：2026-10-30。机器人断电，本轮全部在电脑隔离 Linux 环境执行。
本次将上一阶段的局部配准与跟踪状态机接入已有独立进程监督、
原生 GLIM 和纠正点输出，处理保留的完整回调序列。
源码入口为 `tools/run_recorded_localization_trial.py`，
脱敏聚合为 `slam-recorded-localization-evidence.json`。

## 实现范围

复用既有录制输入、接收节奏、传感器工作线程、原生适配器、点云扩展、
健康协议和进程停止流程，不修改冻结组件或放宽定位质量门槛。
配准器与跟踪器均在原生工作线程创建、使用和停止。
健康通道仅传输原有健康状态，不混入点云或定位结果。

三个控制使用相同的完整点云序列：原记录首帧位姿、首帧位姿的既定小幅扰动、
以及额外平移 `[100, 100, 100]` 原始单位的错误初值。
每组只在第一包显式设定一次初始位置，后续使用里程计预测和配准修正，
不会按帧重置初始位置，也不会在失定位后自动重新播种。

运行前后核对输入、配置、地图收据和文件清单；临时输入与源码先复制到私有
Linux 目录并核对摘要。运行中核对实际加载的 GLIM、点云扩展与 small-gicp
库；导出时重新核对报告、源码摘要、全包计数、跟踪状态和原生清理结果。
保留原始输入与完整私有报告，仓库只包含聚合和摘要。

## 实际原生结果

| 项目 | 本次观察 |
| --- | --- |
| 平台 | WSL Linux x86_64；small-gicp 1.0.1 |
| 输入 | 全部 4,144 条回调：197 个点云、3,947 个 IMU |
| 原生处理 | 196 次扫描、165 个位姿、3,930 次 IMU 插入 |
| 纠正点包 | 164 包，共 1,246,242 点；每包 7,539–7,779 点 |
| 录制位姿初值 | 164 次配准全部通过；前 2 帧确认，第 3 帧起跟踪，共 162 帧 |
| 小幅扰动初值 | 同样 164 次通过、162 帧跟踪，中途未失定位 |
| 远处错误初值 | 第 1 帧拒绝，只调用配准 1 次；其后共 164 次观察均保持失定位 |
| 正样本配准耗时 P95 | 两组分别约 14.80 ms、14.36 ms |
| 最大工作队列年龄 | 约 89.55 ms |
| 停止结果 | 原生创建/关闭各 1 次，无拒绝或丢弃回调，无残留工作线程或子进程 |

耗时是本次电脑上的录制输入观测，不代表 ARM64 或现场实时性能。
配准耗时包括该配准函数内的预处理和质量检查，不是语音延迟、端到端定位
延迟或导航控制周期。三组控制按顺序在同一工作线程运行。

本次输入为此前保留的短段被动回调记录，采样期间的实际运动未被独立确认，
**不能把它称为用户早前约 5 米往返行走记录的持续定位验收**。
地图也可能复用同段输入，初始位置来自原记录；这会降低匹配难度。
因此这些结果只说明该完整序列上的有初值跟踪组合运行成功，
不证明独立位置精度、动态步行覆盖、全局重定位、失定位恢复、
底座坐标正确或长程可靠性。原始坐标尚未作为物理距离验收。
所有定位、导航和部署就绪标记继续为 false。

## 复现

使用已具备 NumPy、SciPy、small-gicp、rosbags 及既有地图工具依赖的
独立 Linux Python 环境。x64 定位试验依赖见
`robot-slam/requirements-localization-trial-x64-py312.txt`。
本工具只支持已核对的 x64 原生准备结果，不是 ARM64 启动入口。
参数指向既有私有准备结果；新输出须位于允许的忽略缓存内，且不能覆盖输入。

```sh
python -B -m unittest discover -s tools/tests -p test_slam_recorded_localization.py -v
python -B tools/run_recorded_localization_trial.py run \
  --work /private/cpu-build \
  --adapter /private/replay-build \
  --points /private/point-output-build \
  --bundle /private/matched-bundle \
  --reference /private/matched-reference \
  --saved-map /private/previous-saved-map \
  --point-reference /private/previous-point-trial \
  --hypothesis header_is_scan_start --rate 1.0 \
  --acknowledge-recording-reuse \
  --output "$HOME/.cache/lingxi-next/new-recorded-localization"
python -B tools/run_recorded_localization_trial.py export \
  --trial "$HOME/.cache/lingxi-next/new-recorded-localization" \
  --output /private/new-localization-aggregate.json
```

需要完整保留的地图收据、纠正点试验报告及首末帧样本，不能以任意点云或手工
伪造初始位置代替。现有输出一律保留；缺失、变化或来源不匹配即失败。
扫描起点只是沿用的诊断假设，本次不批准其时间标定。

11 项新增测试实际调用 small-gicp，并注入过期、重复、缺帧、时间倒退、
错误初值、质量拒绝、错误线程关闭和非法聚合。最终回归数量记录于
`VALIDATION.md`，不把平台跳过算作通过。

## 接下来的产品工作

后续离线优先接入地图身份、显式初始位置与跟踪状态的管理接口，
继续复用单会话和独立进程退出语义，再推进导航任务适配。
独立尺寸与位置对照、按时刻传感器到底座变换、长程闭环、重复场景拒绝、
ARM64 资源及现场移动验证仍需集中实机验收。
当前不更新生产安装包或 APK，也不启用机器人运动。
