# 物理导航桥契约

更新于 2026-11-03。本轮已提供独立产品的 ROS 2/Nav2 物理桥生产骨架、私有
Unix Socket 客户端与 AIMDK 输出适配器；真实机器人 topic/frame、Nav2 controller
输出、地图格式和现场安全验收仍需在目标机上完成。离线测试不代表机器人能够导航。

## AIMDK v1.1.0 输出确认

压缩包中的 `McLocomotionVelocity` 只有 `header`、`source` 和三项速度字段，
没有 `nonce`、`stop_confirmed` 或 `stopped`。桥因此订阅 `/aima/mc/common/state`，
并通过 `/aimdk_5Fmsgs/srv/SetMcInputSource` 先 ADD、再 ENABLE 独立输入源。
停止回执由本地 nonce、零速发布时刻，以及零速发布之后新收到的本源
`McCommonState.fsm_state.current_state` 为 `STABLE(2)` 或 `SAFE(4)` 组合确认。
旧状态、其他输入源、超时状态和注册失败都不会授予 `velocityOutput` 准入。

## 显式启用

`LINGXI_NAVIGATION_SOCKET` 默认不配置。只有以 `independentProduct: true`
构建的独立产品运行时才允许使用它；普通 AIMDK/vendor API、独立建图/定位
控制台及离线 Nav2 工作区不能混用。误配置时拒绝启动，不悄悄切换后端。
独立运行时还需要原有的建图、定位服务与已保存 SLAM 地图目录。

外部桥进程负责 ROS 2/Nav2 生命周期与输出，不替换 AIMDK 或厂商系统。
网页与 APK 不提供扫图遥控、任意速度或关节控制。扫图移动仍由外置遥控器完成。
健康接口、点云显示和地图保存都不能单独授予导航权限。

## 本地连接

```dotenv
# 默认关闭；完成部署核对后才在独立产品运行时设置。
# LINGXI_NAVIGATION_SOCKET=/var/lib/lingxi-next/run/navigation.sock
```

客户端只在 Linux 运行，要求绝对且规范化的路径，UTF-8 编码长度小于 100
字节。部署者必须让 API 与桥使用同一受限 UID，专用目录模式 `0700`、Socket
模式 `0600`，不可放入共享可写目录或通过符号链接共享。不开放 TCP 监听。
路径格式由客户端校验；所有者、目录与 Socket 权限由部署流程负责保证。
通用安装器不会自动开启物理桥；部署包必须显式安装 `lingxi-next-navigation.service`
并完成独立产品、地图收据、标定、足迹和真实 ROS 接口核对。

## 请求与回执

每次连接只传一个请求和一个回执，均为以换行结束的 UTF-8 JSON。客户端
串行处理请求，最多保留 16 个在途/排队请求。每个请求含随机 32 位十六进制
`id`（16 字节），回执必须原样返回它。回执上限为 64 KiB，不接受多行回执。
正常请求使用新连接；传输一旦失败，本客户端实例不再建立连接或重放请求。

成功与失败回执的结构分别为：

```text
{"id":"<32 hex>","ok":true,"value":<PhysicalNavigationState>}
{"id":"<32 hex>","ok":false,"code":"<lower_snake_case>","status":409}
```

失败 `status` 只允许 `400`、`409`、`503`。错误码不包含业务数据。
精确字段以 `shared/physical-navigation.ts` 为准：

| action | 请求字段（除 id/action） | 超时 |
|---|---|---|
| `state` | 无 | 1500 ms |
| `activate_map` | `session`、`mapId`，可选 `evidence`、`initialPose` | 5000 ms |
| `start` | `session`、`expectedEpoch`、`taskId`、`mapId`、`pointId`、`goal`、`evidence` | 5000 ms |
| `pause` / `resume` | `session`、`expectedEpoch`、`taskId`、`mapId`、`pointId`、`evidence` | 5000 ms |
| `stop` | `session`、`expectedEpoch`、`taskId`、`mapId`、`evidence`，当前点位存在时附 `pointId` | 7000 ms |

`goal` 和 `initialPose` 为 `{x,y,yaw}`，单位是米与弧度。地图 ID 为
`slam-` 加 32 位十六进制。证据包含 `mapId`、`receiptSha256`、
`calibrationSha256`、`footprintSha256`，三个哈希均为小写 SHA-256。
桥负责核对实际地图回执、标定与足迹内容，不能只回显请求值。

## 状态与准入

每次回执都返回完整状态：`protocol: 1`、`source: "ros2-nav2"`、`session`、
严格递增的 `sequence`、`identity`、`phase`、`detail`、`ticket`、`pointId`、
`requiresStop`、`outputQuiescent`、`pose`、`checks`、`navigationReady` 和
`physicalMotionSent`。`ticket` 为 `{task_id,epoch}` 或 `null`，与点位同步存在。
`session` 和任务 `epoch` 都是 32 位十六进制，重启桥须更换 `session`。

`checks` 必须来源于真实、持续更新且失效即撤销的证据：

| 字段 | 外部桥需核验的内容 |
|---|---|
| `nav2Lifecycle` | 必需 Nav2 节点处于可用生命周期状态 |
| `localization` | 当前地图绑定的定位有效且新鲜 |
| `tf` | 地图、底盘、传感器的时间对齐坐标变换有效 |
| `costmap` | 当前地图下全局/局部代价地图有效且新鲜 |
| `terrain` | 实测地面、地形约束与当前机器人配置相符 |
| `footprint` | 当前生效足迹与已核验的足迹哈希一致 |
| `velocityOutput` | 唯一受控输出链路接通，停止/撤销机制可用 |

仅当七项全部为真且 `requiresStop` 为假时，`navigationReady` 才能为真。
API 校验字段一致性，但不能替桥证明实际传感器新鲜度或底盘安全性。
地图回执、标定、足迹的哈希绑定也不能替代几何精度与安全距离验收。

## 任务与地图切换

- API 串行执行状态读取和导航操作，状态回执必须比上一回执更新。
- `start` / `resume` 必须确认相同任务/点位、新 epoch，以及已请求、运行中
  或已完成状态。`pause` 必须保留任务 epoch，确认暂停且输出静止。
- `stop` 只有确认 `outputQuiescent=true`、`requiresStop=false` 且处于
  `idle` / `finished` / `failed` 时才成功。仍在停止中的回执不算成功。
- 普通状态读取不可擅自改变 session、任务 epoch 或地图证据。迟到回执、
  序号倒退、身份不符、未确认命令均锁住后续 start/resume/pause。
- 地图切换先确认输出静止，再由桥核验新地图证据。切换回执必须是无任务的
  `idle` 状态、输出静止且不要求 stop。切换地图不代表七项准入已经通过。
- 已保存且经目录校验的地图可通过物理桥激活后进入普通地图状态库；没有
  物理桥时，保存目录仍为只读目录，不自动成为可导航地图。

## 故障恢复

命令未确认时不自动重试或排队重放旧动作，后续普通导航操作被拒绝。
适配器只有收到新的、身份匹配且输出已静止的显式 `stop` 回执才解除不确定
状态。正常轮询即使再次返回就绪也不能解除这一状态。

如果故障来自 Socket 超时、断开或无效协议，传输客户端本身已永久关闭；
不能在该实例上直接重试 stop。需现场先通过受认可的停止/急停链路确认机器人
停止，检查桥和任务清退情况，再受控重启产品运行时并显式确认 stop。
重启不会证明旧输出已经停止，不能用重启绕过输出确认。

## 部署前核对

1. 外部物理桥实现与本契约一致，传感器/定位证据具有过期撤销机制。
2. 地图、标定、足迹版本匹配，坐标方向和实际距离通过现场核验。
3. 同 UID、目录/Socket 权限与路径符合要求，通用 AIMDK API 未启用此选项。
4. 急停、人工停止、暂停、继续、超时与断连后的输出清退均经现场验收。
5. 完成短距离导航、静态/动态避障和讲解打断恢复测试后再记录实际结果。

`deploy/diagnose.sh` 只检查配置和 Socket 文件是否存在，不发导航命令，不把
存在的 Socket 当作监听存活、七项就绪或实机验收通过的证明。

## 本轮离线检查

2026-11-03 的验证结果：

- Windows 定向 Vitest：44 项通过、3 项 Linux Socket 测试跳过。
- WSL/Linux Node 22 实际 Unix Socket 检查：连续请求往返、回执 ID 不匹配时
  及时失败、超时后不重连/重放，三种场景全部通过；没有机器人连接。
- WSL/Linux 诊断脚本单测：8 项通过，包含未配置、文件不存在、真实 Socket
  文件存在、权限提示与非 Socket 文件检查。Socket 测试未启动导航或 ROS。
- 原有 AIMDK Python 桥单测：39 项通过；`bash -n deploy/diagnose.sh` 通过。
- API 与共享 SLAM 运行时的 esbuild 打包通过。完整回归与产品构建结果记录在
  [当前离线工作流](OFFLINE_PRODUCT_ACCEPTANCE.md)。

诊断检查可单独重跑：

```sh
python3 -m unittest discover -s tools/tests -p test_navigation_diagnostic.py -v
npx vitest run robot-api/tests/physical-navigation.test.ts robot-api/tests/independent-product.test.ts
```

这些测试只确认客户端协议、管理 API 组合、物理桥骨架和诊断行为，不证明真实
ROS 接口、地图精度、定位、避障、Android 真机或语音首响指标合格。
