# 已有 AIMDK 机器人部署灵犀 V1.3.1 R18

适用场景：机器人已有 AIMDK，保留现有 `/home/agi/aimdk`，只升级灵犀完整套件至 R18。

目标系统：Ubuntu 22.04 ARM64、ROS 2 Humble、智元 X2 系列机器人。

## AIMDK 版本门槛

R18 的导航客户端按随包 SDK `aimdk v1.1.0-gfaf4c94` 对接。仅仅能够导入 `aimdk_msgs` 不足以证明接口兼容。跳过 AIMDK 替换前，必须满足：

- `/home/agi/aimdk/version` 精确为 `aimdk v1.1.0-gfaf4c94`；
- `PncTaskRequest` 包含 `reference_line`、`target_pose_radius` 等导航字段；
- `PncTaskResponse` 包含 `task_status`；
- ROS 2 Humble 环境下 Python 导入和 `ros2 pkg list` 均通过。

登录 `agi` 检查：

```bash
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
test "$(cat /home/agi/aimdk/version)" = "aimdk v1.1.0-gfaf4c94"
python3 - <<'PY'
from aimdk_msgs.msg import PncTaskRequest, PncTaskResponse
request = PncTaskRequest()
required = (
    "task_type", "task_request", "pnc_mode", "task_id", "map_id",
    "target_pose", "reference_line", "target_pose_radius", "max_forward_speed",
)
missing = [name for name in required if not hasattr(request, name)]
if missing or not hasattr(PncTaskResponse(), "task_status"):
    raise SystemExit(f"AIMDK PNC 接口不兼容：{missing}")
print("AIMDK_R18_CONTRACT_OK")
PY
ros2 pkg list | grep -qx aimdk_msgs
```

若版本不是 v1.1.0，即使导入成功也不要跳过替换。按完整教程先运行 `prepare`，再登录 `agi` 执行 `/home/agi/01-replace-aimdk.sh`。该脚本用于编译并替换 AIMDK；脚本先校验、编译和验证新 SDK，然后备份旧目录再切换；失败会尝试隔离新目录并恢复备份。

## 保留现有 AIMDK 安装 R18

以下操作使用 Windows PowerShell 上传；将机器人 IP 改成现场地址：

```powershell
$RobotIp = "机器人当前IP"
$Root = "E:\1资料\0火花机器人资料\0二次开发\灵犀导航微信小程序开发"
$Tar = "$Root\integrated_deployment\dist\lingxi-v1.3.1-complete-20261024-r18-deployment.tar.gz"
scp $Tar ("run@" + $RobotIp + ":/home/run/")
scp "$Tar.sha256" ("run@" + $RobotIp + ":/home/run/")
ssh ("run@" + $RobotIp)
```

登录 `run` 后校验、解包并准备文件：

```bash
cd /home/run
sha256sum -c lingxi-v1.3.1-complete-20261024-r18-deployment.tar.gz.sha256
tar -xzf lingxi-v1.3.1-complete-20261024-r18-deployment.tar.gz
cd /home/run/lingxi-v1.3.1-complete-20261024-r18-deployment
sha256sum -c SHA256SUMS.txt
bash ./install_all.sh prepare
```

本说明的保留 AIMDK 路径不会运行替换脚本。重新登录 `agi` 再做一次前述 R18 契约检查；检查通过后回到 `run`：

```bash
cd /home/run/lingxi-v1.3.1-complete-20261024-r18-deployment
bash ./install_all.sh install-navigation
bash ./install_all.sh install-addons --agent-code AI_AGENT_实际智能体Code
bash ./install_all.sh doctor
sudo sparkrobot-lingxi-complete status
sudo sparkrobot-lingxi-complete doctor
```

安装器依次部署灵犀导航、灵创、知识库和连续导航修复。连续导航修复必须最后安装。配置灵心/豆包密钥时按安装器提示交互输入，不要写入命令行或部署文档。

## R18 AIMDK 接口行为

- `/aima/te/pnc_task_request` 发布 QoS：`BEST_EFFORT + VOLATILE`。
- `/aima/pnc/pnc_task_response` 订阅 QoS：`BEST_EFFORT + TRANSIENT_LOCAL`。
- `reference_line` 字段用于发送经地图净空处理的全局参考路径。
- START 的 `target_pose_radius` 不低于 `0.25 m`；STOP 仍发送 `0.0`。
- 到达逻辑自身的 `position_tolerance_m` 仍保持单独配置，不会因 SDK 最小值被扩大。

本次配套组件为 `sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18`、`sparkrobot-lingxi-kb-1.0.10-20261023` 和 `sparkrobot-lingxi-consecutive-nav-fix-1.0.8-20261024-r18`。连续导航修复必须作为最后一层覆盖安装。

## 全局路径净空与 3000 页面

地图、点位、任务、机器人实测外接半径和额外障碍净空均在机器人 `3000` 控制台配置。路径安全参数与 `task_type` 位于同一页。`8002`、`18082` 是内部服务端口，不是用户配置入口。

R18 保留 V1.3.1 R17 全局路径预防逻辑：仅对 `task_type=2、pnc_mode=0` 的全局导航，按实测机器人半径和额外净空膨胀静态地图障碍物，生成 `reference_line` 后提交 PNC。实测半径缺失、定位过期、地图不可读或无安全路径时拒绝下发，不回退到未保护路径。`target_pose_radius` 是到点容差，不是障碍净空。

## 语音配置和 TTS 边界

已有 AIMDK 和重新替换 AIMDK 使用相同的 R18 知识库、语音策略及部署顺序。`doubao.streamingReplies=false` 默认关闭；不得因为保留 AIMDK 就自动开启流式回复。配置文件位置由 Java Agent 的 `WorkingDirectory` 决定，安装器会读取 `CONFIG="${AGENT_WORKDIR}/config/lingxi-nav.properties"` 对应配置；升级不覆盖现有 properties。

若后续在目标机器人单独评估流式回复，必须先验证 AIMDK PlayTts 连续队列、首段、后续分段、停止和新播报抢占行为。`call_async` 一旦提交，本桥接层不具备撤回或停止能力，`stream_action=end` 只关闭软件流状态。此说明不代表已通过目标机器人 AIMDK 队列验收。

参数保存后，确认页面报告运行时回读一致。真实移动前由现场人员看护，确认地图、定位和急停正常，先以低速短距离路线验收。离线包审计不等于机器人实机验收。
