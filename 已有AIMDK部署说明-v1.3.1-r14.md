# 已有 AIMDK 机器人部署灵犀 V1.3.1 R14

版本：`v1.3.1-complete-20261023-r14`

适用场景：机器人已安装并验证 AIMDK，需要安装或更新灵犀 V1.3.1 导航产品。

目标系统：Ubuntu 22.04 ARM64、ROS 2 Humble、智元 X2 系列机器人。

本流程保留现有 `/home/agi/aimdk`，不替换、不删除 AIMDK。完整包虽包含 AIMDK 安装介质，`prepare` 阶段也会把介质和辅助脚本复制到 `/home/agi`，但本说明不执行 `/home/agi/01-replace-aimdk.sh`；该脚本用于编译并替换 AIMDK。

## 1. 部署前确认现有 AIMDK

先用 Windows PowerShell 登录机器人 `agi` 账号：

```powershell
$RobotIp = "机器人当前IP"
ssh ("agi@" + $RobotIp)
```

在机器人执行：

```bash
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
cat /home/agi/aimdk/version
python3 -c "import rclpy; import aimdk_msgs.msg; print('AIMDK_IMPORT_OK')"
ros2 pkg list | grep '^aimdk_msgs$'
```

本次已验证基线为 `aimdk v0.0.0-g1bde262`。版本文件、Python 导入和 ROS 包查询都应正常；失败时先停止并修复 AIMDK 环境，不要绕过完整包安装器检查。完成后退出：

```bash
exit
```

## 2. 上传 R14 完整包

Windows 文件：

```text
integrated_deployment\dist\lingxi-v1.3.1-complete-20261023-r14-deployment.tar.gz
integrated_deployment\dist\lingxi-v1.3.1-complete-20261023-r14-deployment.tar.gz.sha256
```

在 Windows PowerShell 中上传：

```powershell
$RobotIp = "机器人当前IP"
$Root = "E:\1资料\0火花机器人资料\0二次开发\灵犀导航微信小程序开发"
$Tar = "$Root\integrated_deployment\dist\lingxi-v1.3.1-complete-20261023-r14-deployment.tar.gz"
$Sha = "$($Tar).sha256"
scp $Tar ("run@" + $RobotIp + ":/home/run/")
scp $Sha ("run@" + $RobotIp + ":/home/run/")
ssh ("run@" + $RobotIp)
```

## 3. run 账号校验和准备

在机器人 `run` 账号执行：

```bash
cd /home/run
sha256sum -c lingxi-v1.3.1-complete-20261023-r14-deployment.tar.gz.sha256
tar -xzf lingxi-v1.3.1-complete-20261023-r14-deployment.tar.gz
cd /home/run/lingxi-v1.3.1-complete-20261023-r14-deployment
sha256sum -c SHA256SUMS.txt
chmod +x install_all.sh control_all.sh
bash ./install_all.sh prepare
```

外层校验和包内校验都必须通过。`prepare` 会安装所需系统依赖并准备导航文件；它不会修改 `/home/agi/aimdk`，也不需要为了本流程执行 AIMDK 替换脚本。若看到切换 `agi` 执行替换的提示，请按本说明跳过该操作。

## 4. 再次确认 AIMDK 未改变

新开一个 Windows PowerShell 窗口登录 `agi`：

```powershell
ssh ("agi@" + $RobotIp)
```

重新检查：

```bash
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
cat /home/agi/aimdk/version
python3 -c "import rclpy; import aimdk_msgs.msg; print('AIMDK_IMPORT_OK')"
ros2 pkg list | grep '^aimdk_msgs$'
exit
```

版本和导入应与部署前一致。不要运行 `/home/agi/01-replace-aimdk.sh`，不要手动覆盖 `/home/agi/aimdk`。

## 5. 安装灵犀导航主包

回到 `run` 会话：

```bash
cd /home/run/lingxi-v1.3.1-complete-20261023-r14-deployment
bash ./install_all.sh install-navigation
```

安装器会在干净的 `agi` shell 中检查 ROS、`rclpy` 和 `aimdk_msgs`。检查失败时停止并修复原 AIMDK 环境，不要手动绕过。

按提示填写属于同一灵心应用的 App ID、AppKey、AppSecret、豆包 API Key、模型名称、机器人显示名和机器人实际 SOC2 地址。密钥只在机器人终端交互输入，不要写入命令或部署文件。

安装后确认导航服务：

```bash
readlink -f /opt/sparkrobot/lingxi-nav/current
sudo systemctl is-active lingxi-nav-console.service
curl -fsS http://127.0.0.1:3000/health
```

## 6. 安装知识库、灵创和连续导航修复

使用灵心平台中已绑定知识库的真实智能体 Code：

```bash
bash ./install_all.sh install-addons --agent-code AI_AGENT_实际智能体Code
```

R14 组件版本：

```text
sparkrobot-lingxi-kb-1.0.9-20261023
sparkrobot-lingxi-consecutive-nav-fix-1.0.5-20261023
```

安装器依次安装灵创、知识库服务与 Java Agent、连续导航修复，最后重启网页控制台。连续导航修复必须最后覆盖相关导航文件。该阶段不自动发送导航、重定位、动作或语音请求。

## 7. R14 语音行为和流式回复

R14 保留已知地图点位快速匹配、重名别名澄清、低 ASR 置信度拒识、明确文本停止命令本地分流，以及事实检索与意图解析并行。原始音频仍使用现有转写/意图模型；本版本不含端侧离线 ASR。

可选流式回复默认关闭：

```properties
doubao.streamingReplies=false
```

默认关闭时不改变既有非流式行为或 800 ms 播报等待。仅普通文本闲聊、机器人 TTS 模式且非固定对话测试可能使用流式回复。导航、停止/讲解控制、知识库事实核实、预置动作和原始音频不走该流式路径。

升级安装不会替换保存在服务工作目录中的用户配置。若该机器人此前手动启用过流式回复，先检查 Java Agent 工作目录及配置，保持属性为 `false`：

```bash
sudo systemctl show -p WorkingDirectory --value lingxi-java-agent-robot.service
```

将输出目录用于检查实际配置：

```bash
AGENT_WORKDIR="$(sudo systemctl show -p WorkingDirectory --value lingxi-java-agent-robot.service)"
CONFIG="${AGENT_WORKDIR}/config/lingxi-nav.properties"
sudo test -f "${CONFIG}"
sudo grep -nE '^[[:space:]]*doubao\.streamingReplies[[:space:]]*=' "${CONFIG}" \
  || echo "未显式设置，使用代码默认值 false"
```

若配置为 `true` 且尚未完成目标机器人验收，先备份配置，再将该项改为 `false` 并重启 Java Agent：

```bash
AGENT_WORKDIR="$(sudo systemctl show -p WorkingDirectory --value lingxi-java-agent-robot.service)"
CONFIG="${AGENT_WORKDIR}/config/lingxi-nav.properties"
sudo cp -a "${CONFIG}" "${CONFIG}.r14-$(date +%Y%m%d-%H%M%S).bak"
sudo sed -i -E \
  's/^([[:space:]]*doubao\.streamingReplies[[:space:]]*=[[:space:]]*).*/\1false/' \
  "${CONFIG}"
sudo systemctl restart lingxi-java-agent-robot.service
```

R14 为流式 TTS 增加 stream ID 墓碑和代次复核：停止请求先到时，迟到首段不能复活旧流；PlayTts 服务等待结束后、调用 `call_async` 前会重新验证所有权。新的普通回复也可取得 Java TTS owner token 并使旧流的软件后续段和完整答复回退失效；旧回调不能清理新请求状态或覆盖新播报。只有仍持有播报权的会话才可在续段失败时回退完整答复。

停止或抢占先于 `call_async` 提交时，旧段会被拦截；已调用 `call_async` 提交到 PlayTts 的请求，本桥接层不具备撤回或停止能力。`stream_action=end` 仅关闭软件流状态，不代表取消已排队或正在播放的机器人语音。

这些是软件并发回归结果，不代表 AIMDK PlayTts 连续队列、机器人声学或现场运行已通过验收。只有在目标机器人经过授权的安全维护窗口逐项验证排队、打断、恢复、下一轮语音、导航和讲解后，才考虑手动开启流式回复。出现错序、重复、截断或错误抢占时立即恢复 `false` 并重启 Java Agent。

## 8. 无移动健康检查

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

健康检查不会发送真实导航、重定位、动作或语音请求。手机与机器人连接同一局域网后，可打开：

```text
http://机器人当前IP:3000/console/
```

地图定位、重定位、导航、讲解和动作均属于现场验收；须有人看护、确认周围安全并确保实体急停可用。

## 9. 回滚

查看并回滚最近一次连续导航修复：

```bash
sudo sparkrobot-lingxi-consecutive-nav-fix backups
sudo sparkrobot-lingxi-consecutive-nav-fix rollback latest
```

只回滚 Java Agent 时，先确认知识库组件解压路径和备份 JAR：

```bash
sudo ls -lh /var/lib/sparkrobot/lingxi-kb/agent-backups/
sudo bash /home/run/v1.3.1-complete-deployment-20261023-r14/addons/sparkrobot-lingxi-kb-1.0.9-20261023/install_voice_agent.sh \
  --rollback /var/lib/sparkrobot/lingxi-kb/agent-backups/实际备份文件.jar
```

完整部署教程（含日志、排障和组件回滚）位于包内：

```text
docs/灵犀完整部署教程-v1.3.1-complete.md
```

本说明、软件测试和离线包审计都不代表机器人实机、云端或声学验收已经完成。
