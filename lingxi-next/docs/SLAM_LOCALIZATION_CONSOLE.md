# 离线定位管理网页

日期：2026-10-30。机器人断电，本阶段没有连接或部署机器人。
本页说明新增的配对 HTTP 接口、独立 API 入口及网页；
原生定位入口与证据继续见 [保存地图定位运行链路](SLAM_LOCALIZATION_RUNTIME.md)。
产品及已交付 APK 版本仍为 0.1.19。

## 本轮功能

独立控制台提供“定位验证”和只读“地图管理”。
定位页显示保存地图的点云投影，支持六自由度初始 IMU 位姿输入、
状态刷新、暂停及停止。只有新鲜的 `tracking` 状态才叠加 IMU 位置和朝向。
地图 ID、实际收据字节摘要和点云摘要必须全部匹配，否则不显示叠加图。
图像继续使用点云，不转换成占据栅格。

初始位姿的 X/Y/Z 为 `native-raw`；横滚、俯仰、朝向以度输入，
转换为 `mapFromImu = translation * Rz(yaw) * Ry(pitch) * Rx(roll)`。
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
| `POST /api/v1/slam/localization/seed` | `binding`、`expectedGeneration`、`referenceFrame`、4×4 `mapFromSensor` |
| `POST /api/v1/slam/localization/pause` | `binding`、`expectedGeneration` |
| `POST /api/v1/slam/localization/stop` | `binding`、`expectedGeneration` |
| `GET /api/v1/state` | `slamLocalization.status` 为 `available` 或 `unavailable`；可用时附 `snapshot` |
| `GET /api/v1/slam/maps/:id/points` | 点云预览附实际 `receiptSha256` 和已核验的 `pointsSha256` |

`binding` 包含定位 session、输入模式、地图 ID、收据/点云/配置 SHA256。
写请求必须匹配当前身份和代次；初值另须匹配新鲜参考帧。
矩阵必须为有限数值、正交且行列式为 +1 的刚体变换。
未知字段、缩放、反射及非法矩阵均被拒绝。

写请求成功返回 202，状态代次必须恰好加一，`requestPending=true`，
位姿必须为空；这不是定位成功确认。冲突返回 409，非法请求返回 400，
后端超时、断开或协议无效返回 503。单次 Unix 请求超时为 1,200 ms，
请求/响应上限为 4,096/8,192 字节，每连接仅一次请求，不自动重试。

HTTP 适配当前只接受 `recorded`，不以实时或合成输入冒充已验证定位。
所有定位状态强制保持 `localizationReady=false`、`navigationReady=false`。
独立控制台不调用机器人状态、灵心、模型或运动接口，
写入白名单仅含配对/撤销会话及上述三个定位操作。
业务、建图、语音回调和凭据写入均被拒绝。

## 运行接入

```sh
npm run build
npm run build:localization-runtime
```

新增入口为 `robot-runtime/dist/localization-main.mjs`。
它只提供管理 API，不启动原生定位，也不接管原生进程：
调用方须先按定位运行文档准备普通用户 Linux x86_64 录制定位进程，
再将其私有 `localization.sock` 传入。
原生 socket 的同 UID、0700 父目录、0600 socket 限制保持有效。

父进程须使用管道持有 API 的 stdin；关闭管道即关闭 HTTP/WebSocket。
SIGINT、SIGTERM 同样触发关闭。不要用 `nohup` 或断开 stdin 的方式保活。
此阶段还没有将原生入口与 API 合成一键部署启动器。

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

尚缺原生定位与 HTTP 的统一启动所有权、ARM64/live 定位适配、
地图与建图配置关联证明、底盘坐标标定及独立动态精度验证。
导航规划、运动执行、实机急停与声学指标仍需后续阶段验收。
本轮未改变冻结原生代码，未重跑此前 930 项 Python 回归或原生录制试验，
也没有改写旧证据摘要、重打机器人安装包或 APK。
