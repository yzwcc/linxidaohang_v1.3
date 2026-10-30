# 保存地图定位运行链路

日期：2026-10-30。最初阶段机器人断电，只在电脑端使用保留的录制数据；
随后恢复网络并完成下文的 ARM64 录制定位与 HTTPS 检查。
新增入口为 `robot-runtime/python/controlled_localization.py`；
私有控制和地图加载位于 `localization_control.py`。
这不是导航启动器，也没有更新生产安装包或 Android APK。

## 已实现

运行入口复用既有 `MappingPlatform`、GLIM 纠正点扩展、传感器工作线程和
`ProcessSupervisor`，将保存地图与定位会话组合到同一个受监督子进程中。
small-gicp 固定为 1.0.1，原生配准器由点云工作线程创建、调用和释放，
控制 socket 线程只查询缓存或提交有界请求。
没有复制旧版业务代码，也没有修改冻结的 GLIM 原生适配器。

调用方必须明确提供地图 ID、收据和当前传感器配置的 SHA256。
加载器核对收据字段、帧和子图计数、原生地图文件清单、点云大小及摘要，
拒绝符号链接、重复 JSON 字段、非有限点和超限输入，再创建自有不可写点云副本。
地图树最多 4,096 项、64 MiB，点数另受配准配置限制；
这些是当前诊断入口的容量限制，不是完整场馆地图的产品容量承诺。
结束前再次核对配置、源码、库和地图输入；父进程检查完整回调记账、
输入绑定及定位清理结果，异常或强制终止不能报告成功。

旧地图收据尚未证明建图时使用了当前传感器配置。
本入口只绑定并验证当前提供的配置文件，始终保留
`mapProfileAssociationVerified=false`，不会据此推断标定已经确认。

## 控制与失效

子进程在输出目录创建 `localization.sock`。
目录权限必须为 0700，socket 为 0600，仅接受同 UID 的 Linux 本地连接，
每次请求有大小和读取时间限制；不监听 TCP，不暴露 HTTP。
日志、状态矩阵和完整报告留在私有输出目录，不上传 GitHub。

请求使用 `SLAM_LOCALIZATION_SESSION.md` 中的 `dispatch()` 约定：
查询状态、带当前参考帧设置明确初值、暂停、停止。
写请求同时核对地图/配置/输入身份及 `expectedGeneration`；
没有自动初值、全局搜索或暂停后自动恢复。
暂停和停止立即撤销可见位姿，旧请求和迟到结果不能重新激活跟踪。
接受初值只表示进入请求状态，工作线程确认并连续通过三次配准后才进入诊断跟踪。

本次组合在每次 IMU/frame 原生操作前调用 `tick()`，
收到纠正点时调用 `observe()`；没有为已有 worker 增加空闲 tick 钩子。
状态查询也会独立清空过期位姿，因此原生调用阻塞时不会继续返回过期位置。
硬阻塞的清理由已有进程监督器处理。

位姿仍标记为 `native-raw`、源坐标为 `IMU`，
不是已标定的米制底盘坐标，不发布导航 TF。
所有状态保持 `localizationReady=false`、`navigationReady=false`。

## 离线复现

当前入口支持普通用户 Linux x86_64 或匹配的 ARM64/Humble、录制输入和
已验证的纠正点扩展。ARM64 要求 Python 3.10，使用安装好的 Humble 解码器；
架构不匹配和 root 被拒绝；这个直接录制入口仍拒绝实时输入。
统一启动器后来增加了显式 ARM64 实时模式，见本文末尾。
配置沿用现有录制建图的私有 JSON，要求包含 `pointAdapter`。
还需要既有冻结原生依赖、numpy/scipy、small-gicp 1.0.1，以及匹配平台的
解码器（x64 使用 rosbags，ARM64 使用已安装的 Humble）；
本入口不下载安装依赖，也不更改库搜索路径。

在隔离 Linux 环境预先设置与 `child_environment(work)` 完全一致的
`LD_LIBRARY_PATH`，即原生工作目录下的 `prefix/lib` 与
`sysroot/usr/lib/x86_64-linux-gnu`，并设置干净的 `LINGXI_GLIM_UPSTREAM_ROOT`。
使用新的绝对输出路径，不能覆盖输入或复用旧结果：

```sh
python -B robot-runtime/python/controlled_localization.py run \
  --config /private/config.json \
  --saved-map /private/saved-map \
  --map-id "slam-REPLACE_WITH_32_HEX_SESSION" \
  --receipt-sha256 "REPLACE_WITH_ACTUAL_RECEIPT_SHA256" \
  --profile-sha256 "REPLACE_WITH_ACTUAL_PROFILE_SHA256" \
  --output /private/new-output \
  --seconds 90
```

地图 ID 实际格式为 `slam-` 后跟收据中的 32 位十六进制 session。
运行期限必须为 5–120 秒。成功退出仅表示录制输入完整处理、原生和定位资源正常关闭，
即使没有提交初值也不能解释为已成功定位。

`tools/check_localization_runtime.py` 使用相同参数，另需
`--seed-sample` 和 `--seed-sha256`，无需 `--seconds`。
该检查器启动真实入口，通过私有 socket 提交录制样本中的明确初值，
依次检查跟踪、暂停、旧代次拒绝、再次明确设置初值、跟踪和停止，
之后等待整段录制处理完成，并持续确认停止后位姿不恢复。
第一参考帧若错过即失败，不隐藏重试或自动重新播种。

初值样本为保留的 `private-first-points.npz`；
第二次初值仍使用同一录制位姿并明确关联当时参考帧。
这是诊断控制，不是独立测量的真实位置。

## 验证范围

聚焦测试：

```sh
python -B -m unittest discover -s tools/tests -p test_localization_control.py -v
```

19 项覆盖地图实际字节与身份、输入变化、非法点/符号链接/重复字段、
真实 Unix socket、初值与代次冲突、线程所有权、初始化/关闭异常、
父进程结果拒绝及失败收尾。父进程负样本使用替身报告；
原生整段执行由独立检查器验证，结果见 `VALIDATION.md`。

最终源码的一次真实原生检查完整处理 4,144 条回调、196 次扫描，
生成 165 个里程计位姿；原生实例创建/关闭各一次。
两次明确初值请求分别在观察到第 6、11 帧时已经进入跟踪，
随后确认暂停、旧代次拒绝和停止；停止后 2,887 次查询均未恢复位姿。
子进程自然退出，未强制终止，目录和报告权限分别为 0700/0600，
检查后未发现该入口的残留进程。摘要和来源散列见
`slam-localization-runtime-evidence.json`；该 JSON 是从私有检查报告选取的聚合，
不是独立精度审计。

第一次检查缺少既有原生库路径，启动即被拒绝；补齐显式环境后通过一次整链路检查。
补充父进程校验后又以最终源码完成上述检查，未放宽定位质量门槛。
新增父进程负样本曾因测试夹具写错层级失败，修正夹具后 19 项全部通过。

保留的短段输入不能等同于此前约 5 米往返采集的动态验收：
物理运动未独立确认，地图可能复用同段数据，初值来自原记录。
没有位置真值、长程闭环、重复场景全局重定位、底座外参、
ARM64 性能或导航控制结果。
随后已接入配对管理 API 与网页，见 `SLAM_LOCALIZATION_CONSOLE.md`。
下述统一启动器将同一录制原生会话与配对 HTTPS API 交给一个父进程管理；
下述最初统一启动检查为 x64；ARM64 录制适配结果见本文末尾，实时定位仍未接入。

## 统一启动与退出

新增 `robot-runtime/python/managed_localization.py`，复用上述录制子进程；
`localization_owner.py` 先等原生健康状态和同 session 私有控制 socket 就绪，
再启动已核对 SHA256 的 Node 与定位 API。父进程持有两端输入管道；
API 或原生端退出、启动超时、输入变化和处理期限结束均撤销 HTTP 接入并清理子进程。
父进程被强杀时，子进程通过各自管道 EOF 独立退出。

环境只转交明确允许的字段，不继承 `NODE_OPTIONS`、`LD_PRELOAD`、
演示模式或其他后端覆盖。TLS 文件必须是直接绝对路径，大小为 1 字节至 1 MiB；
私钥须当前用户所有、禁止组和其他用户访问。配对摘要必须为 64 位小写十六进制。
配置、源码、Node/API、地图和 TLS 字节在结束时重新核对。
输出目录不能覆盖地图目录、录制输入、运行时或 TLS 文件。

先在私有环境设置 `LINGXI_TLS_KEY`、`LINGXI_TLS_CERT`、
`LINGXI_PAIRING_CODE_SHA256`，并保留前述原生库环境。
可选 `LINGXI_WEB_ROOT` 指向编译网页；绑定地址和端口沿用控制台文档。
这些值不写入交付包或 Git，启动器不生成配对码，也不安装服务。

```sh
python -B robot-runtime/python/managed_localization.py \
  --config /private/config.json \
  --saved-map /private/saved-map \
  --map-id "slam-REPLACE_WITH_32_HEX_SESSION" \
  --receipt-sha256 "REPLACE_WITH_ACTUAL_RECEIPT_SHA256" \
  --profile-sha256 "REPLACE_WITH_ACTUAL_PROFILE_SHA256" \
  --node /private/node \
  --node-sha256 "REPLACE_WITH_ACTUAL_NODE_SHA256" \
  --api-bundle /private/localization-main.mjs \
  --api-sha256 "REPLACE_WITH_ACTUAL_API_SHA256" \
  --output /private/new-run \
  --seconds 90
```

输出及其 `native/` 目录为 0700，日志和 `owner-report.json` 为 0600；
Unix socket 完整路径须小于 100 字节。使用新的持久目录保留证据，
不要依赖可能随 WSL 退出而清空的 `/tmp`。
启动状态只输出实际监听端口，不输出配对码或位姿。
原生启动、API 启动和录制处理分别有期限；`--seconds` 为 API 就绪后的处理期限。

结果仅在完整处理全部回调、输入复核和子报告校验通过、两个子进程干净退出时为
`completed`。`failurePhase` 保留首个失败阶段。SIGINT/SIGTERM 请求结束，
不把部分录制当作成功：现有原生回放若因提前停止返回失败，整体仍为 `failed`；
只有原生正常收尾的中断才为 `interrupted`。退出码分别为 0、1、2。

`tools/check_managed_localization_runtime.py` 复用同组参数，另需
`--seed-sample` / `--seed-sha256`，不传 `--seconds`。
它以一次性私有证书和随机配对码检查未认证拒绝、HTTPS 配对、明确初值、
实际跟踪、暂停、旧代次拒绝、停止及完整回放。
证书通过明确 CA 校验，没有关闭证书验证。初值取自保留样本，
明确绑定提交时的当前参考帧，不表示二者位置相同，也不是位置真值。

最终源码的一次持久输出检查完整处理 4,144 条回调、196 次扫描、165 个里程计位姿，
观察到第 4 帧跟踪；停止后 592 次 HTTPS 查询未恢复位姿。
原生创建/关闭各一次，API 与原生均退出 0、无强制终止。
13 项 Linux 进程测试和 87 项定位相关 Python 回归通过；
摘要见 `slam-managed-localization-evidence.json`。
这些结果没有扩大地图精度、坐标标定或导航就绪范围。

## ARM64 录制定位验证

同日新增匹配平台的录制定位绑定，共用 `MappingPlatform.profile`，
复用原有 ARM64 GLIM/纠正点库、Node 和录制输入；没有重编原生 SLAM。
`tools/run_localization_arm64_trial.py` 在核对 SSH 主机身份后上传到新的私有目录，
逐文件核对源码和输入，建立使用系统科学计算包的隔离 Python 环境，
只解包固定 SHA256 的 small-gicp 1.0.1 wheel，不安装或修改系统依赖。
Humble 仅用来解码既有录制，不订阅实时传感器。

首次准备遗漏冻结适配器的 C++/CMake 文件，被原有校验在定位启动前拒绝；
上传清单改为复用既有冻结源清单，补回归测试后另建目录运行成功。
准备报告单独保存并核验字节，避免 SciPy 警告混入命令输出后影响 JSON 读取。
失败目录和原报告保留，不计为通过样本。

| 检查 | ARM64 实际结果 |
| --- | --- |
| 完整录制 | 4,144 条回调、196 次扫描、165 个里程计位姿，原生 create/close 各 1 次，单一非主工作线程 |
| 配对 HTTPS | 回环地址、显式 CA 校验；未认证 401、配对 201、明确初值 202，第 4 帧观察到跟踪 |
| 撤销与代次 | 暂停和停止撤销位姿，旧代次 409；停止后 554 次查询未恢复位姿 |
| 正常退出 | API/原生均退出 0，无强杀；进程、组和监控线程已清理，输入结束时重新核验 |
| 兼容回归 | 相同最终源码的 x64 原生 HTTPS 检查通过，同样完整处理回调；停止后 603 次查询未恢复位姿 |
| 自动回归 | 94/94 定位 Python 测试通过，含 4 项 ARM64 协调器输入检查；未重跑历史全量、网页、Java 或 Android |

运行版本为 Python 3.10.12、NumPy 1.26.1、SciPy 1.8.0、small-gicp 1.0.1。
系统 SciPy 报 NumPy 版本范围警告，本轮执行成功不代表生产依赖组合已验收。
地图复制自保留的 x64 保存结果，本轮没有进行 ARM64 原生地图重载。
初值为录制位姿，关联明确当前参考帧，不是位置真值。
库清单含别名及未必逐项映射的核心库，不能把整份清单称为全部实际加载库。

脱敏摘要见 `slam-managed-localization-arm64-evidence.json`。
通过以下命令重新核验保留报告并导出，不连接机器人、不重复执行原生定位：

```sh
python -B tools/export_localization_arm64_evidence.py \
  --trial /private/collected-arm64-trial \
  --compatibility /private/preserved-x64-check \
  --output /private/new-aggregate.json
```

报告中的内部 `robotContacted=false` 表示子进程只处理本地录制；
外围收集器实际通过 SSH 连接机器人，公开摘要正确记录为 `true`。
另外 6 项保留报告测试通过，覆盖公开摘要脱敏、报告字节变化、进程残留、
地图绑定不一致、源码变化和兼容检查使用不同初值；这些测试不重跑原生 SLAM。
设置 `LINGXI_LOCALIZATION_EVIDENCE_TRIAL` 和
`LINGXI_LOCALIZATION_EVIDENCE_COMPATIBILITY` 指向私有报告目录后，
可运行 `python -B -m unittest discover -s tools/tests -p test_localization_evidence.py -v`。
本轮 HTTPS 检查使用机器人本地回环，没有验证局域网浏览器或 APK 访问。
实时定位、地图/配置关联、底盘标定、独立动态精度和导航仍待完成，
三个就绪/关联标记继续为 false；版本及已交付安装包/APK 保持 0.1.19。

## ARM64 实时输入验证

同日随后已完成实时雷达/IMU 到隔离配对 HTTPS 的有界检查：
完成 3,365 条消息、160 次扫描、129 个里程计位姿，观察到 5 个新参考帧。
暂停、停止、旧代次拒绝及停止后 222 次查询正常；原生和 API 均干净退出。
未提交初值，未建立保存地图跟踪；不构成定位精度、底盘标定或导航验收。
实现、复现方式及 IMU 预测警告见
[实时运行链路](SLAM_LIVE_LOCALIZATION.md) 和
[脱敏实机摘要](slam-live-localization-arm64-evidence.json)。
