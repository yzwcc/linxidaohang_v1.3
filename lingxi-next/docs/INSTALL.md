# 安装、升级与回滚

本版只生成联调包。先阅读 ACCEPTANCE.md 的未完成项，现场安排原系统备份与端口切换；不覆盖厂商 AIMDK，也不替换旧版服务。

## 构建工作站

```sh
npm ci
npm run build
npm test
python -m unittest discover -s robot-api/tests -p "test_*.py" -v
npm run build:agent
npm run release:commissioning
npm run android:build
```

Java 构建自动获取并校验固定 SDK JAR、Maven；Android 构建使用本地 JDK21、Android35 和 Gradle。下载缓存与密钥在 `.local/`，不进入 Git 或机器人包。Android 首次构建可能需要较长时间，下载支持续传。

生产 Android 签名需设置 `LINGXI_ANDROID_KEYSTORE`、`LINGXI_ANDROID_STORE_PASSWORD`、`LINGXI_ANDROID_KEY_ALIAS`、`LINGXI_ANDROID_KEY_PASSWORD`。未提供时仅生成本地联调签名，不作为正式发布签名。

## 机器人安装

解压 `artifacts/lingxi-next-*-linux-arm64.tar.gz`，先核对旁路 `.sha256`，再进入解压目录：

```sh
sudo bash deploy/install.sh --commissioning --robot-ip 192.168.1.20 --legacy-dir /path/to/legacy/map-associated-json
```

`--legacy-dir` 可选。迁移先 dry-run，全部源文件转换成功后才应用；有未知结构、重复地图、缺坐标或无效限制时拒绝导入，保留原文件。迁移的动作/资源任务需要网页核对后保存。知识库不迁移。迁移时只读旧配置，不读取密钥或搬动 AIMDK 地图。

独立目录：

| 路径 | 用途 |
|---|---|
| `/opt/lingxi-next/releases/版本` | 只读应用、Node ARM64、Java ARM64 |
| `/opt/lingxi-next/current` / `previous` | 当前与回滚版本链接 |
| `/opt/lingxi-next/backups` | root-only 配置/业务备份，含敏感信息，不外传 |
| `/etc/lingxi-next/robot.env` | root-only 服务配置、内部令牌、配对码哈希 |
| `/etc/lingxi-next/pairing-code.txt` | root-only 首次配对码 |
| `/etc/lingxi-next/tls` | 本机器人 CA、服务证书与私钥 |
| `/var/lib/lingxi-next` | 模式0700的业务状态与凭据 |

安装器只先启动管理 API。配置 `robot.env` 中既有 ROS/AIMDK setup、地图/激光话题和 Domain ID 后：

```sh
sudo systemctl enable --now lingxi-next-aimdk.service
```

服务用户必须能读取既有 AIMDK setup/地图目录、访问 ROS DDS。按现场账号/权限规划授予读取权限，不要递归改变厂商目录所有权。ROS 同用户共享内存限制需现场验证；可使用厂商兼容的 DDS 配置，不改其安全规划参数。

扫图页使用真实雷达点迹，配置 `AIMDK_SCAN_TOPIC` 或 `AIMDK_POINTCLOUD_TOPIC`，
并核对 `AIMDK_LIDAR_FIXED_FRAME` / `AIMDK_BASE_FRAME`。详见 `LIDAR.md`。

将 `/etc/lingxi-next/tls/ca.crt` 导入电脑/Android 的受信任 CA，浏览器访问 `https://机器人IP:3000`，输入配对码。APK 在同局域网输入相同 IP/端口。若机器人 IP 更改，应重新签发包含新 IP SAN 的服务证书；不能关闭证书校验。

设置页写入灵心三项应用凭据、Agent Code、模型凭据；在环境文件设置绑定的 `LINKSOUL_AGENT_ID` 后：

```sh
sudo systemctl enable --now lingxi-next-agent.service
```

Java 不从客户端读取密钥，不打印回调内容，SDK 日志关闭。云端断开或关键唤醒/置信度字段缺失时，不继续派发动作。

## 升级与回滚

升级包使用新的 VERSION，同一安装命令会先停止这三个独立服务、备份配置与数据，再切换链接。配置和厂商地图保留。升级后只有 API 自动启动；现场核对后再启动导航适配与语音，避免升级直接驱动机器人。

```sh
sudo bash /opt/lingxi-next/current/deploy/rollback.sh
```

回滚前验证旧版本校验和并备份现状，只恢复代码链接与服务定义，不自动覆盖业务数据。需恢复迁移前业务时，停本产品服务，从对应 root-only 备份中人工核对恢复 `state.json`，不要覆盖 AIMDK 地图文件或将旧凭据外传。

## 诊断

```sh
sudo bash /opt/lingxi-next/current/deploy/diagnose.sh
sudo journalctl -u lingxi-next-api -u lingxi-next-aimdk -u lingxi-next-agent --since "10 minutes ago"
```

诊断脚本只输出架构、服务结果、版本和健康结果，不打包地图、知识库、凭据或语音原文。`/health` 仅表示 HTTP 服务存活；管理页面的传感器与导航检查才表示接口是否就绪。
