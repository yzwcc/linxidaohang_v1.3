# Lingxi Robot Navigation Java Adapter

## v1.4.0 接入要求

- Java 运行时必须为 17 或更高版本。
- SDK 使用 `linksoul-agentsdk-1.4.0-SNAPSHOT.jar`。
- 网关为 `wss://open.agibot.com/api/V1/open-portal/app/wss/agent-sdk`。
- 必须配置灵心开放平台应用的 `appId`、`appKey` 和 `appSecret`。
- v1.3.0 及更早版本的 JWT 凭证不能继续用于 v1.4.0。

这个项目是灵心/LinkSoul AgentSDK v1.4 的 Java 接入层。当前稳定方案以 `audio2tts` 为主链路：

```text
机器人麦克风音频
-> 灵心平台/AgentSDK 负责设备连接、音频转发和 VAD 分段
-> Java Audio2Tts 回调收到 flag=0/1/2 原始音频流
-> Java 拼接完整音频并调用豆包做音频理解、意图判断和回复生成
-> 导航意图调用机器人上的 PythonBridge /nav/start
-> 回复文本调用机器人上的 PythonBridge /tts/speak
-> PythonBridge 调 AimDK ROS2 PlayTts
-> 机器人本地 TTS 播报
-> Java 向灵心平台上报 ASR final、LLM done、TTS done 状态
```

当前不让灵心平台播同一段回复，避免和机器人本地 TTS 混播。`asr2llm` 仍然注册，用作平台直接下发 ASR 文本时的兼容入口，但当前网页对话调试实际走的是 `audio2tts`。

## 当前配置

运行配置文件是：

```text
config/lingxi-nav.properties
```

模板文件是：

```text
config/lingxi-nav.properties.example
```

当前推荐值：

```properties
lingxi.callbacks=asr2llm,audio2tts
nav.baseUrl=http://127.0.0.1:18080
tts.mode=robot
tts.suppressLingxiText=true
tts.fixedDialogTest=false
tts.delayBeforeSpeakMs=800
```

含义：

```text
lingxi.callbacks=asr2llm,audio2tts
  注册 Asr2Llm 和 Audio2Tts。当前主链路是 Audio2Tts；Asr2Llm 只在灵心平台直接下发 ASR 文本时使用。

nav.baseUrl=http://127.0.0.1:18080
  机器人端 PythonBridge 地址。

tts.mode=robot
  Java 调 PythonBridge 的 /tts/speak，由机器人本地 AimDK PlayTts 播报。

tts.suppressLingxiText=true
  Java 不把 LLM 文本流式返回给灵心播报，只上报 LLM done 状态，避免双播。

tts.fixedDialogTest=false
  正常使用模型回复。只有排查 TTS 时才改成 true。

tts.delayBeforeSpeakMs=800
  LLM 结束后等待 800ms 再调用机器人本地 TTS，减少和灵心回合结束时序冲突。
```

## 需要保留的文件

```text
pom.xml
linksoul-agentsdk-1.4.0-SNAPSHOT.jar
config/lingxi-nav.properties.example
src/main/java/com/agibot/aiem/nav/*.java
src/main/resources/logback.xml
scripts/run-java.ps1
docs/LinkSoul-AgentSdk_v1.1.0_260312.pdf
docs/20s交互方案.md
```

说明：

```text
linksoul-agentsdk-1.4.0-SNAPSHOT.jar
  必须保留在根目录。pom.xml 通过 systemPath 引用它，Java 启动命令也会把它放进 classpath。

PythonBridge
  机器人端 HTTP -> ROS2 桥已经移到独立仓库：
  C:\Users\lenovo\Desktop\robot_python_bridge
  Java 只通过 nav.baseUrl 调它，不再维护本仓库里的 python_bridge 旧拷贝。

灵犀 AgentSDK v1.4 官方文档
  https://open.agibot.com/docs/linksoul/agentsdk_docs/java/v140/zh

target/
  Maven 构建产物。可以删除，重新 mvn package 会生成。

logs/
  本地运行日志。可以删除。
```

## 启动前检查

机器人端需要先从独立仓库启动新版 PythonBridge：

```powershell
cd C:\Users\lenovo\Desktop\robot_python_bridge
python -m robot_local_navigation_v3.lingxi_nav_bridge
```

确认它监听：

```text
http://10.23.64.109:18080
```

在本机检查：

```powershell
curl http://10.23.64.109:18080/points
```

测试机器人本地 TTS：

```powershell
curl -X POST http://10.23.64.109:18080/tts/speak `
  -H "Content-Type: application/json" `
  -d '{"event_id":"java_readme_test","text":"robot local tts test","interrupt":true,"force":true}'
```

如果这句能播，说明 Java 到机器人本地 TTS 的链路是通的。

## 启动 Java

推荐直接用已打包 jar 启动：

```powershell
cd C:\Users\lenovo\Desktop\robot_local_navigation_java

java -cp "target\robot-local-navigation-java-0.1.0-all.jar;linksoul-agentsdk-1.4.0-SNAPSHOT.jar" `
  com.agibot.aiem.nav.LingxiNavAgentApp `
  --config config\lingxi-nav.properties
```

也可以用脚本：

```powershell
cd C:\Users\lenovo\Desktop\robot_local_navigation_java
.\scripts\run-java.ps1
```

启动成功后，日志应出现：

```text
Lingxi auth state: appId=APP..., code=0
Audio2Llm callback disabled by lingxi.callbacks=[asr2llm, audio2tts]
Asr2Tts callback disabled by lingxi.callbacks=[asr2llm, audio2tts]
Lingxi navigation agent started. navBaseUrl=http://10.23.64.109:18080, ttsMode=robot, callbacks=[asr2llm, audio2tts]
```

## 停止 Java

PowerShell 里查进程：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*LingxiNavAgentApp*' } |
  Select-Object ProcessId, CommandLine
```

停止：

```powershell
Stop-Process -Id <PID> -Force
```

## 当前 Java 逻辑

入口：

```text
src/main/java/com/agibot/aiem/nav/LingxiNavAgentApp.java
```

主要流程：

```text
1. AgentSDK 连接灵心 WebSocket。
2. 注册 asr2llm 和 audio2tts 回调。
3. 当前网页调试通常进入 audio2tts：收到 flag=0/1/2 原始音频流。
4. Java 按 eventId 拼接完整音频，在 flag=2 后调用 DoubaoArkClient.parseInteractionAudio。
5. 豆包输出 NavigationIntent，包括 intent、target_name、question、reply。
6. intent=navigate 时调用 NavBridgeClient.startNavigation。
7. intent=stop_navigation 时调用 NavBridgeClient.stopNavigation。
8. 普通聊天只生成 reply，不调用导航。
9. tts.mode=robot 时调用 NavBridgeClient.speak。
10. tts.suppressLingxiText=true 时，不把 reply 文本返回给灵心播报，只上报 done 状态。
```

如果灵心平台直接下发 ASR 文本，则进入 `asr2llm`：

```text
灵心 ASR 文本
-> Java Asr2Llm 回调收到 text
-> Java 调 DoubaoArkClient.parseInteraction
-> 后续导航/TTS 逻辑与 audio2tts 相同
```

意图返回结构由豆包生成，核心字段是：

```json
{
  "intent": "navigate",
  "target_name": "实验室A点",
  "reply": "好的，我带你去实验室A点"
}
```

## 播报保护

Java 侧有一层 TTS 保护，避免机器人重复说话：

```text
普通回复：
  - 去重
  - 至少间隔 3 秒
  - 同时只允许一个普通 TTS 请求

导航/急停回复：
  - 高优先级
  - 可以跳过去重和普通节流

静默窗口：
  - 机器人 TTS 请求 accepted 后，短时间忽略新的普通 ASR 回调
  - 用来减少机器人把自己声音识别成用户输入
  - 急停类指令仍允许处理
```

## 联调判断

Java 日志看到：

```text
Audio2Tts request: ... flag=0
Audio2Tts request: ... flag=2
Audio2Tts final: eventId=..., asrText=..., reply=..., priority=...
Robot bridge /tts/speak response: httpStatus=200, body={"ok": true, "accepted": true, "status": "submitted"}
```

PythonBridge 日志看到：

```text
POST /nav/start HTTP/1.1 200
POST /tts/speak HTTP/1.1 200
TTS request accepted: ...
```

说明链路已经走到机器人本地服务。

## 不再作为当前方案使用的内容

当前稳定方案不使用：

```text
audio2llm
asr2tts
video callbacks
Java 直接写 ROS2
Java 生成 TTS 音频并回传灵心
灵心和机器人本地 TTS 双播
```

这些接口代码保留在 Java 中，是为了以后验证 SDK 其他协议时有入口。当前运行配置只注册 `asr2llm,audio2tts`，实际网页调试主链路是 `audio2tts`。
