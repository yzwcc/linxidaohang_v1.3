# 已有 AIMDK 机器人部署灵犀 V1.3.1 r13

版本：`v1.3.1-complete-20261023-r13`

适用场景：机器人已经安装并验证 AIMDK，需要首次安装或重新安装灵犀 V1.3.1 导航产品。

目标系统：Ubuntu 22.04 ARM64、ROS 2 Humble、智元 X2 系列机器人。

本路径保留现有 `/home/agi/aimdk`。完整部署包虽然包含 AIMDK 安装介质，但本说明**不执行 AIMDK 替换**。不要运行 `/home/agi/01-replace-aimdk.sh`；该脚本用于编译并替换 AIMDK。

## 1. AIMDK 兼容检查

本部署版本以以下 AIMDK 版本作为验证基线：

```text
aimdk v0.0.0-g1bde262
```

先用 Windows PowerShell 新开 `agi` SSH 会话：

```powershell
$RobotIp = "机器人当前IP"
ssh ("agi@" + $RobotIp)
```

在机器人 `agi` 账号执行：

```bash
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
cat /home/agi/aimdk/version
python3 -c "import rclpy; import aimdk_msgs.msg; print('AIMDK_IMPORT_OK')"
ros2 pkg list | grep '^aimdk_msgs$'
```

版本、导入和 ROS 包查询都应正常。版本与验证基线不同、版本文件缺失或导入失败时，先停止部署并确认兼容性；不要绕过安装器检查。验证完成后退出：

```bash
exit
```

## 2. 上传 R13 完整包

Windows 本地文件：

```text
integrated_deployment\dist\lingxi-v1.3.1-complete-20261023-r13-deployment.tar.gz
integrated_deployment\dist\lingxi-v1.3.1-complete-20261023-r13-deployment.tar.gz.sha256
```

在 Windows PowerShell 中替换机器人 IP 并上传：

```powershell
$RobotIp = "机器人当前IP"
$Root = "E:\1资料\0火花机器人资料\0二次开发\灵犀导航微信小程序开发"
$Tar = "$Root\integrated_deployment\dist\lingxi-v1.3.1-complete-20261023-r13-deployment.tar.gz"
$Sha = "$($Tar).sha256"
scp $Tar ("run@" + $RobotIp + ":/home/run/")
scp $Sha ("run@" + $RobotIp + ":/home/run/")
ssh ("run@" + $RobotIp)
```

不要使用旧版 R10、R11、R12 的校验文件或解压目录。

## 3. run 账号校验并准备

在机器人 `run` 账号执行：

```bash
cd /home/run
sha256sum -c lingxi-v1.3.1-complete-20261023-r13-deployment.tar.gz.sha256
tar -xzf lingxi-v1.3.1-complete-20261023-r13-deployment.tar.gz
cd /home/run/lingxi-v1.3.1-complete-20261023-r13-deployment
sha256sum -c SHA256SUMS.txt
chmod +x install_all.sh control_all.sh
bash ./install_all.sh prepare
```

两次 SHA-256 检查都必须通过。`prepare` 会检查 ARM64、校验包内组件、准备依赖和部署文件；它不会替换或删除现有 `/home/agi/aimdk`。完成后安装器可能提示切换 `agi` 执行 AIMDK 替换脚本；本说明路径应跳过该脚本。

## 4. 再次验证 AIMDK

为确认 `prepare` 未影响既有 AIMDK，从 Windows PowerShell 新开 `agi` 会话：

```powershell
ssh ("agi@" + $RobotIp)
```

再次执行：

```bash
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
cat /home/agi/aimdk/version
python3 -c "import rclpy; import aimdk_msgs.msg; print('AIMDK_IMPORT_OK')"
ros2 pkg list | grep '^aimdk_msgs$'
```

确认正常后退出 `agi` 会话。不要执行 `/home/agi/01-replace-aimdk.sh`，也不要手动替换 `/home/agi/aimdk`。

## 5. 安装导航主包

回到机器人 `run` 会话：

```bash
cd /home/run/lingxi-v1.3.1-complete-20261023-r13-deployment
bash ./install_all.sh install-navigation
```

安装器会在干净的 `agi` shell 中确认 ROS、`rclpy` 和 `aimdk_msgs` 可用。验证失败时停止并先修复 AIMDK 环境，不要手动绕过。

按提示填写属于同一灵心应用的 App ID、AppKey、AppSecret、豆包 API Key、模型名称、机器人显示名和实际 SOC2 地址。密钥只在机器人终端交互输入，不要写入命令、文档或部署包。安装后可检查：

```bash
readlink -f /opt/sparkrobot/lingxi-nav/current
sudo systemctl is-active lingxi-nav-console.service
curl -fsS http://127.0.0.1:3000/health
```

## 6. 安装知识库、灵创和连续导航修复

使用灵心平台中绑定知识库的真实 Agent Code：

```bash
bash ./install_all.sh install-addons --agent-code AI_AGENT_实际智能体Code
```

安装器依序安装灵创、知识库服务和 Java Agent、连续导航修复，最后重启网页控制台。R13 组件版本：

```text
sparkrobot-lingxi-kb-1.0.8-20261023
sparkrobot-lingxi-consecutive-nav-fix-1.0.4-20261023
```

连续导航修复必须最后覆盖导航文件。该阶段不会自动发送导航、重定位、语音或动作请求。

## 7. 无移动健康检查

```bash
bash ./install_all.sh doctor
sudo sparkrobot-lingxi-complete status
sudo sparkrobot-lingxi-complete doctor
sudo systemctl is-active lingxi-nav-console.service
sudo systemctl is-active lingxi-java-agent-robot.service
sudo systemctl is-active sparkrobot-lingxi-kb.service
curl -fsS http://127.0.0.1:3000/health
curl -fsS http://127.0.0.1:18084/health
```

健康检查通过后，手机和机器人连接同一局域网，在浏览器打开：

```text
http://机器人当前IP:3000/console/
```

选择当前地图并确认持续定位数据。重定位、导航、讲解和动作会让机器人移动，必须由现场人员看护、确认周边安全并确保实体急停可用后逐项验收。

## 8. R13 语音行为与流式回复

R13 保留已知地图点位精确匹配、重复别名澄清、低 ASR 置信度拒识、明确文本停止指令本地分流，以及事实检索与意图解析并行。原始音频仍使用现有转写/意图模型；本版本不包含端侧离线 ASR。

可选流式模型回复和有序 TTS 分段默认关闭：

```properties
doubao.streamingReplies=false
```

默认状态不改变原有非流式行为和 800 ms 播报等待。只有普通文本闲聊、`tts.mode=robot` 且非固定对话测试才可能进入流式路径；知识库/事实问题、导航、停止/讲解任务控制、预置动作及原始音频不会使用它。

在目标 AIMDK 机器人验证 PlayTts 连续请求的排队、打断和恢复之前，不要将属性改为 `true`。开启后第一段使用 `interrupt`，后续段按序提交且不打断前段；首段前发生模型流错误会回退到普通请求，已播出内容后模型流中断会提示用户重问，TTS 续段失败则尝试以完整最终答复替换部分播报。若经过现场授权后试用，先确认 Java Agent 工作目录：

```bash
sudo systemctl show -p WorkingDirectory --value lingxi-java-agent-robot.service
```

备份并编辑该目录下 `config/lingxi-nav.properties`，设置 `doubao.streamingReplies=true`，再重启 `lingxi-java-agent-robot.service`。依次测试正常闲聊、模型 503 回退、模型流中断、TTS 续段拒绝、下一轮语音，以及导航/讲解不受影响。发现错序、重复、截断或错误打断时恢复 `false` 并重启服务。构建机假服务测试不能替代 AIMDK 实机队列或声学验收。

## 9. 文档和回滚

完整新机部署、现场验收、日志和故障处理见包内：

```text
docs/灵犀完整部署教程-v1.3.1-complete.md
```

如果 Java Agent 流式试验异常，优先恢复 `doubao.streamingReplies=false` 并重启 Java Agent。知识库 Java Agent 还可按包内 `install_voice_agent.sh --rollback <备份JAR>` 指引仅回滚 JAR；连续导航修复可用以下命令查看备份并回滚：

```bash
sudo sparkrobot-lingxi-consecutive-nav-fix backups
sudo sparkrobot-lingxi-consecutive-nav-fix rollback latest
```

本说明和离线审计不代表已在机器人现场部署或完成物理/声学验收。
