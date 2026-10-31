# 独立定位管理网页

日期：2026-10-30。本页最初的网页/API 阶段机器人断电，没有连接或部署机器人；
后续已完成 ARM64 录制定位与配对 HTTPS 回环检查，见本文末尾。
本页说明新增的配对 HTTP 接口、独立 API 入口及网页；
原生定位入口与证据继续见 [保存地图定位运行链路](SLAM_LOCALIZATION_RUNTIME.md)。
产品及已交付 APK 版本仍为 0.1.19。

2026-10-31 新增[点云初始位姿选取](SLAM_POSE_PICKER.md)：字段默认留空，
拖拽仅填写位置与朝向草稿，不自动提交。此源码更新未替换历史 ARM64 包或 APK。

## 本轮功能

独立控制台提供“定位验证”和只读“地图管理”。
定位页显示保存地图的点云投影，支持六自由度初始胸前雷达位姿输入、
状态刷新、暂停及停止。只有新鲜的 `tracking` 状态才叠加雷达位置和朝向。
地图 ID、实际收据字节摘要和点云摘要必须全部匹配，否则不显示叠加图。
图像继续使用点云，不转换成占据栅格。

初始位姿的 X/Y/Z 为 `native-raw`；横滚、俯仰、朝向以度输入，
转换为 `mapFromLidar = translation * Rz(yaw) * Ry(pitch) * Rx(roll)`。
这不是底盘位置、米制标定或导航 TF。初始数值只作为待编辑字段，
页面不会自动提交初值。

提交初值只表示请求被接受，仍需工作线程确认并连续匹配三次。
暂停后重新定位必须再次明确提交初值；停止后当前会话不能恢复。
断线、超时、暂停、停止和失效会撤销可见位姿，
旧查询及已断开连接的迟到命令响应不能重新激活显示。
命令失败或丢失确认均不自动重试，操作员需核对当前状态。

## 接口约定

所有接口沿用现有配对会话认证；控制接口还要求 HTTPS。
会话令牌仅留在网页内存，浏览器只保存机器人地址。

| 接口 | 请求及结果 |
| --- | --- |
| `GET /api/v1/slam/localization` | 返回经过严格校验的当前录制定位状态；无可用后端返回 503 |
| `POST /api/v1/slam/localization/seed` | `binding`、`expectedGeneration`、`sourceFrame=lidar_chest_front`、`referenceFrame`、4×4 `mapFromSensor` |
| `POST /api/v1/slam/localization/pause` | `binding`、`expectedGeneration` |
| `POST /api/v1/slam/localization/stop` | `binding`、`expectedGeneration` |
| `GET /api/v1/state` | `slamLocalization.status` 为 `available` 或 `unavailable`；可用时附 `snapshot` |
| `GET /api/v1/slam/maps/:id/points` | 点云预览附实际 `receiptSha256` 和已核验的 `pointsSha256` |

`binding` 包含定位 session、输入模式、地图 ID、收据/点云/配置 SHA256。
写请求必须匹配当前身份和代次；初值另须匹配新鲜参考帧。
矩阵必须为有限数值、正交且行列式为 +1 的刚体变换。
未知字段、缩放、反射及非法矩阵均被拒绝。

坐标系更正：早期定位页面和状态误标为 IMU；原生点输出的位姿实际取自
`T_world_lidar`，定位预测、匹配及底盘投影也使用该雷达位姿。
点源的 `PointPacket.source_frame=IMU` 仅指 GLIM 内部去畸变点的来源，
这些点经 `T_world_imu` 转到世界坐标后，再用雷达位姿逆变换为雷达扫描。
它不表示 `mapFromSensor` 属于 IMU。本轮没有更改原生点 ABI 或矩阵计算。
状态 `sourceFrame` 和初值请求现均明确为 `lidar_chest_front`；缺失、IMU、
底盘或其他坐标系请求返回 400，不改变已有跟踪代次。旧版诊断客户端必须
与新 API/Python 进程一同更新，不能给旧 IMU 初值简单换标签后重用。
该坐标语义更正不代表厂商内部雷达/IMU 外参已独立标定或物理精度验收通过。

写请求成功返回 202，状态代次必须恰好加一，`requestPending=true`，
位姿必须为空；这不是定位成功确认。冲突返回 409，非法请求返回 400，
后端超时、断开或协议无效返回 503。单次 Unix 请求超时为 1,200 ms，
请求/响应上限为 4,096/8,192 字节，每连接仅一次请求，不自动重试。

HTTP 适配最初只接受 `recorded`；后续增加显式 `live` 诊断输入，仍拒绝 `synthetic`。
输入模式不代表该地图定位或当前现场精度已经通过验收。
所有定位状态强制保持 `localizationReady=false`、`navigationReady=false`。
独立控制台不调用机器人状态、灵心、模型或运动接口，
固定地图入口的写入白名单含配对/撤销会话及上述三个定位操作；
按需服务另增加下文的启动/关闭操作。
业务、建图、语音回调和凭据写入均被拒绝。

## 运行接入

```sh
npm run build
npm run build:localization-runtime
```

新增入口为 `robot-runtime/dist/localization-main.mjs`。
它只提供管理 API，不启动原生定位，也不接管原生进程：
调用方须先按定位运行文档准备匹配平台的普通用户 Linux 录制定位进程，
再将其私有 `localization.sock` 传入。
原生 socket 的同 UID、0700 父目录、0600 socket 限制保持有效。

父进程须使用管道持有 API 的 stdin；关闭管道即关闭 HTTP/WebSocket。
SIGINT、SIGTERM 同样触发关闭。不要用 `nohup` 或断开 stdin 的方式保活。
现在可由 `managed_localization.py` 统一持有原生入口与 API；
参数和生命周期见 `SLAM_LOCALIZATION_RUNTIME.md` 的“统一启动与退出”。
这是有界录制诊断启动器，尚未接入生产安装器。

| 环境变量 | 要求 |
| --- | --- |
| `LINGXI_LOCALIZATION_PARENT_STDIN` | 必须为 `1`，由启动方持有 stdin |
| `LINGXI_LOCALIZATION_SOCKET` | 录制定位 Unix socket 的绝对路径 |
| `LINGXI_SAVED_SLAM_DIR` | 保存地图目录的绝对路径，与定位加载的地图一致 |
| `LINGXI_DATA_DIR` | 独立控制台状态目录的绝对路径 |
| `LINGXI_PAIRING_CODE_SHA256` | 私有配对码的 SHA256；生产启动必须提供 |
| `LINGXI_TLS_KEY` / `LINGXI_TLS_CERT` | 生产 HTTPS 私钥及证书文件 |
| `LINGXI_WEB_ROOT` | 可选，已编译网页目录；未指定时沿用 API 默认目录 |
| `LINGXI_HOST` / `LINGXI_PORT` | 生产默认 `0.0.0.0:3000`，端口须为 0–65535 整数 |

不能同时设置 `LINGXI_MAPPING_SOCKET` 或 `LINGXI_SLAM_POINT_FD`。
缺少父进程所有权、相对路径、非法端口或生产 TLS 均在监听前拒绝。
本机测试可使用 `LINGXI_DEMO_MODE=1`，仅监听回环地址、默认 3010，
允许本机 HTTP 测试，但不生成演示地图或把机器人标记在线。
此选项不能作为局域网生产 HTTPS 的替代。

## 本轮验证

| 检查 | 结果及范围 |
| --- | --- |
| 全量 Vitest | 256 通过、9 个 Linux 专用项在 Windows 明确跳过 |
| Linux Unix 后端 | 18/18 通过，含上述 9 个跳过项、1.2 秒超时和丢失确认不重试 |
| 编译 API 的实际 Linux 进程 | 7/7 通过：父管道 EOF、SIGTERM 关闭监听与 WebSocket，五种无效启动拒绝 |
| 构建 | 网页、API/运行时类型检查及独立定位 API 编译通过 |
| 合成浏览器 | 11 阶段通过，1440×900 桌面与 844×390 横屏，无横向溢出、区域重叠或浏览器错误 |

浏览器覆盖初值待确认/跟踪、地图摘要不匹配、暂停、迟到查询、
横屏、断线恢复、代次冲突和终止，并检查只读地图及令牌不持久化。
使用 `tools/fixtures/localization-ui.ts` 的独立回环 HTTPS 合成夹具，
通过 `tools/check-localization-ui.mjs URL SPKI SESSION OUTPUT` 复现。
输出目录必须为新的私有目录；夹具会在期限内退出。
测试证书的 SPKI 仅用于该次浏览器测试，不修改系统证书信任。

本次浏览器报告及截图位于 `.local/localization-ui-20261030-c/`，
包含页面像素、状态、截图摘要；它们是合成协议/UI 验证，
没有执行原生 SLAM，也没有验证机器人或 APK 渲染。
Linux 进程检查可设置 `LINGXI_LOCALIZATION_TEST_BUNDLE` 指向本轮编译 bundle，
再以普通用户执行 `node --test tools/check-localization-api.mjs`。

## 后续边界

后续统一启动器已通过原生录制到配对 HTTPS 的完整检查和 13 项进程故障测试，
详见运行文档；本节上方的浏览器结果仍为合成测试，没有重跑或改写。
上述阶段当时尚缺 ARM64/live 定位适配、
地图与建图配置关联证明、底盘坐标标定及独立动态精度验证。
导航规划、运动执行、实机急停与声学指标仍需后续阶段验收。
统一启动器阶段未改变冻结原生代码；另完成 87 项定位相关 Python 回归和
真实录制 HTTPS 检查，未重跑全部 930 项历史回归。
旧证据摘要、机器人安装包和 APK 保持原样。

随后 ARM64/Humble 录制定位绑定已在实际机器人通过原生到 HTTPS 检查：
未认证拒绝、配对、明确初值、跟踪、暂停、旧代次拒绝、停止和完整退出均正常。
停止后 554 次查询未恢复位姿，相同源码的 x64 兼容检查也通过。
94 项定位 Python 回归通过。详情见 `SLAM_LOCALIZATION_RUNTIME.md` 的
“ARM64 录制定位验证”及 `slam-managed-localization-arm64-evidence.json`。
这次使用机器人本地回环，没有重跑浏览器，也未验证 LAN/Android 实际访问；
实时定位、底盘标定、导航和产品验收范围不变。

后续实时雷达/IMU 已接入同一隔离 HTTPS 定位状态，网页按状态区分实时和录制输入。
本轮未提交初值，不声称保存地图跟踪通过；停止后 222 次查询没有恢复位姿。
更新后的网页另通过相同 11 阶段合成桌面/横屏回归，没有实机 LAN/Android 浏览器验收。
详情见 [实时运行链路](SLAM_LIVE_LOCALIZATION.md)。

## 雷达位姿约定更正验证

2026-10-30 更正了上述 IMU 误标，并为初值增加显式坐标系字段。
本次仅做离线与本机 Linux 验证，没有连接或部署机器人，没有发送运动指令。

| 检查 | 结果 |
| --- | --- |
| 全量 Vitest | 307 通过、9 个 Linux socket 项在 Windows 跳过 |
| Linux 定位 API/socket | 50/50 通过，包含上述 9 项 |
| 全量 Python | 1,033 项，1,018 通过、15 项因可选环境依赖跳过 |
| Python Unix 控制复核 | 27/27 通过，错配/缺失坐标系返回 400 且不推进代次 |
| 浏览器 | 11 阶段，1440×900 与 844×390；验证雷达标签、初值坐标系及位姿撤销 |
| 构建 | 网页、API/运行时类型检查和独立定位 bundle 通过 |

新增合成回归使用非零平移和旋转的 IMU/雷达外参，检查原生世界点只逆变换
一次，匹配初值与输出继续属于雷达；它不是物理外参标定或实际定位精度证明。
浏览器报告及截图保存在私有 `.local/localization-frame-ui-20261030-a/`。
测试服务和浏览器会话已退出，交付 APK/安装包仍保持 0.1.19 原样。

首轮全量 Python 的可选上游检查因 Windows 挂载的 GLIM 文件被 Linux Git
判为修改而失败；未修改该工作副本。改用 `LINGXI_GLIM_UPSTREAM_ROOT`
显式指定已有、干净且同一固定提交的 Linux 检出后，全量重新通过。
Linux Vitest 通过现有隔离依赖的 `NODE_PATH` 和 `ESBUILD_BINARY_PATH` 加载
Linux 原生二进制，没有重新安装或改写 Windows 依赖。

## 按需会话接入

2026-10-30，新增服务生命周期的 HTTP 和网页接入。原有固定地图入口保持兼容，
没有覆盖厂商环境、原生算法或已有安装包。本次没有连接机器人或发送运动指令。

`UnixLocalizationServiceBackend` 复用已有有界 Unix 传输。
独立 API 只有在 `LINGXI_LOCALIZATION_SERVICE=1` 时才选择服务协议，
`LINGXI_LOCALIZATION_SOCKET` 此时指向 `localization_service.py` 的私有服务 socket，
而不是某个子进程的 socket。其余 TLS、配对、地图目录和父输入所有权限制不变。
这只增加协议接入；现有固定地图所有者不会因此改为按需服务所有者。
生产统一所有者和安装器尚未接入，不应仅设置该变量就替换现有生产服务。

| 接口 | 请求及结果 |
| --- | --- |
| `GET /api/v1/slam/localization/runtime` | 返回服务 ID、运行代次、run ID、所选地图、阶段与清理状态；固定地图入口返回 501 |
| `POST /api/v1/slam/localization/start` | `serviceId`、`expectedGeneration`、`mapId`、`receiptSha256`；202 仅表示 `starting` |
| `POST /api/v1/slam/localization/shutdown` | `serviceId`、`expectedGeneration`、`runId`；202 撤销当前工作进程的查询/控制并异步关闭 |

新读接口仍要求配对，新写接口还要求 HTTPS。响应校验服务 ID、代次恰好加一、
所选地图或 run ID；错配确认不当作成功。每个控制请求只转发一次，不自动重试。
只接收地图身份和摘要，不接收文件路径、命令或传感器配置。
目录解析、schema-v2 来源匹配及最终原生加载仍由既有服务/子进程检查。
`running` 仅表示进程健康，所有定位/导航就绪标志继续为 false。

网页新增保存地图选择和启动/关闭按钮。缺少配置来源或合成输入地图不可选；
预览取得实际收据摘要后才开放启动。工作进程占用时禁止换图，关闭只显示待清理，
清理未确认或失败时不允许新建。`stop` 保持终止定位会话的含义，
不等于 `shutdown`，也不等于进程已退出。

服务模式按“runtime → 定位状态 → runtime”读取；只有服务、运行代次、run ID、
地图摘要、输入模式和运行阶段保持一致，才接纳工作进程位姿。
观测过期、断线、控制派发或连接切换即撤销旧位姿，旧响应不能恢复显示。
新 run 清空上次初始位姿字段，没有自动提交或恢复。固定地图入口只在明确的
501 能力响应后继续使用原有状态查询；503、超时或格式错误不会降级成可控会话。

本次验证分为三层：纯协议/路由测试；实际 Linux HTTP 监听到 Unix 服务及
受监督合成子进程的两次新建/关闭；回环 HTTPS 合成网页的桌面/横屏测试。
真实进程检查核对旧绑定拒绝、两次 session 不同、子进程 PID 消失、父输入 EOF
以及服务 socket 删除。合成浏览器覆盖新服务 17 阶段和固定入口 11 阶段，
含迟到查询、匹配位姿、摘要错配、清理失败阻止替换、重新启动初值清空。
两种视口为 1440×900 和 844×390，均检查点云像素、位姿标记及无横向溢出/面板重叠。
它们不是 GLIM 实际执行、机器人定位精度或 Android 实机验收。

复现服务浏览器夹具时，为 `tools/fixtures/localization-ui.ts` 的原有三个参数
（新私有目录、编译网页目录、30–900 秒期限）追加 `service`；不追加则测试固定入口。
以工具输出的回环 URL 与该次证书 SPKI 调用 `tools/check-localization-ui.mjs`。
报告保存在忽略入库的 `.local/localization-ui-service-20261030-a/` 和
`.local/localization-ui-fixed-20261030-d/`，不得当成实际地图或机器人数据交付。
Linux 真实服务整链路测试为 `localization-service-process.test.ts`，需显式设置
`LINGXI_LOCALIZATION_SERVICE_TEST_PYTHON` 为具备既有 numpy 依赖的普通用户 Python。

最终检查：Windows 全量 Vitest 336 通过、14 个 Linux 专用项跳过；
Linux 全量 350/350 通过，包含真实服务 HTTP/Unix/合成子进程整链路；
Python 服务回归 22/22，编译 API 进程回归 7/7，网页与独立 API 构建通过。
首次编译 API 检查在 Windows 挂载依赖下有两项超过原有 8 秒启动期限；
转到 Linux 本地依赖后完整复核通过，没有放宽期限。两份 bundle SHA256 一致，
95 个运行依赖版本逐项一致。此次未修改 Java/Android 源码或重打 APK，
也没有重复全量 Python/原生 SLAM/实机验收；构建仅有依赖包的注释注解警告。

2026-10-31 后续已为同地图目录的两项服务入口加入
[共享运行锁](SLAM_ACTIVITY_OWNERSHIP.md)，不涉及网页改动。
[统一工作区](SLAM_WORKSPACE.md) 随后完成同一 API/服务所有者的源码组合和网页切换。
尚缺：将所有产品入口纳入互斥、统一安装器和 APK 更新，以及该组合的 ARM64 验收。
之后仍需实机带初值的保存地图跟踪、物理标定、导航/避障及语音验收；
当前 live 会话的有界时长限制保持原样，不自动续期或重启。
