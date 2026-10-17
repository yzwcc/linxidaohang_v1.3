# 灵犀导航到达判定修复增量包

版本：1.0.2-20260906

本包只修复一个问题：底层 PNC 已经完成导航，但里程计距离仍停在约 0.28 米时，上层导航会继续等待并最终超时。本包订阅 `/aima/pnc/pnc_task_response`，按 `task_id` 匹配当前任务；收到 `CommonTaskResponse.state.value=1` 后立即进入到点讲解流程。

本包不会重新部署或修改：AIMDK、语音服务、灵心配置、地图服务、灵创资源、知识库、Java Agent、直播项目。

## 包含内容

- `payload/navigation_client.py`：导航会话增加 PNC 完成/失败回验。
- 取消正在导航或返航的任务时，STOP 会短时持续发布，避免新目标 START 与旧任务停止发生竞态。
- `payload/pnc_response.py`：解析 `aimdk_msgs/msg/CommonTaskResponse`。
- `install.sh`：备份后原子替换，仅重启导航 bridge。
- `control.sh`：状态、诊断、日志、回滚和导航 bridge 重启。
- `docs/导航到达判定修复包部署说明.md`：现场命令。

## 安装原则

安装脚本默认目录为 `/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3`。如果现场目录不同，可在命令前设置 `NAV_DIR`，例如：

```bash
sudo env NAV_DIR=/opt/sparkrobot/lingxi-nav/current/navigation/robot_local_navigation_v3 ./install.sh
```

安装脚本不会调用会连带重启语音的总控命令。找不到导航专用重启脚本时，文件会保持已备份和替换状态，并提示手动重启命令。

如果压缩包经过 Windows 工具解压后执行权限丢失，先执行 `chmod +x install.sh control.sh`，再用 `sudo bash ./install.sh` 安装。
