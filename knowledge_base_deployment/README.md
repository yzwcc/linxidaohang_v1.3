# 灵心知识库检索与语音接入部署包

版本：`sparkrobot-lingxi-kb-1.0.5-20261023`

本包包含两部分：独立 Retriever Proxy 服务，以及已验证的 Java Agent 知识库接入更新。它不包含导航、地图、AIMDK 或直播项目配置。

完整部署步骤见 `docs/灵心知识库独立部署教程.md`。

## 功能

- 通过 `POST /search` 调用灵心 `agent_kb` 检索接口。
- 使用灵心要求的 AK/SK HMAC-SHA256 请求签名。
- `GET /health` 健康检查。
- `kb-search` 命令行查询。
- 独立 systemd 服务，监听机器人本机 `127.0.0.1:18084`。
- Java Agent 对本地/实时事实使用知识库支撑回答；普通聊天复用意图解析的首轮答复。
- Java Agent 集成 V1.3.8 输入拒识策略：填充词/重复片段/播报回声过滤、可用质量元数据校验和真实唤醒会话门控。
- Java Agent 支持 18 种明确语音祈使口令的预置动作识别，并对问句、否定、引用和条件表达拒识。
- Java Agent 更新前自动备份，可从备份 JAR 回滚。

## 在已部署灵犀导航的机器人上安装

在机器人 `run` 账号执行：

```bash
cd /home/run/sparkrobot-lingxi-kb-1.0.5-20261023
sudo ./install.sh --from-lingxi --agent-code AI_AGENT_你的智能体Code
sudo ./install_voice_agent.sh
```

`--from-lingxi` 只读取 `/etc/sparkrobot/lingxi-nav/lingxi-nav.properties` 的 `lingxi.appId/appKey/appSecret`，不会修改该文件。智能体 Code 可从灵心平台或现有 Java 日志中的 `AI_AGENT_...` 确认。知识库服务必须先安装，再安装 `install_voice_agent.sh`。

如果不希望复用现有灵心配置，也可以准备只包含以下字段的临时配置文件：

```text
RETRIEVER_APP_ID=app_xxx
RETRIEVER_APP_KEY=ak_xxx
RETRIEVER_APP_SECRET=sk_xxx
RETRIEVER_AGENT_CODE=AI_AGENT_xxx
```

然后执行：

```bash
sudo ./install.sh --config ./retriever.env
```

安装后检查：

```bash
sudo systemctl status sparkrobot-lingxi-kb.service --no-pager
curl -fsS http://127.0.0.1:18084/health
kb-search "这里填写要检索的问题"
```

## 查询接口

```bash
curl -X POST http://127.0.0.1:18084/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"这里填写要检索的问题","top_k":5}'
```

返回上游 `data` 中的文档片段、问答、分数和元数据。服务端会固定使用配置中的 Agent Code，客户端不能覆盖，避免检索到其他智能体的知识库。

## 语音接入回滚

安装语音接入时会把旧 JAR 保存到：

```text
/var/lib/sparkrobot/lingxi-kb/agent-backups/
```

查看备份并回滚：

```bash
ls -lh /var/lib/sparkrobot/lingxi-kb/agent-backups/
sudo ./install_voice_agent.sh --rollback /var/lib/sparkrobot/lingxi-kb/agent-backups/robot-local-navigation-java-all.实际备份文件名.jar
```

回滚只重启 `lingxi-java-agent-robot.service`，不重启导航服务。

## 运维

```bash
sudo systemctl restart sparkrobot-lingxi-kb.service
sudo journalctl -u sparkrobot-lingxi-kb.service -n 100 --no-pager
sudo /usr/local/sbin/sparkrobot-lingxi-kb doctor
```

密钥保存在 `/etc/sparkrobot/lingxi-kb/retriever.env`，权限为 `run:run 0600`，不会进入安装包或日志。卸载知识库程序但保留配置、状态和日志：

```bash
sudo /usr/local/sbin/sparkrobot-lingxi-kb uninstall
```

## 语音回答速度与准确性

本版对机器人/场馆/企业/地点和实时状态类问题先查知识库。相关度至少 0.60 才使用命中答案；未命中、低分或检索超时则回复无法核实，不让模型补猜。开放式闲聊和一般常识不再多等一次知识库请求。文本 ASR 的意图解析会同时生成最终答复并直接复用，只有该答复为空才额外调用聊天兜底。

点位列表缓存 10 秒；知识库请求最多等待 1.2 秒，关闭 rerank 和 QA 前置检查以减少额外计算。该策略降低无依据回答风险，不保证模型对所有一般知识都绝无错误；重要场馆信息应以维护后的知识库和现场确认为准。

当前未实现模型流式输出或 TTS 分段播放，机器人配置中的原有 800 ms 播报等待保持不变。排查延迟时通过 `sudo journalctl -u lingxi-java-agent-robot.service --since "30 min ago" --no-pager` 检查 `Doubao request completed`、`Knowledge lookup finished`、`Text chat answer source`、`Robot TTS dispatch timing`，并使用同一 `eventId` 对齐阶段。

## 语音交互拒识策略

Java Agent 默认过滤无信息填充词、重复 ASR 片段、播报回声和静音窗口中未唤醒的输入。真实唤醒后 8 秒内可连续交流；离开窗口后需新的 SDK KWS/唤醒状态事件，或能按 event ID 核验且不超过 8 秒的 SOC2 唤醒记录。仅仅在识别文本中说出“你好灵犀”不会伪造唤醒证据。

默认质量门控兼容 SDK 未提供声学元数据的情况；若字段存在，仍执行以下阈值检查：

| 元数据 | 默认门槛 |
| --- | --- |
| DOA 正前方半角 | 60° |
| SNR | 不低于 6 dB |
| AEC 残余回声 RMS | 不高于 0.12 |
| VAD 语音概率 | 不低于 0.70 |
| ASR 置信度 | 不低于 0.65 |

对应的 properties 默认值如下：

```properties
voice.gate.metadataRequired=false
voice.gate.frontHalfAngleDeg=60
voice.gate.minSnrDb=6
voice.gate.maxEchoRms=0.12
voice.gate.minVadProbability=0.70
voice.gate.minAsrConfidence=0.65
voice.gate.wakeMode=enforce
voice.gate.allowAddressedWake=false
voice.gate.wakeSignalMaxAgeMs=8000
voice.gate.sessionWindowMs=8000
```

只有现场 SDK 确实提供并完成标定后，才可设置 `voice.gate.metadataRequired=true`；严格模式会拒绝缺失任一必要元数据的输入。Java Agent 从其 `WorkingDirectory/config/lingxi-nav.properties` 读取 properties，也支持对应的 `VOICE_GATE_*` 环境变量。修改前先用 `systemctl show -p WorkingDirectory --value lingxi-java-agent-robot.service` 确认实际工作目录，不要盲改导航服务配置文件。

这是 ASR/SDK 元数据上的软件拒识策略，不会对 PCM 音频做 RNNoise、AEC 或真实 DOA 滤波，也不能替代机器人硬件急停。现场验收仍须测试正常唤醒、背景噪声、播报回声、侧后方说话与短指令，并由现场人员确认硬件急停有效。

拒识原因可从 `lingxi-java-agent-robot.service` 日志排查；回滚方法见上方“语音接入回滚”，只替换 Java Agent JAR，不删除知识库服务和配置。

### 语音预置动作

支持动作：挥手、握手、举手、飞吻、比心、击掌、敬礼、平举、拥抱、加油、鼓掌、双手打叉、胸前挥手、鞠躬、拜拜、动感光波、挠头、抓屁股。例如可以在正常唤醒后说“请和我握个手”或“请双手比心”。只有导航空闲且没有进行或暂停中的讲解任务时才会提交；问句、否定、引用/转述和条件表达不会触发，直播弹幕不支持触发动作。

机器人回复“已受理/已提交”只表示动作请求进入服务，不保证动作已经完成。此 Java Agent 需要搭配导航增量包 `sparkrobot-lingxi-consecutive-nav-fix-1.0.2-20261022` 才能调用机器人动作桥接接口；单独升级知识库包不会安装导航端接口。
