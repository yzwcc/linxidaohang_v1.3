# 灵创舞蹈播放器：接口验证阶段

目标是独立部署在机器人上的节目单播放器：同网段手机/电脑编辑完整舞蹈的顺序，保存节目单，顺序播放或整单循环。每支舞使用原配音乐，通过机器人扬声器播放。

**当前交付是第一阶段验证工具和资料，不是可控制机器人的播放器。** 本地接口审查已完成，尚未连接目标机器人，断点续跳能力仍为 `UNVERIFIED`。第二阶段实机验证通过后才进入播放器开发，不会用重新启动舞蹈替代续跳。

## 进度与入口

| 阶段 | 当前状态 | 交付或进入条件 |
| --- | --- | --- |
| 接口核查 | 已完成本地 SDK 审查；等待厂商补充 | [接口审查](docs/INTERFACE_AUDIT.md)、[厂商确认单](docs/VENDOR_QUESTIONS.md)、只读工具 |
| 单支舞蹈实机验证 | 未执行；机器人当前不可连接 | 取得可用接口并完成[验收记录](docs/ACCEPTANCE.md) |
| 节目单播放器开发 | 未开始 | 目标版本及目标舞蹈的断点续跳验证通过 |

## 已锁定的产品行为

- 暂停时冻结动作与音乐的实际播放位置，机器人平稳站立，期间可以正常对话。语音唤醒不自动暂停或继续舞蹈。
- 手动继续时，从保存位置恢复当前舞蹈的动作和原配音乐；恢复过程需要适配站立到舞蹈姿态的过渡。
- 当前舞蹈真实结束后才切换下一支；不能靠 WAV 时长或倒计时认定舞蹈完成。
- 机器人端持有播放会话和进度，浏览器只是控制端。锁屏、关闭浏览器或断网不中断播放，重连显示实际状态。
- 区分播放中、暂停过渡、已暂停、恢复过渡、完成和故障。重复点击不重复执行；服务重启后等待人工操作，不自动重放。
- 核心验收场景：第二支舞播放到约35秒，暂停并对话数分钟，继续同一支舞的保存位置，然后自动进入第三支。

## 本机离线检查

工具使用 Python 3.9+ 标准库，无需安装依赖。以下命令在仓库根目录执行，仅读取指定 SDK 的 ROS 接口文件，报告路径必须尚不存在：

```powershell
rtk proxy python -X utf8 dance-player/tools/probe.py --sdk-root ".cache_new_aimdk_1bde262f/aimdk-aarch64-1bde262f-artifacts/aimdk-aarch64-1bde262f-artifacts" --output ".cache_dance_sdk_local.json"
rtk proxy python -X utf8 -m unittest discover -s dance-player/tests -v
```

也支持直接指定 SDK 的 `src/aimdk_msgs/interface` 或安装后的 `share/aimdk_msgs`。报告包含核心定义的字段、行号和 SHA-256，以及候选接口名称，不读取环境变量、密码文件或舞蹈模型。候选名称匹配不代表支持暂停/续跳；`string meta` 也不代表接受任意播放参数。

## 机器人可连接后的只读采集

把本目录复制到目标机器人，在已正确加载 ROS/AIMDK 环境的终端执行。这里使用机器人上的 `python3`；无需为了采集安装 RTK。未连接机器人前不使用历史 IP 作为当前地址。

```bash
python3 dance-player/tools/probe.py --ros --output dance-ros-observation.json
```

该命令仅执行固定的 `ros2 service list -t`、`topic list -t`、`interface list/show`。不调用运动服务，不发布控制消息，不运行 SDK 示例、不启动或重启机器人业务服务。默认每条查询最多5秒，共8条；ROS 发现不可用、权限不足或超时均记为采集失败，不解释为“设备没有这项能力”。

从报告中确认 `aimdk_msgs/srv/GetRobotResources` 的实际服务名，再进行资源目录查询。下面的占位符必须替换为发现的服务名：

```bash
python3 dance-player/tools/collect_resources.py --service '<实际服务名>' --output dance-resources.json
```

资源采集需要目标机器人已有 `rclpy` 和 `aimdk_msgs`，只调用 `GetRobotResources`，不会调用 `ExecuteActionResource`。报告保留资源名称、key 和当前版本，不推算舞蹈时长。SDK 的 `play_linkcraft.py` 还会启动舞蹈，**不要将它作为只读采集脚本运行**。

退出码：`0` 表示本次指定的采集完整完成；`1` 表示采集不完整、失败或报告无法写入；`2` 表示参数错误。`0` 不表示支持断点续跳，报告始终保留 `playback_gate=NOT_VALIDATED`、`physical_validation=NOT_RUN`。固件、机型和 AIMDK 版本需要人工核对，`--robot-model`、`--firmware`、`--aimdk-version` 只记录操作员提供值，不自动验证其真实性。

本地采集报告保存在 `.cache*` 或本目录 `reports/`（Git 忽略）。提交或转发报告前检查现场服务名称等内部信息；不提交原始配置、认证信息或完整机器人日志。随代码保留的[离线证据快照](docs/sdk-interface-evidence-1bde262f.json)仅包含已审查 SDK 的接口信息，不能代表当前机器人。

## 下一阶段的通过条件

厂商给出的接口契约和单支舞蹈实测记录必须覆盖五项能力：真实进度与完成反馈、暂停保存并站立、从站立断点续跳、音乐同步与语音交接、目标版本及资源兼容性。任一缺失保持“未验证”，不开放实机播放器。如果只能通过替换底层运动控制器实现，则另行评估运动控制研发，不在现有导航服务上直接替换。
