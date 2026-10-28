# Jammy ARM64 CPU 交叉构建验证

范围：在本机 WSL 的普通用户目录生成面向 Ubuntu 22.04 / AArch64 的
GTSAM、gtsam_points、GLIM CPU 核心库及合成检查可执行文件。
**交叉编译和链接不等于在机器人执行通过，也不是 ROS 2 Humble 包或导航验收。**

## 构建边界

固定源码与 `SLAM_CPU_BUILD.md` 一致：

| 仓库 | 提交 |
|---|---|
| borglab/gtsam | `3ad4b4c3cb28394c9597f48fa02dad361c8450e3` |
| koide3/gtsam_points | `9d32e7dbecf6015560d84b4901d6b0a6f483ec46` |
| koide3/glim | `1a197c999e4865f5cab7c9cd25474128413ebbca` |

构建主机是 WSL Ubuntu 24.04 / x86_64，目标用户空间来自官方签名的 Jammy 源。
`prepare_slam_arm64_cache.py` 使用独立 APT 配置、软件源、空包状态、
索引、日志与下载目录；保留签名/TLS 检查，只下载，不执行系统安装或主机 APT 钩子。
不是给 WSL 安装 ARM64 包，也没有连接机器人安装依赖。

下载报告记录 35 个 amd64 交叉工具链包与 103 个 arm64 依赖包的
名称、版本和 SHA-256。构建前复核缓存内容与报告一致，拒绝重复包、架构错误、
脏源码、错误提交和覆盖既有结果目录。依赖按当次官方仓库解析，
**仍不是已冻结全部传递依赖的产品发布锁文件**。

实际使用 GCC 11.4.0、CMake 3.28.3，CPU `-O1`、并发 2、进程 nice 10；
禁用 CUDA、可视化、OpenCV 与 `-march=native`，启用 GTSAM unstable。
全部安装前缀和 sysroot 均在独立的新目录；不启动 ROS、不含运动适配器。

## 复现

要求普通用户、Linux x86_64、官方 Ubuntu archive keyring，
以及先前成功的本机 CPU 构建目录提供原生 CMake/Ninja 运行环境。
源码检出放在 Linux 本地文件系统，不放 `/mnt/e` 下编译大量头文件。

```sh
python3 tools/prepare_slam_arm64_cache.py \
  --output "$HOME/.cache/lingxi-next/new-jammy-cache"

python3 tools/build_slam_arm64_trial.py \
  --source-cache "$HOME/.cache/lingxi-next/slam-sources-20261028" \
  --package-cache "$HOME/.cache/lingxi-next/new-jammy-cache" \
  --native-work-root "$HOME/.cache/lingxi-next/slam-cpu-20261028-b" \
  --work-root "$HOME/.cache/lingxi-next/new-arm64-trial" \
  --jobs 2
```

上面的 `new-*` 必须是未存在的目录。命令通过 Python 参数数组调用 CMake，
不能手工拼接 PowerShell/WSL 参数后假定 `-D`、`-L` 或分号列表未被改写。

## 本轮配置问题与修复

前三次尝试均在真实库编译前的工具链配置阶段失败，日志和目录保留：

1. 解包的交叉 binutils 找不到 `libopcodes`。将所需 host 库显式加入独立搜索目录。
2. 将整个旧工具链 host 库目录放进 `LD_LIBRARY_PATH` 会遮蔽原生 CMake
   所需的新版 libstdc++。改为只链接所需的 libbfd/libopcodes/libctf。
3. 交叉 libc 的链接脚本包含绝对路径，单纯 sysroot 不足。
   对 EXE/SHARED/MODULE 显式提供目标 sysroot 的原生 arm64 库搜索目录，
   实际基本 C/C++ 编译及链接探测通过后才继续。

这些不是 GLIM 算法不兼容的结果，也没有通过修改上游源码绕开编译问题。
第四次使用修复后的工具链实际编译，完整结果见下节。

## 结果记录

本轮构建目录：
`~/.cache/lingxi-next/slam-arm64-20261028-d`。
详细日志在 `logs/`，完整依赖/阶段结果在 `cross-build-report.json`。
2026-10-28 实际构建进程退出 0，结果为
`arm64_cross_built_and_linked_not_executed`：

| 项目 | 配置 | 编译 | 独立前缀安装 |
|---|---|---|---|
| GTSAM + unstable | 通过 | 通过 | 通过 |
| gtsam_points CPU | 通过 | 通过 | 通过 |
| GLIM CPU 核心/模块 | 通过 | 通过 | 通过 |

合成检查程序的交叉配置/链接也通过；代表性的 7 个库和 1 个可执行文件
均为 ELF64/AArch64，最高观测到 GLIBC 2.34、GLIBCXX 3.4.30、
CXXABI 1.3.11 需求。**机器人现有 libstdc++ 是否满足 3.4.30
及全部依赖加载在该交叉构建阶段仍待单独验证**，不能仅凭 GCC 11.4.0
的编译器版本判断。该阶段没有执行目标程序或做 ARM64 模块动态加载测试。
后续真实机器人隔离加载及合成执行结果见 `SLAM_ARM64_TARGET.md`；
不修改本节历史交叉构建报告中的未执行标记。

检查器验证实际文件为 little-endian ELF64/AArch64，
读取 GLIBC、GLIBCXX 和 CXXABI 需求，拒绝高于 Jammy GLIBC 2.35 的产物。
合成时间检查只交叉链接，**不执行 ARM64 目标程序**。
产物机器类型/ABI 检查不等于依赖在目标机器人上能加载，
更不证明 Humble 封装、传感器标定、GPU、实时性或地图持久化可用。

构建/传输进程均已结束，固定上游源码前后均通过清洁状态检查。
再次查询确认交叉编译器、CMake、Ninja、Eigen、Boost serialization、
spdlog 没有安装到 WSL 系统。没有生成新机器人部署包或修改已有 APK。
脱敏阶段结果、8 项产物 SHA-256、完整报告校验和及验收边界见
`slam-arm64-build-evidence.json`；完整下载/构建报告保留在本机缓存，
不保证换工具链或路径重编后具有相同二进制校验和。
