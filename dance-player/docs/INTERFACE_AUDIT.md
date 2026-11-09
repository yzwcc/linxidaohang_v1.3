# 断点续跳接口审查

审查日期：2026-11-09。对象：本地 `aimdk-aarch64-1bde262f-artifacts`；公开文档为 AIMDK_X2 1.1.0。**目标机器人的实际型号、固件、AIMDK 版本和舞蹈目录尚未核对。**

## 结论

节目单编辑本身没有技术阻碍，但当前材料不足以实现“暂停后站立并对话，再从原位置同步续跳”。这表示能力未验证，不表示厂商底层一定不支持。不能在未取得支持契约的情况下自行向 `meta` 填入 `offset`、`resume` 等参数。

本次工具扫描该 SDK 的101个 `.srv/.msg/.action` 定义，提取9个核心定义和33个候选接口名；候选名称未出现 pause/resume/seek/checkpoint。名称扫描仅辅助检索，不能证明能力不存在。[JSON证据快照](sdk-interface-evidence-1bde262f.json)保存字段行号和文件哈希，可用 `tools/probe.py` 重做检查。

## 接口证据与边界

下表路径相对于 SDK 的 `src/aimdk_msgs/interface/`。

| 定义 | 观察到的能力 | 不足以证明的能力 |
| --- | --- | --- |
| `robot/app_proxy/srv/GetRobotResources.srv` | 返回 `RobotResource[] robot_resources`，包含 key 和 current_version | 不返回实时舞蹈进度、暂停断点或完成事件 |
| `robot/app_proxy/srv/ExecuteActionResource.srv` | 请求含 header、resource_key、resource_version、slaves、meta；响应只有 CommonResponse | 没有明确的暂停/继续/seek 字段，也没有匹配舞蹈会话的完成反馈 |
| `robot/common/msg/CommonResponse.msg` | header、status、message | 服务应答不能直接证明整支舞已经完成 |
| `robot/mc/motion/srv/SetMcPresetMotion.srv` | 预设动作、interrupt、ani_path、play_timestamp | `play_timestamp` 注释为 UNIX 毫秒启动时刻，0表示立即执行；不是舞蹈偏移量 |
| `robot/mc/motion/msg/McPlayerState.msg` | 上肢播放状态：IDLE、PRE_PLAYING、PLAYING、INTERRUPTING、ERROR | 不是全身灵创舞蹈的时间轴，也不能关联当前资源的完成状态 |
| `robot/hal/audio/msg/AudioFile.msg` | 音频文件、格式和优先级 | 没有已定义的断点恢复字段 |
| `robot/interaction/msg/PlayMediaFileRequest.msg` | 文件播放、优先级及打断策略 | 没有已定义的音频断点恢复字段；音频恢复也不能证明身体动作恢复 |

SDK 的 `src/py_examples/py_examples/play_linkcraft.py` 仅示范发现资源和启动。官方示例按 key 中是否含 `onnx` 选择 `BODY_MONTION` 或 `ARM_MONTION`，该命名约定不能当作“支持从任意姿态断点续跳”的依据。

## 底层强化学习示例

SDK 的 `extra/x2_rl_deploy/README.md` 明确注明：示例仅在仿真环境验证，通过它控制真实灵犀X2需要重新适配参数。控制器包含动作帧/phase 和推理历史，并输出关节控制指令。它不是安装舞蹈的已验证续跳接口；修改帧计数不能独立解决站立到舞蹈姿态的平稳过渡、推理历史恢复或音乐同步。本阶段不运行或部署该控制器。

## 公开资料

- [灵创动作接口](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/control_mod/linkcraft.html)：公开列出 GetRobotResources、ExecuteActionResource 和群控时间同步。`Play success` 表示启动调用成功；不能作为播放结束证明；文档说明不以 status.value 判断该调用成功。
- [运动状态](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/control_mod/mc_status.html)：通用运动状态不能替代匹配当前灵创资源会话的进度/结束通知。
- [预设动作](https://x2-aimdk.agibot.com/zh-cn/latest/Interface/control_mod/preset_motion.html)：预设动作的时间参数不能直接套用为灵创舞蹈断点。
- [灵创平台使用手册](https://agirobot.feishu.cn/wiki/IOAdwbPc3i0o8YkLOz0c2sgTn63)：作品、下发和播放概念可以参考，但不构成机器人运行时断点续跳的接口契约。

下一步取得[厂商确认单](VENDOR_QUESTIONS.md)所列资料，再在目标版本上执行[实机验证](ACCEPTANCE.md)。离线扫描、ROS发现成功、仿真演示和历史启动成功记录均不能代替本次实机验证。
