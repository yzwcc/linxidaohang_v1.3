# 构建与依赖

本项目使用根目录锁文件固定 npm 解析结果。使用 Node.js 22 或更高版本、Python 3、JDK 17 或更高版本构建 API 和 Java 适配器：

```sh
npm ci
npm test
npm run build:agent
npm run release:commissioning
python tools/verify-release.py artifacts/lingxi-next-0.1.5-commissioning-20261028-linux-arm64.tar.gz
python tools/verify-android.py artifacts/lingxi-next-0.1.5-commissioning.apk
```

Windows 上执行 `npm run android:build`。工具链安装在忽略目录 `.local/`，
不会改变系统 Java、Android SDK 或 PATH。脚本使用 SDK 内置的 Android CLI，
固定 Android 35、Build Tools 35.0.0、Gradle 8.11.1，并关闭 Android CLI 统计。
Unicode 工作区路径在 Java properties 中转义；未关闭 TLS 验证。

Gradle 下载可使用华为镜像，最终必须与 Gradle 官方 SHA256 一致。
仅构建时启动的回环 Maven 转发只访问 Google 官方 Maven 和 Maven Central，
以绕过本机 Java TLS 握手异常；Central 转发限定为构建中缺失的 Groovy/Apache 依赖组。
外部 HTTPS 仍验证证书，该转发不会进入机器人运行包。

## 固定二进制

| 依赖 | 版本/目标 | SHA256 |
|---|---|---|
| Node.js | 22.23.4 Linux ARM64 | `f7a1e486dbc8b286847815b477d4fb5e9fb963ed4489c3e1588853d685f589f9` |
| Azul Zulu JRE | 21.0.13 Linux ARM64 | `3f4649159c0780bfb4c66b73f17f784830e1cbc0b79698a2b235bd7fe18dea25` |
| Azul Zulu JDK | 21.0.13 Windows x64，APK 构建 | `69e9fd7afafe0d70235884f8d9ab15cf8a4b43215b27900d710841e1fb3c7126` |
| Gradle | 8.11.1 bin | `f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6` |
| AgentSDK | 1.5.0-SNAPSHOT | `810c5e7a7d40191097dc5c4c37e3133407e63307fcb204d35caacb7768912c25` |

Java 源码以 `--release 17` 编译，交付包使用私有 JRE 21，不替换机器人已有 Java。
打包工具直接从校验过的原始运行时归档组装最终 tar，保留 Linux 符号链接和 POSIX
执行权限，包括 Java 的 `lib/jspawnhelper`，不依赖 Windows 解压 Linux 链接。
`artifacts/` 中的中间目录不含运行时；安装必须使用最终 `.tar.gz` 的完整解压目录。

## Android 签名

未提供签名环境变量时，生成本机 commissioning 签名，密钥和口令仅放在 `.local/keys/`，
不进入源码、APK 或机器人包。正式产品签名由产品所有者保管，通过以下四个环境变量注入：

```text
LINGXI_ANDROID_KEYSTORE
LINGXI_ANDROID_STORE_PASSWORD
LINGXI_ANDROID_KEY_ALIAS
LINGXI_ANDROID_KEY_PASSWORD
```

APK 更新必须使用相同签名；更换签名可能需要卸载旧 App。调试和验收不使用生产密钥。
实际设备横屏、证书导入、断网恢复仍需按 `ACCEPTANCE.md` 验收。
