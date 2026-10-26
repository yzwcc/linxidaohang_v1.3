# 灵犀 X2 V1.3.1 R19 部署说明

## 包含内容与适用范围

R19 是一次性部署 V1.3.1 R18 导航、地图标注、实时建图、保存地图、定位诊断、网页、运行时与 Java Agent 的 ARM64 部署包。无需先安装旧版 AIMDK。适用于灵犀 X2 V1.3.1 R18 固件；不适用于其他固件版本。

包内包含 R18 所需的 `aimdk_msgs` AArch64 预编译消息 overlay，但不包含 ROS 2、厂商底层固件、HAL、SLAM、MapManager 或完整 AIMDK runtime。新机必须已经有厂商 R18 固件及 `/opt/ros/humble`。已有 AIMDK 可选择复用现有 setup。

自定义机器人轮廓、额外安全距离和自定义路径安全规划已从 R19 网页/API/导航代码中移除。R19 不关闭、不替代、不修改机器人固件自带的安全功能。

建图工作流是：进入网页“实时建图”页面并检查传感器接口 -> 在机器人周边由人员使用外置遥控器控制机器人移动 -> 网页实时显示扫描地图 -> 网页停止并调用 AIMDK 保存地图。网页不发送移动控制命令。

## 安装准备

1. 将归档包和 `.sha256` 文件复制到机器人，例如 `~/r19/`。
2. 校验 SHA-256：

```bash
cd ~/r19
sha256sum -c sparkrobot-lingxi-r19-full-1.1.0-20261027-r19-full.tar.gz.sha256
```

3. 解压：

```bash
mkdir -p ~/r19/package
tar -xzf sparkrobot-lingxi-r19-full-1.1.0-20261027-r19-full.tar.gz -C ~/r19/package
cd ~/r19/package/sparkrobot-lingxi-r19-full-1.1.0-20261027-r19-full
```

4. 准备首次安装密钥文件。可以交互生成临时文件：

```bash
./configure.sh ~/r19/secrets.env
```

密钥文件包括 `LINGXI_APP_ID`、`LINGXI_APP_KEY`、`LINGXI_APP_SECRET`、`DOUBAO_API_KEY`，权限应为 `0600`。不要发给同事以外人员或上传 Git。

## 方式 A：使用包内 AIMDK 消息 overlay

适用于机器人只有 R18 固件和 ROS 2，没有可复用 AIMDK Python 消息包的情况。包内 overlay 只补足本项目所需的 `aimdk_msgs` Python/ROS 接口，不替代完整 AIMDK。

```bash
sudo ./install.sh --secrets ~/r19/secrets.env --aimdk-mode overlay
sudo sparkrobot-lingxi-internal start
sudo sparkrobot-lingxi-internal doctor
```

新机默认启动网页、导航 bridge、本机语音 bridge 和 Java Agent。重启厂商底层语音服务所需的 SOC2 SSH 通道为可选配置，不阻断网页/导航/建图。

## 方式 B：复用已有 AIMDK

适用于机器人已安装与 R18 兼容的 AIMDK，并且其 setup 导出本项目所需接口的情况。安装器会先验证接口，再写配置或停止现有服务；失败时不会切换版本或覆盖 AIMDK。

```bash
sudo ./install.sh \
  --secrets ~/r19/secrets.env \
  --aimdk-mode reuse \
  --aimdk-setup /home/agi/aimdk/install/setup.bash
sudo sparkrobot-lingxi-internal start
sudo sparkrobot-lingxi-internal doctor
```

`--aimdk-setup` 必须是机器人上的绝对路径。安装器只读取该 setup 做接口预检，不会覆盖或搬动现有 AIMDK。

升级已有 R18 安装时，如需沿用已保存的灵心/豆包密钥，可省略 `--secrets`；首次安装必须提供 `--secrets`。安装器会保留地图、点位和任务，并在切换前备份现存配置。

## 启动网页和开始建图

安装成功后，手机或电脑连接机器人同一局域网，浏览：

```text
http://机器人局域网IP:3000
```

打开“实时建图”页，先点检查。确认传感器、SLAM、雷达点云、IMU、里程计和 AIMDK 建图接口状态正常后，点“开始建图”。手机会进入全屏建图工作区，并在浏览器允许时请求锁定横屏；如浏览器不支持方向锁定，请手动横向旋转手机。横屏时地图画布占主区域，采集/保存操作和诊断收在侧栏。网页每 0.5 秒请求一次增量预览，断线或增量窗口失效时自动回完整地图快照；实际点云与轨迹更新速度仍取决于机器人 SLAM、无线网络及浏览器性能。随后由外置遥控器缓慢带机器人经过待建区域；不要用网页驱动机器人。扫描完成后点“停止采集并保存地图”，确认保存结果，再在地图列表中检查地图文件和方向。

手机与电脑均可访问机器人网页；手机浏览器的横屏锁定能力因系统/浏览器策略而异。按钮会在用户点击后请求全屏和横屏，不能锁定时不阻断建图操作，会提示手动横屏。退出全屏可点击“退出全屏”或使用浏览器/系统返回手势。

如果检查未通过，不要反复点击开始；记录页面诊断和日志后先解决对应固件/ROS/DDS/接口问题。地图保存前不要重启或重新安装，以免中断当前建图会话。

## 运维、日志和回滚

```bash
sudo sparkrobot-lingxi-internal status
sudo sparkrobot-lingxi-internal doctor
sudo sparkrobot-lingxi-internal logs all
sudo sparkrobot-lingxi-internal logs navigation
sudo sparkrobot-lingxi-internal rollback
```

如果现场需要让网页一键重启 SOC2 底层语音服务，再单独运行 `sudo /opt/sparkrobot/lingxi-nav/current/scripts/setup_soc2_access.sh` 配置受限 SSH 权限；这不是建图所需条件。

网页本机健康检查：`curl http://127.0.0.1:3000/health`。导航/建图桥接端口为 `18082`，网页为 `3000`。日志位于 `/var/log/sparkrobot/lingxi-nav/`。

卸载服务不会删除地图、配置或版本目录；请勿手动删除 `/var/lib/sparkrobot/lingxi-nav/maps`。

## 已知验收边界

部署包构建与静态/自动化检查通过，表示文件、接口和安装逻辑达到相应软件检查要求，不表示真实机器人已完成硬件验收。首次在目标机器人安装后必须分别确认局域网可访问、手机横竖屏与电脑布局、建图健康检查、实时点云变化、保存服务成功、地图可重新加载与方向正确。导航、语音链路也需按现场业务单独验收。
