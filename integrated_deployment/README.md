# 灵犀 v1.3.1 完整部署包

版本：v1.3.1-complete-20261023-r16
适用对象：内部部署人员  
目标系统：Ubuntu 22.04 ARM64、ROS 2 Humble、智元 X2 系列机器人

本包把一台新机器人部署为可以通过手机网页使用的灵犀导航产品。客户日常只需要让手机和机器人连接同一个局域网，打开机器人当前 IP 的网页并点击“一键开启全部服务”，不需要电脑常驻运行。

## 包含内容

- 新 AIMDK：aimdk-aarch64-1bde262f-artifacts.7z，版本 aimdk v0.0.0-g1bde262。
- 导航主包：sparkrobot-lingxi-nav-internal-1.0.8-20261023-r16。
- 地图服务、导航 bridge、网页控制台、语音兼容网关和灵心 Java Agent。
- 灵创动作、表情和可选舞蹈资源编排能力：1.0.3-20260902。
- 灵心知识库检索和语音接入：sparkrobot-lingxi-kb-1.0.10-20261023。
- 普通常识、科学原理、语言、历史、观点和开放问题使用模型已有知识正常回答；不因当前对话或知识库没有给出常识资料而拒答。
- 本机机器人专属状态/能力、具体场馆/企业/地点资料和实时事实才要求知识库依据；无命中、低相关或检索超时时不猜测。
- 回答提速：点位列表 10 秒缓存，文本问答复用首轮大模型意图解析答复；本地/实时事实检索与意图解析并行。
- 已知地图点位的明确文本导航命令会先按当前地图语音名称和别名精确匹配，命中后跳过一次意图模型请求；未知或非明确表达仍走原模型路径。
- 当前地图中重名语音别名会触发澄清，不会提交导航；明确文本停止指令在意图模型调用前分流，音频输入仍先经过原有转写链路。
- 低 ASR 置信度时不执行普通命令并提示重说；本地事实检索与意图解析并行，检索队列有界，队列满时串行回退。
- Java Agent 启用 V1.3.8 交互拒识策略：填充词、重复 ASR 片段和播报回声过滤；质量元数据校验；真实 KWS/新鲜唤醒会话门控。
- 语音预置动作：支持 18 种白名单动作，如“请和我握个手”“请双手比心”；只在导航和讲解任务空闲时提交。
- 连续导航卡住修复：1.0.6-20261023-r16，必须最后覆盖导航文件。
- R16 全局路径净空：对 `task_type=2、pnc_mode=0` 使用静态占用地图、机器人实测外接半径和额外障碍净空生成 `reference_line`；未填写实测半径、定位过期或找不到安全路线时拒绝下发。
- 可选流式聊天回复：能力随 KB Java Agent 提供，但 `doubao.streamingReplies=false` 默认关闭；只有目标机器人完成 AIMDK PlayTts 连续分段队列验收后才可手动开启。
- R14 流式 TTS 使用 stream ID 墓碑、派发代次复核和 Java owner token；普通新回复也可接管旧流 owner，旧流失去所有权后不会继续提交后续段或回放完整旧答复。已调用 `call_async` 提交到 PlayTts 的请求，本桥接层不具备撤回或停止能力；`stream_action=end` 仅关闭软件流状态。
- 中文部署教程和客户日常使用手册。

完整包内部已经包含 AIMDK 和导航主包，不需要另外下载或上传这两个组件。components/ 中的副本用于校验和安装，不能删掉。

## 不包含的内容

包内没有灵心 AppSecret、豆包 API Key、SSH 私钥、知识库密钥或直播项目配置。安装时交互填写的密钥只写入机器人受保护配置，不会进入归档。不要把直播项目目录或其语音配置复制到本包。

## 最短部署流程

以下流程必须按顺序执行。外层安装器由机器人 run 用户执行；AIMDK 编译和替换由 agi 用户执行。

### 1. Windows 上传

在保存本包的 Windows PowerShell 中，把 ROBOT_IP 替换为现场机器人 IP：

~~~powershell
$RobotIp = "172.20.10.2"
$Package = ".\lingxi-v1.3.1-complete-20261023-r16-deployment.tar.gz"
$Checksum = ".\lingxi-v1.3.1-complete-20261023-r16-deployment.tar.gz.sha256"
scp $Package ("run@" + $RobotIp + ":/home/run/")
scp $Checksum ("run@" + $RobotIp + ":/home/run/")
~~~

### 2. 机器人 run 用户准备环境

~~~bash
cd /home/run
sha256sum -c lingxi-v1.3.1-complete-20261023-r16-deployment.tar.gz.sha256
tar -xzf lingxi-v1.3.1-complete-20261023-r16-deployment.tar.gz
cd lingxi-v1.3.1-complete-20261023-r16-deployment
chmod +x install_all.sh control_all.sh
bash ./install_all.sh prepare
~~~

看到校验失败时不要继续安装。prepare 会安装编译依赖，并把 AIMDK 文件、AIMDK 脚本和导航安装脚本准备到 /home/agi、/home/run。

### 3. 机器人 agi 用户替换 AIMDK

用新的 SSH 会话登录 agi（不要假设 run 可以切换用户）：

~~~bash
ssh agi@ROBOT_IP
cd /home/agi
bash ./01-replace-aimdk.sh
~~~

脚本会先编译和验证新 AIMDK，成功后才删除旧 /home/agi/aimdk。完成后退出 agi 会话。

### 4. run 用户安装导航主包

~~~bash
cd /home/run/lingxi-v1.3.1-complete-20261023-r16-deployment
bash ./install_all.sh install-navigation
~~~

按提示填写同一灵心应用的 App ID、AppKey、AppSecret、豆包 API Key、模型、机器人名称和实际 SOC2 地址。安装器会删除临时 secrets.env，不要手动把密钥写进包目录。

### 5. 安装灵创、知识库和连续导航修复

~~~bash
bash ./install_all.sh install-addons --agent-code AI_AGENT_xxxxxxxx
~~~

把 AI_AGENT_xxxxxxxx 替换为客户实际的灵心智能体 Code。安装器固定按以下顺序执行：灵创文件 -> 知识库服务和 Java Agent -> 连续导航修复 -> 网页控制台重启。最后一步保证网页能读取新的动作、表情和舞蹈选项，同时不会让机器人执行真实舞蹈或导航。

### 6. 无移动健康检查

~~~bash
bash ./install_all.sh doctor
sudo sparkrobot-lingxi-complete status
sudo sparkrobot-lingxi-complete doctor
~~~

doctor 只检查服务、文件、ROS/AIMDK 导入、资源和权限，不会发送导航、重定位、语音或动作指令。

## 使用入口

手机与机器人连接同一个局域网后打开：

~~~text
http://机器人当前IP:3000/console/
~~~

客户在网页中可以选择地图、查看和编辑点位、根据机器人实际位置自动重定位、启动/停止导航、配置讲解任务，以及在点位讲解和普通动作完成后选择灵创舞蹈。机器人更换网络后 IP 可能变化，网页地址也应使用新的 IP；本包不固定某一台机器人的地址。

## 文档

- 完整部署教程：docs/灵犀完整部署教程-v1.3.1-complete.md。内容包括上传、安装、验收、排障和回滚。
- 已有 AIMDK 部署说明：docs/已有AIMDK部署说明-v1.3.1-r16.md。该路径保留现有 `/home/agi/aimdk`，跳过 AIMDK 替换。
- 客户日常使用手册：docs/客户日常使用手册-v1.3.1.md。面向没有电脑基础的客户。

## 交付前检查

在开发电脑上按顺序构建并审计。完整包构建器只打包已经生成的 KB ZIP，不会自动重编 Java Agent：

~~~powershell
python .\knowledge_base_deployment\build_package.py
python .\knowledge_base_deployment\audit_package.py
python .\integrated_deployment\build_package.py
python .\integrated_deployment\audit_package.py
~~~

构建机需要 Python 3 和 JDK 17（`java`、`javac`、`jar` 在 PATH）。KB 构建器会先重编 Java Agent 并运行拒识、预置动作、回答策略和请求耗时回归测试，再生成 KB ZIP；随后审计 KB 并生成、审计完整包。

交付给 Linux 机器人优先使用：

~~~text
integrated_deployment/dist/lingxi-v1.3.1-complete-20261023-r16-deployment.tar.gz
integrated_deployment/dist/lingxi-v1.3.1-complete-20261023-r16-deployment.tar.gz.sha256
~~~

本次外层交付只生成 TAR.GZ 和对应 `.sha256`；内部基础套件 ZIP 仅作为 components/ 中的安装组件和一致性校验副本保留。每台新机器人都必须独立完成定位、急停、短距离导航和语音验收；离线审计不能替代现场安全验收。

语音拒识默认在兼容模式下接受缺失的声学质量元数据，但会校验已提供的值；唤醒门控默认 enforce，需要真实 SDK KWS/状态事件或可核验的新鲜 SOC2 唤醒证据。该策略是输入门控，不包含端侧 RNNoise、AEC 或实际 DOA 音频滤波；声学链路需由现场单独验收。

语音预置动作包含挥手、握手、举手、飞吻、比心、击掌、敬礼、平举、拥抱、加油、鼓掌、双手打叉、胸前挥手、鞠躬、拜拜、动感光波、挠头和抓屁股。仅明确祈使口令会触发；动作问句、否定、引用/转述和条件表达拒识，直播弹幕不触发。导航中或讲解进行/暂停时不会下发动作；语音回复只表示请求已提交，不代表动作执行完成。

## 语音回答策略

普通问答由首轮意图解析大模型生成并复用；首轮答复为空时才调用聊天兜底。普通常识、科学原理、语言、历史、观点和开放问题使用模型已有知识正常回答，不要求知识库命中。只有本机机器人专属状态/能力、具体场馆/企业/地点资料和实时事实才要求检索依据；低于 0.60 分或超时会明确表示无法核实。该边界降低无依据现场事实的风险，不等同于零幻觉保证。明确文本停止指令会在意图模型调用前本地分流；低 ASR 置信度的普通命令会提示重说。本地导航快速路径只优化已经得到的识别文本；原始音频仍需调用当前配置的转写/意图模型。可选流式回复默认关闭，不改变原有 800 ms 播报等待。完整策略和延迟日志见部署教程第 10.4 至 10.7、15 节。

## R14 流式会话保护

流式回复默认关闭，不改变原有 800 ms 播报等待。启用前仍须在目标机器人验证 AIMDK PlayTts 连续请求的排队、打断和恢复。

R14 通过 stream ID 墓碑阻止已停止流的迟到首段复活；PlayTts 服务等待结束后、真正派发前再次核对流代次。Java TTS owner token 防止旧回调释放新请求状态或覆盖新播报；普通新回复也可取得 owner 并使旧流后续段及完整答复回退失效。只有仍持有播报权的会话才可在续段失败时回退完整答复。

停止或抢占先于 `call_async` 提交时，旧段会被拦截；已调用 `call_async` 提交到 PlayTts 的请求，本桥接层不具备撤回或停止能力。`stream_action=end` 仅关闭软件流状态，不代表取消已排队或正在播放的机器人语音。

升级不会覆盖已有 Java Agent properties；若历史配置手动开启流式回复，该值会保留。未完成目标机器人验收时应恢复为 `false`。

## 已知点位导航快速路径

文本识别结果只有在命令明确、目标与当前活动地图点位语音名称或别名精确匹配且没有别名歧义时，才会绕过意图模型并直接提交导航。歧义时机器人只追问目标，不下发导航；部分匹配、未知地点、疑问、否定、条件或引用表达不会走本地快速路径。

这不是离线语音识别：原始音频仍由当前配置的模型端点转写；本版不能保证音频不发送到云端。具体边界见完整部署教程第 10.5 节。

## R16 全局路径净空

R16 修复的是 V1.3.1 的全局路径贴近静态障碍物问题，不涉及 V1.3.9。对 `task_type=2、pnc_mode=0` 的全局导航，导航客户端会读取当前地图的静态占用地图，按机器人实测外接半径加额外障碍净空膨胀障碍物，并生成 `reference_line` 后再提交 AIMDK PNC。这样可以让全局路线在进入局部避障触发区之前绕开静态障碍，而不是只依赖局部避障临时处理。

在网页“全局路径安全”区域填写“机器人实测外接半径（米）”，默认“额外障碍净空（米）”为 `0.10`，默认“地图占用灰度阈值”为 `205`，保存后再做导航验收。半径必须按机器人实际外接轮廓测量；未填写半径时，R16 会拒绝下发这类真实全局导航。`target_pose_radius` 只是到达判定容差，不是障碍物安全距离。

若定位数据过期、起点或终点已落入膨胀障碍物、静态地图不可读或找不到安全参考线，系统会拒绝下发并记录原因，不会退回未经净空保护的全局路线。该保护不替代现场地图、定位、底盘 PNC 和急停验收。
