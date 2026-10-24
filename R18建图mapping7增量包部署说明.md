# 灵犀 X2 R18 建图 mapping7 增量包

本增量包基于 V1.3.1 R18 与 AIMDK v1.1.0，只更新机器人本机导航控制台及 AIMDK 建图适配器。它不替换 AIMDK、不发送机器人移动命令，也不包含新的 Java Agent 或导航控制行为。

## 文件

`internal_deployment/dist/mapping7/sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18-mapping7.tar.gz`

SHA-256：

`bea443ea73a372fdda494c660edc5278f124895e2409d4e1ed96efcceb96f96d`

请先将 tar 包复制到机器人，然后校验并解压：

```bash
sha256sum sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18-mapping7.tar.gz
mkdir -p ~/r18-mapping7
tar -xzf sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18-mapping7.tar.gz -C ~/r18-mapping7
cd ~/r18-mapping7/sparkrobot-lingxi-nav-internal-1.1.0-20261024-r18-mapping7
```

首次安装若机器人尚未配置灵心/SOC2 凭据，使用 `sudo ./install.sh --secrets ./secrets.env --setup-soc2`；不要把真实凭据放进压缩包。已有 R18 安装只升级程序时，安装器会保留机器人现有配置、地图和任务：

```bash
sudo ./install.sh
sudo sparkrobot-lingxi-internal start
sudo sparkrobot-lingxi-internal doctor
readlink -f /opt/sparkrobot/lingxi-nav/current
```

确认当前版本目录为 `1.1.0-20261024-r18-mapping7`，并检查控制台健康状态：

```bash
curl -fsS http://127.0.0.1:3000/health
```

## 建图操作

1. 手机或电脑连接机器人所在局域网，访问 `http://机器人局域网IP:3000/console/`。
2. 打开建图工作区并检查 AIMDK、雷达、IMU、SLAM 里程计和建图接口状态均就绪。
3. 输入地图名称并开始建图。
4. 使用外置遥控器手动移动机器人完成环境覆盖；网页不会控制底盘移动。
5. 在网页停止并保存。停止命令通过 AIMDK `/integrated_command` 发布 `stop_mapping:<map_name>`，随后调用 `GetStoredMapByName` 确认地图并导入导航地图目录。
6. 确认网页返回保存成功后，在地图/点位页面刷新并检查地图显示，再按现场流程验收定位与导航。

当前接口不支持旋转的地图原点，若服务返回此类地图会拒绝导入以避免点位坐标错误。保存失败时先查看 `sudo sparkrobot-lingxi-internal logs console`，并在机器人上核对 AIMDK SLAM 服务状态；不要重复覆盖同一数字地图 ID 的不同内容。

## 回滚

安装器会保留已安装的历史 release。确认新版本异常时，可回滚到安装前的 R18 版本并重启：

```bash
sudo sparkrobot-lingxi-internal rollback
sudo sparkrobot-lingxi-internal start
readlink -f /opt/sparkrobot/lingxi-nav/current
```

## 验证边界

本次已执行生成保存脚本的离线 AIMDK mock 回归，验证栅格到 PNG 的方向/灰度、导航原点元数据、重复 ID 复用和冲突拒绝。机器人实机 SSH、实时传感器、完整建图、地图重载与现场导航仍须在目标 X2 上验收。
