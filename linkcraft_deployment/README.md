# 灵创舞蹈与动作独立更新包

版本：1.0.3-20260902

这个包只更新导航讲解任务中的灵创舞蹈/灵创动作执行链路及网页资源列表读取模块。它不会重新部署导航主包，不会替换 AIMDK，不会修改灵犀 Java Agent、语音配置、知识库或直播项目。

## 包含内容

- 到点讲解完成后可选执行灵创舞蹈或灵创动作。
- 使用 AimDK /aimdk_5Fmsgs/srv/ExecuteActionResource 提交资源。
- 支持 BODY_MONTION 和 ARM_MONTION 两类资源。
- 资源提交、忙重试、等待完成、超时继续任务和停止时中断动作。
- 从机器人资源目录扫描 linkcraft_resource_* 资源。
- 资源诊断默认以机器人资源用户 run 读取受限挂载目录，避免 sudo root 被 NFS 权限拒绝。
- 修复机器人模式网页后端的 Python 路径，确保网页能读取 current/navigation 下的灵创资源。
- 任务没有配置灵创资源时，按原逻辑直接进入下一个点位。
- 安装前自动备份，支持单独回滚。

## 构建

在本仓库根目录执行：

```powershell
python linkcraft_deployment\build_package.py
python linkcraft_deployment\audit_package.py
```

构建结果在 linkcraft_deployment/dist/，同时生成 .tar.gz、.zip 及各自的 .sha256 文件。建议 Linux 机器人使用 .tar.gz。

## 现场限制

安装包只负责更新执行代码。机器人仍需已经安装可运行的导航主包、ROS2/AIMDK Python 环境以及对应灵创资源；没有资源文件时，资源列表为空，不能执行舞蹈。安装和静态检查不会执行真实舞蹈。

完整命令见 docs/灵创独立更新包部署教程.md。
