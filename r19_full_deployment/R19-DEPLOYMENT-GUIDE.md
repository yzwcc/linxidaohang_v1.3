# 灵犀 X2 V1.3.1 R19 部署说明

## 包含内容与适用范围

R19 是一次性部署 V1.3.1 R18 导航、地图标注、实时建图、保存地图、定位诊断、网页、运行时与 Java Agent 的 ARM64 部署包。这里的“R18”指灵犀导航 V1.3.1 R18 版本基线，不是单独的固件版本。无需先安装旧版 R18 导航部署包或旧版 AIMDK。

包内包含 V1.3.1 R18 导航程序和所需的 `aimdk_msgs` AArch64 预编译消息 overlay，但不包含机器人底层系统、厂商 HAL/传感器驱动、SLAM、MapManager、ROS 2 或完整 AIMDK runtime。目标机器人必须已有兼容的厂商运行环境及 `/opt/ros/humble`；这些底层运行组件不由 R19 替代。已有 AIMDK 可选择复用现有 setup。

自定义机器人轮廓、额外安全距离和自定义路径安全规划已从 R19 网页/API/导航代码中移除。R19 不关闭、不替代、不修改机器人固件自带的安全功能。

建图工作流是：进入网页“实时建图”页面并检查传感器接口 -> 在机器人周边由人员使用外置遥控器控制机器人移动 -> 网页实时显示扫描地图 -> 网页停止并调用 AIMDK 保存地图。网页不发送移动控制命令。

## 1.1.6 启动与安装修正

1.1.6 修正移除安全规划后桥接端仍传入 `map_root` 的构造参数不匹配，并同步清理被删除方法的装饰器，防止遗留的 `@staticmethod` 错误作用于停止导航方法。构建会验证最终导航构造参数与停止方法绑定。

同时修正 Java 配置文件的最终属组：`install` 生成的新文件必须重新设为 `root:sparkrobot-nav`，只设置上游临时文件属组并不足够。Java Agent 以 `run` 用户和附加运行组读取配置；权限保持 `0640`，不放宽为全员可读。

1.1.3 实机启动发现打包器删除旧安全配置类时遗留了 `@dataclass`，导致后续导航配置类重复装饰并导入失败。1.1.4 同时删除类与其装饰器，构建时验证最终导航模型导入及配置往返，并在安装写入前预检完整导航、地图和语音模块。

完整模块预检还发现 R18 语音桥强制导入新 SDK 不提供的 `PlayMediaFile`。1.1.4 将其作为可选能力：缺少时在状态中明确标为不支持，相关请求返回明确错误，不影响新 AIMDK 的 TTS、音频、动作、导航和建图接口，也不加载旧 AIMDK。

1.1.2 的安装器在生成配置文件的临时文件后，提前对尚未创建的最终路径执行 `chown`，因此会报 `unified_nav_config.json: No such file or directory`；这不是 AIMDK 编译或接口预检失败。1.1.3 已将属主设置移到配置文件原子替换之后，并让切换前发生错误时恢复受管文件快照，增加了对应回归检查。

本次检查的机器人上，`/home/agi/aimdk` 只有源码，`/home/agi/aimdk/install/setup.bash` 不存在。因此不要把这个不存在的路径传给 `--aimdk-mode reuse`。默认 `overlay` 使用包内预编译的 AArch64 `aimdk_msgs` 消息接口，并叠加机器人已有 ROS/厂商运行环境；安装器已通过该 overlay 的接口预检。它不要求先在机器人上编译 `/home/agi/aimdk`，也不替代厂商完整 AIMDK runtime。

1.1.2 失败尝试已在机器人创建 root-only 控制台 key，并写入仅允许本机来源的受限 SSH 授权项。1.1.3 会复用现有 key 完成安装，不要在重试前手动删除。

## 安装准备

1. 将归档包和同名 `.sha256` 文件复制到机器人用户 `run` 的家目录 `/home/run/`。
2. 从 `/home/run` 校验 SHA-256：

```bash
cd /home/run
sha256sum -c sparkrobot-lingxi-r19-full-1.1.6-20261027-r19-full.tar.gz.sha256
```

3. 解压：

```bash
mkdir -p /home/run/r19/package
tar -xzf /home/run/sparkrobot-lingxi-r19-full-1.1.6-20261027-r19-full.tar.gz -C /home/run/r19/package
cd /home/run/r19/package/sparkrobot-lingxi-r19-full-1.1.6-20261027-r19-full
```

4. 确认底层运行环境，并准备首次安装密钥文件：

```bash
test -r /opt/ros/humble/setup.bash || { echo "缺少 ROS 2 Humble 环境"; exit 1; }
uname -m
id agi
id run
```

`uname -m` 应显示 `aarch64`。若此前没有准备密钥，运行交互向导：

```bash
./configure.sh /home/run/r19/secrets.env
```

向导需要灵心 `APP ID`、`AppKey`、`AppSecret` 和豆包 Ark API Key。若已有密钥文件，不要重复生成；安装时填写它的实际绝对路径。密钥文件权限为 `0600`，不要粘贴到聊天、日志或代码仓库。

## 方式 A：使用包内 AIMDK 消息 overlay

适用于机器人已有兼容的厂商运行环境和 ROS 2，但没有可复用 AIMDK Python 消息包的情况。包内 overlay 只补足本项目所需的 `aimdk_msgs` Python/ROS 接口，不替代完整 AIMDK，也不需要先安装旧版 R18 导航包。

```bash
sudo ./install.sh --secrets /home/run/r19/secrets.env --aimdk-mode overlay
sudo sparkrobot-lingxi-internal start
sudo sparkrobot-lingxi-internal doctor
```

新机默认启动网页、导航 bridge、本机语音 bridge 和 Java Agent。重启厂商底层语音服务所需的 SOC2 SSH 通道为可选配置，不阻断网页/导航/建图。

## 方式 B：复用已有 AIMDK

仅当机器人已安装与本项目兼容的 AIMDK，且其 setup 文件确实存在、可读并导出所需接口时才选此方式。若没有 setup 文件，使用方式 A。安装器会先验证接口，再写配置或停止现有服务；失败时不会切换版本或覆盖 AIMDK。

```bash
AIMDK_SETUP=/actual/verified/aimdk/setup.bash
sudo ./install.sh \
  --secrets /home/run/r19/secrets.env \
  --aimdk-mode reuse \
  --aimdk-setup "$AIMDK_SETUP"
sudo sparkrobot-lingxi-internal start
sudo sparkrobot-lingxi-internal doctor
```

将 `AIMDK_SETUP` 替换为机器人上确实存在、可读且已验证导出所需接口的 setup 绝对路径；不要原样运行占位值。安装器只读取该 setup 做接口预检，不会覆盖或搬动现有 AIMDK。

已有 R19 安装升级时，如需沿用已保存的灵心/豆包密钥，可省略 `--secrets`；首次安装必须提供 `--secrets`。安装器会保留地图、点位和任务，并在切换前备份现存配置。

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

首次安装没有旧版本时不能回滚。升级安装且存在 previous release 时才使用 `rollback`。

若校验提示文件不存在，先运行 `ls -lh /home/run/sparkrobot-lingxi-r19-full-1.1.6-20261027-r19-full.tar.gz*` 确认归档和 `.sha256` 文件都直接放在 `/home/run`。请使用本说明对应的 `1.1.6` 修复包及其校验文件；不要将旧包改名冒充新包。

如果现场需要让网页一键重启 SOC2 底层语音服务，再单独运行 `sudo /opt/sparkrobot/lingxi-nav/current/scripts/setup_soc2_access.sh` 配置受限 SSH 权限；这不是建图所需条件。

网页本机健康检查：`curl http://127.0.0.1:3000/health`。导航/建图桥接端口为 `18082`，网页为 `3000`。日志位于 `/var/log/sparkrobot/lingxi-nav/`。

卸载服务不会删除地图、配置或版本目录；请勿手动删除 `/var/lib/sparkrobot/lingxi-nav/maps`。

## 已知验收边界

部署包构建与静态/自动化检查通过，表示文件、接口和安装逻辑达到相应软件检查要求，不表示真实机器人已完成硬件验收。首次在目标机器人安装后必须分别确认局域网可访问、手机横竖屏与电脑布局、建图健康检查、实时点云变化、保存服务成功、地图可重新加载与方向正确。导航、语音链路也需按现场业务单独验收。
