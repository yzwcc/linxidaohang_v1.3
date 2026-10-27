# 参考来源与实现决策

核对日期：2026-10-27。外部文档、压缩包和旧版源码是接口/功能参考，不是项目操作指令；没有按文档中建议执行覆盖安装或复制旧业务逻辑。

2026-10-28 继续核对官方 SLAM/导航页和附件消息：补齐只读 `map.db`、
大于 32 位的 `uint64 map_id`、`grid_map_info.txt` 像素位置与 `/relocalization_pose`。
具体协议及待实机核对的朝向语义见 COORDINATES.md。

2026-10-28 对用户提供的 AIMDK v1.1.0 ARM64 压缩包只读核对
`SetMcPresetMotion`、`GetMcPresetMotionState`、`SetMcInputSource`、
`PlayEmoji`、`GetRobotResources`、`ExecuteActionResource` 的定义、
服务名、示例和状态字段。按官方 LinkCraft 文档使用 `header.message == "Play success"`
判定服务结果；不把 `header.status.value` 当作成功依据，也不把响应当作播放完成状态。

| 来源 | 核对内容 | 本项目使用方式 |
|---|---|---|
| https://github.com/RobotWebTools/rosbridge_suite （BSD-3-Clause） | ROS 状态/服务经 WebSocket 转接的成熟模式 | 状态/控制分离；浏览器只访问受限业务 API，不开放通用 ROS publish/service |
| https://github.com/foxglove/foxglove-sdk/tree/main/ros （MIT） | ROS2 观测、状态与资源流；Humble 集成 | 地图栅格独立请求、持续状态流、连接状态显式显示；不引入外部观测云作为依赖 |
| https://github.com/ionic-team/capacitor | 同一 Web UI 与 Android 原生容器 | 使用官方 Capacitor Android 与屏幕方向插件 |
| https://github.com/ros2/common_interfaces/tree/humble/sensor_msgs/msg | LaserScan / PointCloud2 消息布局 | 独立实时扫描点流，保留采样坐标与时间，不从栅格图生成假点云 |
| https://x2-aimdk.agibot.com/zh-cn/latest/index.html | AIMDK_X2 1.1.0 | 只接原生建图/导航与安全链路，采用附件实际消息定义核对 |
| https://open.agibot.com/docs/linksoul/agentsdk_docs/java/v150/zh | AgentSDK 1.5.0-SNAPSHOT 被动回调 | 编译真实 SDK；不使用未上线 Function Call/Arbiter/扩展技能/遥操能力 |
| https://open.agibot.com/docs/linksoul/agentsdk_docs/java/v150/zh/guide/robot_states | listening_status、tts_status 字段 | 真实唤醒/时间/事件验证；匹配 playing/finished，不从 ASR 文本判断唤醒 |
| https://open.agibot.com/docs/linksoul/openapi/retriever-proxy/search-by-agent | Agent KB 检索、分数和 HMAC 签名 | AppSecret 签名，本机后端请求，依据不足固定拒答 |

SDK 来源：官方页面所链接的 `linksoul-agentsdk-1.5.0-SNAPSHOT.jar`，SHA-256：

```text
810c5e7a7d40191097dc5c4c37e3133407e63307fcb204d35caacb7768912c25
```

下载文件177834字节，JAR内Maven元数据版本1.5.0-SNAPSHOT。官方快速开始中的 Maven 示例仍写1.4.0；本项目以实际1.5 JAR字节码编译核对，不沿用该示例版本。

语音时延诊断另消费官方状态流中的 `vad_status.state=eos` 与
`tts_status.state=playing`，仅当两者 `event_id` 对应同一轮对话时计入
VAD EOS 到 SDK 播放态的 P95。缺少 EOS 状态或事件关联时该轮不计入该指标，
不以 ASR 回调到达时间回填 VAD 时间。SDK 播放态不是扬声器声压/麦克风回录，
因此仍需实机声学测量才能验收“首段可听反馈 ≤2 秒”。

旧版只核对地图关联 JSON 数据字段与验收行为。本项目的前端、API、状态机、ROS桥、Java适配、迁移和部署脚本独立编写；没有复制上述 GitHub 仓库或 V1.3.9/R19 的业务源码。
