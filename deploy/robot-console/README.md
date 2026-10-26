# 机器人本机导航控制台

这是正式交付模式。控制台后端、网页、Node ARM64 运行时和全部生产依赖均安装在机器人 `/opt/lingxi-nav-console`，不依赖固定网站、公网服务器或客户电脑常驻程序。

## 客户使用

1. 机器人开机并连接现场网络。
2. 手机或电脑连接同一网络。
3. 在浏览器输入 `http://机器人IP:3000/`。
4. 点击“一键启动全部服务”。
5. 确认语音、灵心平台、导航、地图均在线，再选择地图并完成重定位。

机器人 IP 改变时不需要修改任何配置，只需在浏览器地址栏使用新 IP。

## 安装

把完整交付目录放到机器人后执行：

```bash
cd lingxi-nav-robot-console
sudo env ROBOT_AIMDK_SETUP=/机器人上/AIMDK-v1.1.0/install/setup.bash bash deploy/robot-console/install_robot_console.sh
```

安装器会拒绝空配置。必须先在机器人安装供应商提供的 AIMDK v1.1.0 overlay；用户提供的 SDK artifacts 开发归档不等于机器人可直接替换安装包。该路径会保存到控制台环境文件，并用于导航、定位、传感器/地图诊断。

安装脚本会：

- 安装到 `/opt/lingxi-nav-console`；
- 创建并启动 `lingxi-nav-console.service`；
- 设置开机自启和异常自动重启；
- 停用旧 `daohang-reverse-tunnel.service`；
- 保留机器人现有导航、地图及灵心语音配置，不覆盖 `/home/agi/robot_local_navigation_v3` 和 `/home/run/lingxi_java_agent`。

## 本机检查

```bash
systemctl status lingxi-nav-console.service
curl http://127.0.0.1:3000/health
```
