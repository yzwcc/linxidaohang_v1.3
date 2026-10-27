# 灵犀 X2 V1.3.1 R19 完整部署包

R19 以灵犀导航 V1.3.1 R18 `b12ce51` 为基线，包内包含导航控制台、机器人端网页、实时建图预览、建图保存、定位诊断、导航 bridge、Java Agent、运行时和安装/回滚脚本。R18 在此指导航版本，不是固件版本；目标机器人仍需具备厂商底层运行环境。自定义机器人轮廓、额外安全距离和路径安全规划控件/逻辑均不包含；机器人原生安全能力保持不变。

网页运行在机器人 `:3000`，同一局域网的手机或电脑访问 `http://机器人IP:3000`。建图页面只负责传感器状态、实时地图显示和 AIMDK 地图保存；机器人移动由外置遥控器负责。

## 构建

需要仓库现有 V1.3.1 Java/ARM64 runtime 构建产物、Node.js/npm、Python 3.10+ 和 AIMDK SDK artifacts ZIP：

```powershell
python r19_full_deployment/build_r19_release.py `
  --aimdk-archive "r19_full_deployment/aimdk-sdk-v1_1_0-aimdk-aarch64-faf4c945-artifacts.zip" `
  --output "r19_full_deployment/dist"
```

构建器将 SDK ZIP 中的 AArch64 `aimdk_msgs` 预编译 overlay 放入发布包，不会把整个 SDK ZIP 复制到部署包。

## 发布前验证

构建器会从固定 R18 提交快照构建网页、运行服务端测试与 TypeScript 构建，并检查 AIMDK 接口、建图入口、移除的自定义安全逻辑和 ARM64 Node 架构。对最终生成的导航代码还会实际导入模型、检查默认工厂隔离和配置序列化往返，避免只通过语法检查却无法启动。安装前会用机器人环境导入导航、地图、语音网关模块。构建结束后，在 Ubuntu/WSL 中以 root 将最终归档直接交给隔离测试：

```bash
sudo bash r19_full_deployment/tests/test_installer_isolated.sh \
  r19_full_deployment/dist/sparkrobot-lingxi-r19-full-1.1.6-20261027-r19-full.tar.gz
```

该隔离测试覆盖包内 AIMDK overlay 安装、已有 AIMDK 复用、接口预检失败保护、配置属主顺序和切换前失败恢复。测试通过后仍须在目标机器人验收真实 ROS/DDS 传感器、SLAM 实时扫描和地图保存。

## 验收边界

发布前检查可验证包完整性、前端和服务端构建、单元测试、Python 语法、shell 语法、AArch64 Node 架构、两种 AIMDK 安装路径、接口预检失败保护及 SHA-256。传感器真实数据、SLAM 实时扫描、地图保存结果和地图方向仍须在目标 R18 机器人上验收；软件包检查不能代替实机验收。
