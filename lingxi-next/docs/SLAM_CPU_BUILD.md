# 开源 SLAM CPU 核心构建验证

本页范围是无机器人连接的本机编译与合成时间转换检查。
**不是 ARM64/Humble 适配通过、真实建图、重定位或导航验收。**
完整选型与后续门槛见 `OPEN_SOURCE_NAV_EVALUATION.md`。

## 固定源码

| 仓库 | 参考版本 | 实际提交 |
|---|---|---|
| borglab/gtsam | 4.3a0 | `3ad4b4c3cb28394c9597f48fa02dad361c8450e3` |
| koide3/gtsam_points | v1.2.2 | `9d32e7dbecf6015560d84b4901d6b0a6f483ec46` |
| koide3/glim | 既有评估基线 | `1a197c999e4865f5cab7c9cd25474128413ebbca` |

gtsam_points 的 `v1.2.2` 是附注标签；标签对象
`16a1ccbd10ca75cb4b7afd3b9502e69972e2f9ef` 不等于提交 ID。
构建器检查的是上表的实际提交，并要求源码工作树干净。
GTSAM 的 `LICENSE.BSD` 有三条分发条件，且根 LICENSE 另列内嵌依赖；
gtsam_points 根 LICENSE 为 MIT。此记录不代替全部传递依赖的许可核对。

## 本机构建方式

`tools/build_slam_cpu_trial.py` 只在 Linux 运行：

- 从现有缓存读取源码和已下载 `.deb`，不自行下载或运行软件源脚本。
- 核对源码提交/清洁状态，检查包架构与本机一致，记录包版本和 SHA-256。
- 拒绝同一缓存内同名包的多个版本，避免按文件名排序解包导致隐式覆盖。
- 用 `dpkg-deb --extract` 解包到独立 `sysroot`，不执行系统包安装。
- 按 GTSAM → gtsam_points → GLIM 构建，安装前缀始终在新验证目录中。
- GTSAM unstable 开启；CUDA、可视化、OpenCV 和 `-march=native` 关闭。
- C++ 使用 `-O1`，并发限制为 1–4，默认 1；此构建不用于性能跑分。
- 不启动 ROS，不带运动适配器，不调用机器人接口。
- 保留每阶段日志和 `build-report.json`；已有验证目录一律拒绝覆盖。

先在目标 Linux 环境准备软件包缓存。下面命令**必须保留 `--download-only`**：

```sh
mkdir -p "$PACKAGE_CACHE/partial"
apt-get --download-only --yes --no-install-recommends \
  -o Debug::NoLocking=1 -o "Dir::Cache::archives=$PACKAGE_CACHE" install \
  cmake ninja-build libeigen3-dev libboost-serialization-dev \
  libboost-system-dev libboost-filesystem-dev libboost-thread-dev \
  libboost-date-time-dev libboost-timer-dev libboost-chrono-dev \
  libboost-program-options-dev libboost-regex-dev libboost-graph-dev \
  libspdlog-dev libmetis-dev
```

此步骤使用 APT 的已认证软件源索引，不关闭签名或 TLS 检查。
软件包缓存随环境解析，未冻结所有操作系统依赖；构建报告中的版本/校验和
是该次观测记录，不是已可分发的完整依赖锁。
需检查下载结果；不能把 APT 输出的“将新增包”当作已经安装。

在原生 Linux 文件系统中准备三个固定提交的独立检出。
WSL 的 `/mnt/c`、`/mnt/e` 源码目录会让大量头文件读取受挂载盘 I/O 影响；
本轮实际观察到 `p9_client_rpc` 等待，改用 Linux 用户缓存目录的同提交检出。
首次低效构建主动中断，日志保留，不作为算法编译不兼容的结论。

```sh
python3 tools/build_slam_cpu_trial.py \
  --source-cache "$SOURCE_CACHE" \
  --package-cache "$PACKAGE_CACHE" \
  --work-root "$HOME/.cache/lingxi-next/new-cpu-trial" \
  --jobs 2
```

`SOURCE_CACHE` 下应有 `gtsam/`、`gtsam_points/`、`glim/`。
普通用户运行，不需要 sudo；不能将编译出来的 x86_64 库拷到机器人。
新目录的 `logs/` 与 `build-report.json` 用于检查真实阶段结果。

## 合成时间转换检查

`tools/glim-smoke/synthetic-profile.json` 中的单位缩放、零偏移和单位外参
**都是测试数据，不是 X2 的标定结果**。
它与 Python 配置测试共用，明确标记为 `synthetic_fixture`。
生成出的清单始终将机器人标定与导航验收置为 false。

核心构建成功后，在同一 Linux 环境执行：

```sh
python3 tools/run_glim_cpu_smoke.py \
  --work-root "$HOME/.cache/lingxi-next/new-cpu-trial" \
  --upstream-root "$SOURCE_CACHE/glim"
```

运行器先核对成功的核心构建报告、源码基线和主机架构；
再生成合成配置、配置/构建 C++ 检查并运行 CTest，日志写入 `native-smoke/`。
已有检查结果拒绝覆盖；CTest 失败不会写成通过。
本地解包的 CMake/库依赖专门的 PATH/LD_LIBRARY_PATH，运行器自行设置；
不设置库路径就直接执行其二进制，
可能报缺少 `librhash.so.0` 等库，不应误判为 SLAM 编译失败。
运行器通过 Python 参数数组调用工具，避免 PowerShell/WSL 嵌套命令中的
分号列表或 `-D...=/path` 路径被截断。CMake 入口还要求存在合成配置清单，
拒绝误指向普通目录或真实机器人配置。

C++ 检查链接实际 GLIM 库，并用 `RTLD_NOW` 加载实际的 CPU 里程计、
passthrough 子图和 pose-graph 全局建图模块，核对动态链接是否可用；
不调用模块的算法实例工厂。输入合成帧时间 1000 秒和
332000 / 95181000 纳秒的逐点相对时间，要求帧时间保持不变，
逐点时间为 0.000332 / 0.095181 秒。程序输出明确的合成数据标记。
它不估计轨迹、不处理真实传感器、不构建地图，不提供导航质量证据。

## 结果记录

2026-10-28，在 WSL Ubuntu 24.04.4 / x86_64、GCC 13.3.0、
CMake 3.28.3、Eigen 3.4.0、Boost 1.83.0 下实际完成：

| 项目 | 配置 | 编译 | 独立前缀安装 |
|---|---|---|---|
| GTSAM + unstable | 通过 | 通过 | 通过 |
| gtsam_points CPU | 通过 | 通过 | 通过 |
| GLIM CPU 核心/模块 | 通过 | 通过 | 通过 |

C++ 合成检查的配置、构建、CTest 均通过，加载 3 个模块，实际输出：

```json
{
  "inputClassification": "synthetic_fixture",
  "nativeModulesLoaded": 3,
  "robotCalibrationValidated": false,
  "navigationValidated": false,
  "frameSeconds": 1000,
  "firstSeconds": 0.000332,
  "lastSeconds": 0.095181
}
```

构建时 55 个 `.deb` 仅下载/解包；后续 dpkg-query 再确认
CMake、Ninja、Eigen、Boost serialization 和 spdlog 没有安装到 WSL 系统。
构建/检查进程均已结束，源码前后均通过清洁状态检查。
本地原始构建报告在 WSL 用户缓存
`~/.cache/lingxi-next/slam-cpu-20261028-b/build-report.json`，
合成报告和详细日志在其 `native-smoke/`；首次主动中断的 `-a` 目录仍保留。

**本页未涵盖**：ARM64/Humble 验收、真实 rosbag 回放、
传感器标定/动态同步、实时性、地图保存重载/重定位、运动安全及自主导航。
没有安装或替换机器人软件，没有运行真实 SLAM 轨迹估计。
本机构建只是降低依赖组合与时间转换配置的不确定性。
其后完成的 Jammy ARM64 核心交叉构建单独记录于 `SLAM_ARM64_BUILD.md`；
它没有执行目标程序，也未构建 ROS 2 封装。
脱敏的阶段结果、源码提交和实际二进制/日志校验和记录在
`slam-cpu-build-evidence.json`。它只记录本次构建，不保证换路径或工具链重编后
具有相同二进制校验和，也不代替目标架构的验收。

## 一手资料

- <https://github.com/borglab/gtsam/tree/3ad4b4c3cb28394c9597f48fa02dad361c8450e3>
- <https://github.com/koide3/gtsam_points/tree/9d32e7dbecf6015560d84b4901d6b0a6f483ec46>
- <https://github.com/koide3/glim/tree/1a197c999e4865f5cab7c9cd25474128413ebbca>
