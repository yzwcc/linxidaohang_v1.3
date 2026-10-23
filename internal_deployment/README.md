# 灵犀导航内部明文部署包

本目录用于生成内部人员使用的新机器人离线部署包。它不包含设备绑定、许可证、签名验签、Python 原生编译或 Java 混淆。

## 构建

```powershell
cd "E:\1资料\0火花机器人资料\0二次开发\灵犀导航微信小程序开发"
python internal_deployment\build_internal_release.py --force
```

默认输出：

- `internal_deployment/dist/sparkrobot-lingxi-nav-internal-1.0.8-20261023-r16.tar.gz`
- `internal_deployment/dist/sparkrobot-lingxi-nav-internal-1.0.8-20261023-r16.zip`
- 两个归档对应的 `.sha256`

## 构建输入

- `internal_deployment/build/robot-local-navigation-java-all.jar`：现场验收通过的未加密 Java JAR。
- `productization/build/linksoul-agentsdk-1.4.0.jar`：灵心 AgentSDK v1.4。
- `productization/build/node-arm64`：ARM64 Node.js 运行时。
- `productization/build/jre17-arm64/`：ARM64 JRE 17。
- 当前仓库的 Python 导航、Java 源码与 `server/` 网页源码。

新机器人的安装命令和验收步骤见归档内 `docs/内部部署与命令手册.md`。

现场验收 JAR 固定校验值：

```text
SHA256 d3858827d71aa54dc4b7cc794a0d8446cf0168d65e4cca0a2e686b6f7012dd04
```

机器人端最短安装流程：

```bash
./quick_install.sh
```

分步安装流程：

```bash
./configure.sh ./secrets.env
sudo ./install.sh --secrets ./secrets.env --setup-soc2
rm -f ./secrets.env
sudo sparkrobot-lingxi-internal start
sudo sparkrobot-lingxi-internal doctor
```
