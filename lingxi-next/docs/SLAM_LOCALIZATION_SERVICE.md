# 按需定位会话服务

日期：2026-10-30。该服务将“启动前固定一张地图”的原生定位入口，扩展为
按保存地图身份创建、停止及重新创建定位进程。它是后续网页地图选择的服务端
基础，**本轮尚未接入网页启动/关闭按钮、生产安装器或 APK**。
没有连接或部署机器人，没有发送运动指令。

## 生命周期

`robot-runtime/python/localization_service.py` 只管理一个原生进程槽位。
服务和各定位工作进程分别有独立生命周期：

- 服务 `runtime` 有自己的 `serviceId`、`generation` 和 `runId`，用于启动与关闭进程。
  服务重启会更换 `serviceId`；旧请求不能利用新服务从零开始的代次重新生效。
- 子进程的 `binding.session`、定位 `generation` 和参考帧继续用于初值、暂停和停止定位。
- `stop` 只终止当前定位会话，不代表工作进程已退出；`shutdown` 才请求退出工作进程。
- 启动只返回 `starting`。`running` 表示受监督工作进程已产生健康事件，
  不是定位成功、地图精度合格或可导航；所有就绪标志仍为 false。
- 新进程必须等上一进程的清理已确认且管理线程已结束后才能创建。
  线程结束但进程清理不确定时仍拒绝替换，不凭等待超时推断退出。
- 地图选择中取消不会启动子进程；关闭服务、父输入 EOF 或 SIGTERM/SIGINT
  会撤销请求接纳，停止并回收子进程。旧命令、旧查询的迟到响应不能进入新会话。
- 不自动重试控制命令、重启故障进程、提交初值或恢复定位。

工作进程复用现有 `controlled_localization.py child`、原生 GLIM、small-gicp、
点流、定位状态机与 `ProcessSupervisor`，没有另一套算法实现。
单次 live 输入仍受已验证配置里的 `maxSeconds` 限制；达到限制不自动续期，
需要检查当前终态后明确关闭/新建。本服务不改变现有配置和算法验收范围。

## 地图选择

调用者只提供 `mapId` 与实际收据字节的 `receiptSha256`，不能提供文件路径、
命令、环境变量、库地址或传感器配置。
目录由受信任启动参数给出，服务有界枚举其中已保存的收据。
同身份存在重复候选、收据变更、尚未完成保存、未通过新进程重载，或传感器
配置来源不一致时，选择失败且不启动原生进程。

只接纳带当前 profile/configuration 来源的 schema-v2 收据。
旧 schema-v1 地图保持原位，不重写、不升级标签；它们不能经此入口启动，
需通过既有只读诊断入口查看，或用当前受验证建图流程重新保存。
目录名不等于地图 ID；服务使用收据中的 session 确认身份。

选择器只负责有界收据定位与来源关联，不声明地图内容已经加载。
完整点云字节、有限数值、地图文件清单和实际输入来源由受监督子进程中的
现有 `load_map` 再验证。即使选择后文件发生变化，子进程仍必须自行通过检查，
不能由服务的 `starting` 结果跳过这些步骤。

## 私有控制接口

使用 Linux 同 UID 的私有 Unix socket。父目录要求 0700、socket 0600，
请求与响应继续有界，每次连接只接受一条 JSON 请求，不自动重试。

| action | 请求字段 | 结果 |
| --- | --- | --- |
| `runtime` | 仅 `action` | 服务代次、阶段、当前地图引用、清理状态 |
| `start` | `serviceId`、`mapId`、`receiptSha256`、`expectedGeneration` | 新代次与 `starting`，不等待原生加载 |
| `shutdown` | `serviceId`、`runId`、`expectedGeneration` | 立即撤销工作进程查询/控制，并异步退出进程 |
| `state` | 仅 `action` | 当前工作的既有定位状态；无活动进程返回 503 |
| `seed` | 既有 binding/代次/参考帧/矩阵及 `sourceFrame=lidar_chest_front` | 原样单次转交当前子进程 |
| `pause` / `stop` | 既有 binding 与定位代次 | 原样单次转交当前子进程 |

地图/服务引用过期返回 409，格式不符返回 400，工作进程不可用返回 503。
服务状态不返回私有日志或异常内容。故障只暴露固定码：地图不可选择、
受监督进程的既有固定故障码或通用 `localization_process_failed`。

读取 `runtime` 不读取点云、不执行配准；读取 `state` 只访问子进程缓存。
任何导航、行走、建图、语音、模型或预设动作请求都不在该协议中。

## 启动接入

普通用户在 Linux 使用现有匹配平台的 Python 环境与已核验配置，准备独立的
保存地图目录、0700 运行目录和私有 socket 父目录：

```sh
python -B robot-runtime/python/localization_service.py \
  --config /absolute/private-config.json \
  --maps /absolute/saved-maps \
  --runs /absolute/private-runs \
  --socket /absolute/private-control/localization-service.sock \
  --parent-stdin
```

运行目录必须与地图目录分离。路径长度还需允许新子目录中的 Linux Unix socket。
父进程须持有 stdin，EOF 即退出；不用父输入模式的前台运行仍由信号退出。
进程来源、Python/ARM64/Humble 兼容性及库完整性沿用现有核验，不安装或覆盖厂商环境。

旧定位 API 的 `state/seed/pause/stop` 协议保持不变，可在工作进程运行后代理；
本轮没有扩展 HTTP 写白名单，也没有更改已有独立定位启动器。
下一步需要增加服务代次接口与网页地图选择/启动/关闭流程，再进入统一部署接入。
单槽位限制只约束本服务内的定位子进程，尚不构成与另一个建图服务之间的互斥。
统一所有者必须补上建图/定位模式互斥后，才能作为产品自动编排部署；
本轮没有将两套独立服务接成可并发启动的生产流程。

## 验证范围

测试位于 `tools/tests/test_localization_service.py`。
合成地图元数据和健康事件均为隔离测试夹具，不是实机地图、传感器或定位精度证据。
真实 Linux 子进程、健康管道、Unix socket 和进程组使用现有监督器执行，
覆盖新建/关闭/再建、旧绑定拒绝、取消、故障、迟到响应和父输入 EOF/SIGTERM 回收。
实际 GLIM 建图/配准、现场地图选择、网页、APK、机器人运动与避障仍需后续单独验证。

SIGTERM 测试曾发现父输入监听线程的阻塞缓冲读取会导致 Python 退出异常。
服务改为可取消的管道就绪监听，并在退出时等待该线程结束。
验证同时核对退出码、socket 移除、原生 PID 消失及确认清理状态，不仅检查页面或端口。

本轮最终验证：新增服务测试 22/22 通过；全量 Python 1,055 项，1,040 通过、
15 项因可选环境依赖跳过。均在普通用户的隔离 Linux 环境执行；全量检查使用
同一固定提交、干净的 Linux 原生 GLIM 检出作为可选上游配置输入。
本轮没有改动前端/API TypeScript、Java 或 Android 源码，没有重跑其构建或实机验收。
