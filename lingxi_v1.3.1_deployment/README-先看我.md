# 灵犀 v1.3.1 完整离线部署包

本套件用于内部人员把一台新智元 X2 机器人部署为可通过手机网页使用的灵犀导航机器人。客户日常只需让手机与机器人连接同一局域网，打开 `http://机器人IP:3000/console/`，点击“一键开启全部服务”。

## 套件内容

- AIMDK：`aimdk v0.0.0-g1bde262`。
- 导航运行组件：`1.0.8-20261023-r16`。
- 导航、地图、语音 bridge、灵心 Java Agent、网页控制台及完整源码。
- Windows 文件校验和上传脚本。
- 新机器人环境、AIMDK、导航和验收脚本。
- 完整部署教程、参数模板和验收清单。

套件不包含真实灵心、豆包、SSH 密钥。部署时从内部参数文件交互填写，不要修改直播项目的配置。

## 第一步：解压并上传

在 Windows PowerShell 中进入本目录，执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\Windows-校验并上传全部文件.ps1 -RobotIp 172.20.10.2
```

部署其他机器人时，把 IP 替换成现场机器人的实际 IP。

## 第二步：安装环境

```powershell
ssh run@机器人IP
```

机器人 `run` 终端执行：

```bash
chmod +x /home/run/00-install-environment.sh
/home/run/00-install-environment.sh
exit
```

## 第三步：更换 AIMDK

```powershell
ssh agi@机器人IP
```

机器人 `agi` 终端执行：

```bash
chmod +x /home/agi/01-replace-aimdk.sh
/home/agi/01-replace-aimdk.sh
exit
```

脚本会先编译、验证新 AIMDK，再无备份删除旧 `/home/agi/aimdk`。验证失败时不会删除旧目录。

## 第四步：安装导航产品

```powershell
ssh run@机器人IP
```

机器人 `run` 终端执行：

```bash
chmod +x /home/run/02-install-lingxi-navigation.sh
/home/run/02-install-lingxi-navigation.sh
```

按提示填写灵心 APP ID、AppKey、AppSecret、豆包 API Key、模型、机器人名称和 SOC2 地址。SOC2 默认通常为 `10.0.1.42`。安装后临时密钥文件会被自动删除。

## 第五步：必须通过网页启动

手机和机器人连接同一局域网，打开：

```text
http://机器人IP:3000/console/
```

点击“一键开启全部服务”。页面全部正常后，在 `run` 终端执行：

```bash
chmod +x /home/run/03-verify-after-web-start.sh
/home/run/03-verify-after-web-start.sh
```

## 第六步：配置地图和点位

新机器人没有点位时 `point_names=[]` 是正常状态。必须在网页中：

1. 刷新并选择机器人实际地图。
2. 添加起点和导航点。
3. 确认 `/slam/lidar_odom` 持续有定位数据。
4. 现场人员看护下执行自动重定位。
5. 先测试短距离点位，再测试讲解任务。

检查定位时登录 `agi` 执行：

```bash
chmod +x /home/agi/04-check-localization.sh
/home/agi/04-check-localization.sh
```

完整命令、故障排查、升级和回滚见：

```text
docs/灵犀-v1.3.1-完整部署教程.md
```
