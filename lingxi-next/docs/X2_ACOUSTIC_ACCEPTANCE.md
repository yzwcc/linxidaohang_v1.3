# X2 V1.1.5 现场语音验收取证

`tools/score_x2_voice_field.py` 在电脑上离线读取 WAV 和现场标注，输出四个量化门槛。它不连接机器人，不测量 SDK 播放态，不上传或复制录音，也不替代现场人员辨认说话者、核对事件归属和签署验收。录音、标注与运行证据放在受限目录，须事先取得录音许可，不放进安装包或 Git。可分享不含路径、设备编号或转写的汇总 JSON。

## 取样

1. 在同一台目标 X2、同一版本和声学环境中预先登记 100 次有效唤醒试次，覆盖实际使用的问答和控制场景；不能只登记成功轮次。另登记有代表性的模糊和越权负样本，检查 API/动作侧实际是否派发，不以回复文案判断。动作与导航试次沿用[现场监护流程](X2_REFERENCE_COMMISSIONING.md#x2-现场验收顺序与记录)。
2. 用同一录音设备及采样时钟录下用户语音和机器人扬声器输出，例如同一双通道 WAV 分别靠近语音和扬声器。录下未经剪接、时间伸缩、消除或重采样的 16-bit PCM WAV，8–96 kHz，单声道或双声道。人工复核波形和听感，标注语音结束与本轮首段可听反馈的采样帧；背景声、回声、上一轮语音、SDK `playing` 不计作首响。每个有效试次语音结束后至少留 5 秒，且试次窗口不重叠。没有本轮反馈标 `null`，不能删除该轮或补成 SDK 时间。
3. 另录连续 8 小时现场噪声，不混入测试唤醒词或业务控制命令。对于普通 RIFF WAV，建议 16 kHz 单声道以避免 4 GB 容器限制。同步保存这 8 小时的真实唤醒事件观测，覆盖整段噪声，从 SDK 回调与声学记录对照去重并排除真实唤醒。即使没有可听回复，误开会话也算误唤醒。采样或事件监控中断，应标为不完整，不能拼接不同窗口凑时长。

## 标注文件

把 `annotations.json` 和录音放在同一受限目录。JSON 顶层只包含 `schemaVersion`（`1`）、`captureKind`（现场 `field`，合成 `synthetic`）、`target`、`recordings`、`wakeTrials`、`negativeTrials`、`noise`：

| 字段 | 内容 |
|---|---|
| `target` | `deviceRef`（匿名编号）、`firmware`（V1.1.5 的完整 SoC1 版本）、`aimdk`（实装版本标识，去掉显示用前缀）、`release`、`profileSha256`（经现场复核的试验环境记录 SHA-256） |
| `recordings` | 例如 `{ "trials": { "file": "trials.wav", "sha256": "…" }, "noise": { "file": "noise.wav", "sha256": "…" } }`。路径相对 JSON 所在目录；SHA-256 为文件的 64 位小写十六进制摘要。 |
| `wakeTrials` | 恰好 100 个对象，例如 `{ "recording": "trials", "start": 0, "end": 96000, "speechEnd": 8000, "wakeConfirmed": true, "firstAudible": 24000 }`。`wakeConfirmed` 应由真实有效的唤醒回调确认；`firstAudible` 为整数或 `null`。 |
| `negativeTrials` | 至少覆盖 `ambiguous`（模糊）和 `unauthorized`（越权或未配置）两类。每项含 `recording`、`start`、`end`、`case`、`executed`，后者为实际派发或执行的布尔值。 |
| `noise` | `{ "recording": "noise", "monitoringComplete": true, "falseWakeFrames": [] }`；误唤醒按同一录音的采样位置从小到大列出，每个独立事件一项。 |

帧号从 0 开始，单位为采样帧（双声道同一时刻算一帧），不是毫秒、字节或墙钟。`start` 含该帧，`end` 不含该帧。环境记录应包含操作者、见证人、版本、试次计划、录音通道/位置、环境噪声、回调归属、任何丢样与排除原因；保留同一批原始证据，以便独立复核。不要把账号、鉴权信息或转写放入标注。

在解包目录或源码根 `lingxi-next` 下运行：

```bash
python3 -B tools/score_x2_voice_field.py /private/x2-voice/annotations.json
```

程序检查 WAV 帧数、文件摘要、试次窗口、重复录音以及缺样，再报告四项门槛（100 次至少 95 次有效唤醒、8 小时最多 1 次误唤醒、模糊/越权误执行 0 次、首响 P95 ≤ 2 秒）。P95 取全部 100 轮的第 95 位，未响轮次排在最后；当至少 6 轮未响时，P95 无有限值并判失败。返回码 `0` 表示标注的数值均达到门槛，`1` 表示未达到，`2` 表示证据格式或完整性不合格。即使返回 `0`，也必须对照原始录音和回调复核，不能据此宣告语音、动作或整机验收通过。
