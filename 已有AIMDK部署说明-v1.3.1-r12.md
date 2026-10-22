# 已有 AIMDK 机器人部署灵犀 V1.3.1 r12

版本：`v1.3.1-complete-20261023-r12`

适用场景：机器人已安装 AIMDK，首次安装本套灵犀导航，或在备份后重新安装灵犀导航。

目标系统：Ubuntu 22.04 ARM64、ROS 2 Humble、智元 X2 系列机器人。

此路径保留现有 `/home/agi/aimdk`。完整部署包虽包含 AIMDK 安装介质，按本说明部署时**不要运行 AIMDK 替换脚本**。

## 1. AIMDK 兼容条件

本套完整包验证过的 AIMDK 版本为：

```text
aimdk v0.0.0-g1bde262
```

继续前需确认以下文件和导入均正常：

```bash
/home/agi/aimdk/install/setup.bash
```

```bash
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
cat /home/agi/aimdk/version
python3 -c "import rclpy; import aimdk_msgs.msg; print('AIMDK_IMPORT_OK')"
ros2 pkg list | grep '^aimdk_msgs$'
```

如果 AIMDK 版本不同、版本文件缺失或导入失败，先停止部署并确认该版本兼容性。不要通过跳过安装器检查强行安装。

## 2. 部署文件

Windows 本地完整包：

```text
integrated_deployment\dist\lingxi-v1.3.1-complete-20261023-r12-deployment.tar.gz
```

同目录提供 `.sha256` 校验文件。每次交付均以随包校验文件和包内 `SHA256SUMS.txt` 为准，不要复用其他版本文档里的哈希。密钥不包含在包内；部署时只在机器人终端交互填写。

## 3. 上传完整包

在 Windows PowerShell 中，把 `$RobotIp` 替换为现场机器人当前 IP：

```powershell
$RobotIp = "机器人当前IP"
$Root = "E:\1资料\0火花机器人资料\0二次开发\灵犀导航微信小程序开发"
$Tar = "$Root\integrated_deployment\dist\lingxi-v1.3.1-complete-20261023-r12-deployment.tar.gz"
$Sha = "$($Tar).sha256"
scp $Tar ("run@" + $RobotIp + ":/home/run/")
scp $Sha ("run@" + $RobotIp + ":/home/run/")
ssh ("run@" + $RobotIp)
```

## 4. run 账号校验和准备环境

在机器人 `run` 账号执行：

```bash
cd /home/run
sha256sum -c lingxi-v1.3.1-complete-20261023-r12-deployment.tar.gz.sha256
tar -xzf lingxi-v1.3.1-complete-20261023-r12-deployment.tar.gz
cd /home/run/lingxi-v1.3.1-complete-20261023-r12-deployment
sha256sum -c SHA256SUMS.txt
chmod +x install_all.sh control_all.sh
```

两次校验均必须通过。任何一项失败都不要继续。

运行准备阶段：

```bash
bash ./install_all.sh prepare
```

`prepare` 会检查 ARM64、校验组件、安装系统/编译依赖，并准备导航文件及 AIMDK 安装介质。它不会替换或删除 `/home/agi/aimdk`。完成后安装器会提示运行 AIMDK 替换脚本；在本说明的保留路径中跳过该脚本。

## 5. 以 agi 账号验证现有 AIMDK

从 Windows PowerShell 新开 `agi` SSH 会话：

```powershell
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

版本应为 `aimdk v0.0.0-g1bde262`，Python 命令应输出 `AIMDK_IMPORT_OK`，ROS 包列表应包含 `aimdk_msgs`。然后退出 agi 会话：

```bash
exit
```

**不要执行** `/home/agi/01-replace-aimdk.sh`。该脚本用于编译并替换 AIMDK，不属于保留现有 AIMDK 的部署路径。

## 6. 安装导航主包和附加能力

回到机器人 `run` 会话：

```bash
cd /home/run/lingxi-v1.3.1-complete-20261023-r12-deployment
bash ./install_all.sh install-navigation
```

安装器会在干净的 agi shell 中再次检查 ROS、`rclpy` 和 `aimdk_msgs`。检查失败时会停止；不要手动绕过。

按提示输入属于同一灵心应用的 App ID、AppKey、AppSecret、豆包 API Key、模型、机器人名称和实际 SOC2 地址。然后安装附加能力：

```bash
bash ./install_all.sh install-addons --agent-code AI_AGENT_实际智能体Code
```

将 `AI_AGENT_实际智能体Code` 替换成灵心平台中绑定知识库的真实 Agent Code。安装器按顺序安装灵创、知识库和 Java Agent，最后安装连续导航修复并重启网页控制台。不要将密钥写入命令、文档或部署包。

本包使用 `sparkrobot-lingxi-kb-1.0.7-20261023` 和 `sparkrobot-lingxi-consecutive-nav-fix-1.0.3-20261023`；连续导航修复是最后安装的覆盖组件。

## 7. 无移动检查和现场验收

先执行不发送移动指令的健康检查：

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

手机和机器人连接同一局域网后访问：

```text
http://机器人当前IP:3000/console/
```

定位、重定位、导航、讲解和动作会影响机器人运动，必须由现场人员看护并确认实体急停可用后逐项验收。离线构建和包审计不等于机器人现场验收。

## 8. r12 语音导航行为

识别文本中的明确导航命令只有在目标与当前活动地图的完整语音点位名称或别名精确匹配时，才会跳过一次意图模型请求。部分匹配、未知地点、疑问、否定、条件和引用表达继续走原模型路径；导航 bridge 仍会复核活动地图和目标。

如果同一地图中的多个点位使用相同语音名称或别名，机器人会询问具体目标，不会提交导航。明确的文本“停止导航/停止讲解”命令会在意图模型调用前本地分流；语音仍须先经过现有音频转写，再识别停止命令。原始音频仍先调用当前配置的 Doubao 转写/意图模型，随后才尝试本地命令路由。r12 不包含端侧离线 ASR，也不保证原始音频不经过灵心/云端。

ASR 置信度低时，普通命令（包括开始或继续讲解）不会执行；机器人 TTS 模式会提示用户重说。明确停止命令仍优先处理。文本事实问题会并行启动意图解析和知识库检索；检索最多使用 2 个工作线程和 4 个排队位，队列满时回退为意图解析后串行检索。该并发只重叠等待时间，不保证固定提速。模型流式输出/TTS 分段播放未启用，现有 800 ms 播报等待未改。

完整部署步骤、故障处理和组件回滚命令见包内：

```text
docs/灵犀完整部署教程-v1.3.1-complete.md
```
