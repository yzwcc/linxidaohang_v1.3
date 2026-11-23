# 灵犀 X2 Ultra V1.1.5 联调

目标机 SoC1 为 `release-lx2501_3_t2d5-soc1-v1.1.5`。随附 AIMDK 清单为 `v1.1.0-rc10`；2026-11-23 在目标机实际读取的 `/agibot/software/aimdk/version` 和 `~/aimdk/version` 均为 `aimdk v0.0.0-g14fe2e8`。两者不要当成同一个构建。ROS 接入以目标机实际安装的接口定义、服务类型和传感器数据为准。

参考仓库固定在 `90bbc8096c23a5660fc6c5e26dd99ec1ed79da11`。联调服务在私有 Unix Socket 上提供 FAST-LIO 点迹、保存与放弃、栅格读取、GICP 重定位和 Nav2 路径预览。网页和横屏 APK 通过配对后的 HTTPS/WSS 使用这些接口。联调初始配置关闭运动，建图移动由现场人员使用外置遥控器。[参考仓库](https://github.com/Mikeaaaa14/Lingxi-navigation)

## 依赖和部署

ARM64 核心联调包提供产品 Web、API、参考服务适配、AgentSDK 进程和商品化之前的安装、回滚脚本。参考仓库、适配目标机的原生 ROS overlay、Python 3.10 环境、标定和机器人数据单独准备。

1. 在 `/opt` 下准备只读的参考 Git checkout，校验完整提交 ID，保留原仓库许可证和第三方声明。独立构建 `fast_lio`、`robot_nav_recorder`、`robot_nav_costmap`、`pointcloud_to_laserscan`，以及 `integrations/grid_export` 的 `grid_export_native`、`nav_ground_native`。以目标 ROS 2 Humble 和已安装的 AIMDK overlay 为依赖。Python 环境使用参考提交的 `requirements.txt`，并能加载这两个原生模块。FAST-LIVO2 和彩色相机处理不在此联调范围内。
2. 以参考 `config/app.yaml` 为基础生成 `/etc/lingxi-next/reference.yaml`。使用 `motion_enabled: false`、`nav2_enabled: true`、`navigation_mode: point`，并关闭 `visualization.enabled`、`color_mapping.enabled`、`mapping.loop_closure_enabled` 和 `mapping.grid_cache_enabled`。把 `data_dir` 指向 `/var/lib/lingxi-next/reference`，建图目录和导入/录制白名单设为该目录内的专用路径。建立 `lingxi-next` 所有、权限 `0700` 的数据目录。将 `mapping.fastlio_executable`、`mapping.fastlio_config`、`mapping.recorder_executable`、`mapping.calibration_files` 和 `localizer_config` 改为当前 ARM64 环境的绝对路径。按目标机校验 FAST-LIO 雷达—IMU 外参、入站话题 QoS、DDS 配置及初始站姿。
3. 在目标机按 `deploy/install.sh --commissioning --robot-ip IP` 安装产品。设置 `/etc/lingxi-next/robot.env` 中的 `LINGXI_AIMDK_SETUP`、`LINGXI_REFERENCE_SOURCE`、`LINGXI_REFERENCE_CONFIG`、`LINGXI_REFERENCE_CONFIG_SHA256`、`LINGXI_REFERENCE_OVERLAY`、`LINGXI_REFERENCE_PYTHON`、`LINGXI_REFERENCE_ROS2`，并设置 `LINGXI_REFERENCE_SOCKET=/run/lingxi-next-reference/control.sock`。保持 GLIM 和独立物理导航后端未选中。原生二进制和配置放在 systemd 服务账号可读且不依赖 `/home` 的路径。
4. 启动 `lingxi-next-reference-navigation.service`，检查日志后重启 `lingxi-next-api.service`。参考服务启动同提交的 Nav2 进程，使用当前产品的私有工作目录和内网 ROS 图。`deploy/diagnose.sh` 可检查服务状态。跨版本回滚前先结束建图并等待保存回执。若回到未包含参考服务的版本，先停止参考单元，再将 `LINGXI_REFERENCE_SOCKET` 留空。

如果按参考仓库的方式解包 Nav2 `.deb`，并把构建结果整理为同一版本目录下的 `ros/`、`overlay/`、`grid/`、`venv/`，可以把这个版本目录设为 `LINGXI_REFERENCE_RUNTIME_ROOT`，使用产品的 `deploy/reference-overlay.sh` 作为 `LINGXI_REFERENCE_OVERLAY`。可用 `LINGXI_REFERENCE_AIMDK_SETUP` 指定参考服务使用的 AIMDK 消息环境。`tools/prepare_reference_profile.py --source DIR --overlay DIR --fastlio-config FILE --data-dir DIR --output FILE` 用绝对路径生成新的运动禁用配置，不改模板、原配置或厂商文件。保持参考 checkout 与运行时由 root 管理、服务组只读。

参考服务要求 `LINGXI_REFERENCE_DDS_PROFILE` 显式指向这台机器人专用、root 所有的 UDPv4-only XML。用本机当前受信的 DDS 文件作为只读模板；替换下例中的 `/path/to/target-dds.xml`。输出必须是新的文件，目标目录为 root 所有、服务组只读。生成器保留 UDP 接口白名单和缓冲参数，移除共享内存传输及厂商日志配置。其他服务的 DDS 配置不变。

```bash
sudo install -d -o root -g lingxi-next -m 0750 /etc/lingxi-next/dds
sudo /usr/bin/python3 -I -B deploy/reference_dds_profile.py \
  --source /path/to/target-dds.xml \
  --output /etc/lingxi-next/dds/reference-udp4.xml
```

配置 `LINGXI_REFERENCE_DDS_PROFILE=/etc/lingxi-next/dds/reference-udp4.xml` 后，参考服务及其 FAST-LIO、录制器、Nav2 子进程统一使用此配置。静态切换和参考服务启动都拒绝缺失、不安全或仍启用 SHM 的配置。运行只读传感器探测时也使用相同的 ROS 环境、UDP 配置及 `rmw_fastrtps_cpp`，不要再用带 SHM 的模板探测。切换或回滚前先确认停稳并备份 `robot.env`。从带 SHM 的旧版恢复传感器前，先在维护窗口停用旧参考单元的自启动；新版自动恢复可还原旧版文件，但不自动启动或启用不含 UDP 校验的旧静态参考单元。显式回滚也拒绝将这类旧版本作为可启动的静态目标。

从旧 GLIM／独立导航版本切换时，先在现场确认机器人已停稳、没有运行中的导航、讲解或建图会话，并备份厂商原始地图。预先通过只读预检，填写 `robot.env` 中的 ROS/AIMDK、参考源码、overlay、Python、ROS2、配置文件和配置 SHA256；不必先改 `LINGXI_REFERENCE_SOCKET` 或旧后端选择。从核心联调包执行 `sudo bash deploy/install.sh --commissioning --reference-commissioning --robot-ip IP`。该模式先停止本产品的旧进程并备份 `/etc/lingxi-next` 和 `/var/lib/lingxi-next`，只启动参考服务和 HTTPS。私有接口必须确认空闲、目标标定匹配、雷达与 IMU 可用、AIMDK 和 Nav2 生命周期服务可发现，且没有运动接口，才能提交切换；未通过则还原原版本和快照。发现 Nav2 生命周期服务不等于已加载地图或可规划路径。旧业务数据、选项和厂商资产保留。回滚不会自动恢复旧运动、动作或语音进程，需现场确认后再启动。

在机器人上核对发行方提供的 SHA-256 后，以普通用户在私有暂存目录解包，并保留归档权限。替换下例中的归档文件名：

```bash
staging=$(mktemp -d)
tar --no-same-owner --same-permissions -xzf ./lingxi-next-VERSION-linux-arm64.tar.gz -C "$staging"
```

暂存目录保持 `0700`，包内 `deploy/run.sh` 和 `bridge/reference_service.py` 分别保留 `0755`、`0644`。不要仅依赖文件哈希检查权限：普通用户在 `umask 077` 下直接解包会屏蔽系统服务所需的读取权限，即使文件内容的校验全部通过。

`--reference-commissioning` 是静态联调档，选择绑定当前安装版本，不能通过改 YAML 在此档直接启用运动、动作或语音；它也不与旧数据自动迁移选项合用。首次用此档安装前必须准备 `robot.env`；后续静态升级继续显式传相同参数。转到普通版本不会自动继承参考服务自启动。静态联调通过后，机构足迹、停车和现场条件需按下文核定，再通过新版本的手动配置路径验收动作和语音。静态档的 HTTPS 端口沿用 `robot.env`（默认 `3445`），不占用旧 GLIM 控制台的 `3444`。

切换还要求机器人本机的 HTTPS `/health` 报告 `referenceStaticReady: true`。这检查网页 API 能否按严格的接口契约解析建图状态，并确认空闲、标定通过且无运动能力。只有私有 Socket 响应、HTTPS 进程启动或远程 `/health` 成功，都不算联调完成。

### 每台机器单独核对标定

1. 首次登录目标机先按参考 README 执行只读预检，核实 SoC1 固件、AIMDK 安装清单、磁盘、ROS 话题及 TF、当前服务和端口。安装前备份旧配置与业务数据，保留现有 AIMDK 地图和厂商标定原件；未核对这些信息时不要切换服务。
2. 用这台机器的厂商标定及同机 ROS TF 核对雷达和 IMU 的安装外参，另存为产品 FAST-LIO 配置。`mapping.fastlio_config` 指向此文件，`mapping.calibration_files` 仅列入经核对的本机资料；关闭 `mapping.extrinsic_est_en`。不要使用参考仓库的外参数值、其他机器的标定或回放工具生成的配置。配置由管理员维护，参考服务账号只读。不要将本机标定提交到 Git 或写入通用安装包。
3. 保持 `motion_enabled: false`，启动后确认私有 Socket `/lingxi/status` 的 `calibration` 为 `matched`，以及配对界面的「本机标定」检查通过。其中 `matched` 表示配置的 `imu_from_lidar` 与当前 ROS 图的 `lidar_chest_front ← lidar_imu_chest_front` 相乘后，距单位矩阵的最大元素差不超过 `0.0001`。配置无效、TF 缺失/无效或二者不符时，建图、地图激活、路径预览和导航准入被禁用；停止和放弃仍可执行。与 TF 一致不等于标定精度合格，现场仍须验证地图尺度、方向、重定位和停车精度。如果外参确有更新，停下任务和建图后更新配置并重启；旧地图先独立验证，不直接作为运动地图使用。

在启用新服务前，可从解包目录运行以下只读探测。先加载实际选用的 Humble 与 AIMDK overlay，在与计划部署相同的 DDS 域和配置下，使用参考服务的 Python 环境，将路径换成本机拟部署的 FAST-LIO YAML：

```bash
python3 -B tools/probe_reference_target.py --fastlio-config /etc/lingxi-next/fastlio-x2.yaml --seconds 6
```

探测仅订阅传感器、运控状态和 TF，查询 AIMDK 服务类型，不调用服务。雷达与 IMU 的订阅使用 FAST-LIO 配置里的可靠性和队列深度。JSON 记录实收样本与时钟、点云布局、三个同时间戳 TF 的配对次数和外参检查结果，不记录原始点云或标定值。`ok: false` 时先排查不可用项；`ok: true` 只代表该短采样窗口的接口检查通过，不能替代固件/SDK 清单核对、现场标定、足迹测量或运动验收。

如果尚未准备本机 FAST-LIO 配置，可以让探测器从当前机器人直接发布的 LiDAR→IMU 静态边生成候选文件。此模式用参考 YAML 作非外参设置模板，要求显式检查本机出厂 `chest_lidar` 数据。若静态变换缺失、冲突、与 TF 查询不一致或其他预检不通过，不写候选文件。候选路径必须是新的隔离文件，不能指向正在使用的配置或厂商原件：

```bash
python3 -B tools/probe_reference_target.py --seconds 8 \
  --fastlio-config /path/to/reference/integrations/fast_lio/config/agibot_x2.yaml \
  --factory-calibration /agibot/protected/param/factory_calib/factory_calibration_params.yml \
  --write-fastlio-config /path/to/private/staging/fastlio-candidate.yaml
```

2026-11-23 的 V1.1.5 目标机出厂文件实际位于上面的 `protected` 路径，仅含机身到雷达的标定，不能从它推出内置 IMU 外参。首次 8 秒观察收到 79 帧点云、1,580 条 IMU 和 778 条运控状态，三路均无非法、超时钟范围或乱序样本；最大 IMU 头间隔 10.644 ms，最近窗口内有 6 帧同时匹配机身/IMU/足踝 TF。三个预期的 AIMDK 服务类型均匹配，候选文件对当时 TF 的核对结果为 `matched`。未发送运动或更改厂商文件。这里只确认可读性、格式与软件外参一致性；并未测量实际安装误差。

重复探测中，默认浅队列曾见 59.835 ms 和 99.966 ms 的 IMU 间隔。改为与 FAST-LIO 设置一致的可靠传输、雷达深度 20、IMU 深度 2000 后，三个 12 秒窗口的最大 IMU 头间隔分别为 11.758、11.078、14.622 ms。第一个窗口仍因一帧雷达时钟偏差失败，后两个窗口通过。保留 50 ms IMU 门槛，不能用短窗口否定现场长时间断流的可能性。

### 2026-11-23 实机静态联调记录

`0.1.50-core-commissioning-20261123-g70ab4eda93a2` 已在该 V1.1.5 SoC1 上按事务安装。只有产品 HTTPS API 和参考服务运行。校验了 LAN 网页的 TLS、未配对拒绝、配对后的 REST 状态和 WSS 状态；页面状态为 `FAST-LIO`、建图空闲且可启动、运动关闭。私有接口报告本机标定匹配，雷达、IMU、AIMDK 和 Nav2 服务可用。装机后的 12 秒只读复测通过，实际收到 97 帧雷达、1,918 条 IMU、116 条运控状态，最大 IMU 头间隔约 19.606 ms，有 7 帧同时匹配机身、IMU 和足踝 TF。

已保留安装前版本、产品配置与数据快照，并单独备份校验厂商地图；原厂标定和地图仍在原位。没有创建或激活现场导航地图。Nav2 节点可发现不等于已经能够规划实际路线；遥控建图保存/重载、GICP 现场定位和尺度、两种运动模式、急停、Android 真机以及动作/语音均未验收。

后续采集尝试在初始化时失败：参考实时栅格子进程等待上游预览组件生成地面参考，而产品关闭该组件，待处理队列满后退出。失败录制保留，不注册为地图。产品适配层现关闭该缓存（包括使用旧配置升级的情况），保留实时点云，停止时先核对扫描、里程计、重力和末帧是否配对，再由参考导出器从同一录制验证初始静止、地面参考和外参并生成栅格。启动后保持站稳，直到显示「采集中」并出现点云；这个提示表示采集就绪，不代表地面参考已通过。网页和 APK 显示本次会话已观察到的配对扫描帧数和录制目录大小；这两个值不是已保存帧数或完成百分比。保存需要额外时间，须等保存回执，不能把失败录制标成地图。

修正后的 `0.1.50-core-commissioning-20261123-g92c99f66e706` 已在同一台机器人上完成静态升级。本机 HTTPS 返回 `referenceStaticReady: true`，私有接口空闲、标定匹配且无运动能力。采用参考服务相同的 ROS 环境做了 12 秒只读复测：117 帧雷达、2,329 条 IMU、119 条运控状态，最大 IMU 头间隔 18.139 ms；有 6 帧匹配同时间戳的机身、IMU 和足踝 TF，AIMDK 服务类型匹配。旧版本、部署快照及失败采集的原始录制仍保留。通过的是静态升级和建图准入复核，截至本次复核没有验证新版建图保存或地图尺度。

随后的现场重试中，记录器只收到约 3 秒的雷达和 IMU（24 帧、427 条），而机身 TF 持续更新。没有生成任何同会话里程计、建图点云或重力输出；独立 15 秒订阅也未收到雷达和 IMU。同期厂商 `hal_sensor_orin` 崩溃栈位于发布 IMU 时的 Fast DDS 2.14.6 共享内存发送路径；当时应用 DDS 配置也包含 SHM，但记录不能证明哪个进程触发厂商缺陷。两份失败录制均保留。应用改为仅用 UDPv4 不会修复或重启已退出的厂商进程。须先隔离产品 SHM，现场按厂商流程恢复传感器，再从静态只读检查开始；未实机验证前不能宣称故障已修复。

UDP 配置生成器已用目标配置只读核对接口白名单、发送和接收缓冲。独立 x86_64 Humble 环境（Fast DDS 2.6.12、仅环回、私有 `/dev/shm`）中的两进程试验收到全部 30 帧 1.28 MB 合成点云与 600 条合成 IMU 消息，未观察到共享内存文件。该试验未接触机器人，不能替代 V1.1.5 的 Fast DDS 2.14.6、实际总线负载和长时间稳定性验证。

### 2026-11-23 UDP-only 实机安装

在同一台 V1.1.5 SoC1（AIMDK `v0.0.0-g14fe2e8`）卸载旧服务并保留可恢复备份后，核对了参考源码的完整提交及 `0.1.50-core-commissioning-20261123-g58049f2b82da` 的包内校验和。暂存包的网页入口和静态资源与归档旧版逐文件一致。已读验本机出厂 `chest_lidar`，并用新 UDPv4 配置和产品服务账号复核本机 FAST-LIO 文件：关闭外参在线估计、与当前雷达—IMU TF 匹配。安装前 12 秒探测收到 120 帧雷达、2,399 条 IMU，最大 IMU 头间隔 17.494 ms，7 组同时间戳机身/IMU/足踝 TF。未调用运控接口。

备份 `robot.env` 后只替换参考服务的 DDS 配置路径。事务安装成功提交，保留了配置和业务数据快照；厂商标定和 FAST-LIO 配置的文件摘要前后一致。仅 HTTPS 与参考服务运行。本机 HTTPS `referenceStaticReady: true`，私有状态 `idle`、`calibration: matched`、`sensorsReady: true`、`mappingReady: true`，未暴露自主运动能力。已连接日志供现场采集观察。这仍是静态验收：不能据此确认连续建图无断流、地图已保存或可重定位。

参考仓库的版权人代码使用 PolyForm Noncommercial 1.0.0 及另行商业授权。用户已确认该仓库的商业复用和分发授权。实际分发引用仓库或其构建产物时，保留原 LICENSE、`Required Notice: Copyright (c) 2026 Mikeaaaa14 (https://github.com/Mikeaaaa14)`，并履行各组件的对应义务，包括 FAST-LIO 的 GPLv2、Patchwork++ 的 BSD 声明和 OctoMap 随附许可证。

## 点位与讲解迁移

先在新地图上重新确认每个点位的米制坐标和朝向。运行迁移脚本的预检查得到每张旧地图的点位 ID。然后提供以旧地图 ID 为键的 JSON，每项填写已保存的 `fastlio-…` 地图 ID，以及按旧点位 ID 索引的 `points`。每个点位填写 `x`、`y`、`yaw`，角度单位是弧度。提供 `--point-assignments FILE` 再执行一次预检查，确认目标地图、点位姿态、讲解顺序与轮数，并抄录输出的 `reviewSha256`。在产品 API 停止、没有其他写入 `state.json` 的进程时，以 `lingxi-next` 服务账户运行同一条命令并加 `--expect-review SHA256 --apply`；输入、坐标或目标状态改变后，必须重新预览。源和确认表须由管理员放在服务账户可读的路径，不改厂商目录权限。已迁移的讲解仍须复核才可运行。机器人原有地图和知识库不搬迁。

应用结果中的 `backupDirectory` 包含原文件、旧 `state.json`（如果之前存在）和前后状态的 SHA-256。回滚先运行下面的只读命令确认范围；要应用回滚，在同一维护窗口、使用同一服务账户追加 `--apply`：

```bash
node tools/migrate-legacy.mjs --destination /var/lib/lingxi-next --rollback BACKUP_DIRECTORY
```

回滚恢复旧业务状态的原始字节，并归档迁移后的副本。只有当前状态仍与这次迁移完成时逐字节一致才允许回滚；如果迁移没有应用、备份损坏或已有后续编辑，工具会拒绝覆盖。之后可对照备份做人工合并。迁移和回滚均不更改厂商地图或 FAST-LIO 地图文件。迁移锁若因进程被强制结束而残留，先检查进程和备份回执，不要直接重复应用。

## 运动联调

在现场记录 `McCommonState` 的消息类型、时间戳、稳定站立状态及遥控输入源行为。当前适配器读取 `/aima/mc/common/state`，按参考接口在 `SetMcInputSource` 请求中填写时间戳，并按 `response.header.code` 取服务返回码。`McCommonState` 的字段已与随附 SDK 对照，实际发布话题、超时和状态切换以目标机测量为准。完成实测外形、地图尺度和目标机标定后，才使用运动配置开展试验。

完成现场审查后，为非静态试验档准备 `motion_enabled: true`、`navigation_mode: point` 和现场测得的 `lingxi_motion_idle_sources`。设置 `lingxi_motion_review` 指向 root 所有、服务可读、组和其他用户不可写的 JSON 文件；应用 YAML 和 FAST-LIO YAML 也需由 root 管理。建图和不发运动的路径预览不需要启用试验档。**静态联调版仍拒绝启动运动配置。**

从新版解包目录以 `lingxi-next` 服务账户对最终拟用配置做只读检查：

```bash
sudo -u lingxi-next python3 -B bridge/reference_target.py \
  --config /etc/lingxi-next/reference.yaml --fastlio-config /etc/lingxi-next/fastlio-x2.yaml
```

输出 `schemaVersion`、`firmware`、`aimdk`、`targetIdentitySha256`、`configSha256` 和 `fastlioConfigSha256`，不输出机器 ID 或外参数值，也不自动批准任何地图。`aimdk` 读取目标机 `/agibot/software/aimdk/version`，不填随附清单的 RC 版本。`targetIdentitySha256` 从本机 OS 机器标识派生，不能把一台机器的审查记录复制到另一台；仍需通过传感器预检及实测足迹确认。管理员只在现场检查完成后，把已经通过审查的 `maps` 加入记录；键为 `fastlio-…` 地图 ID，值为该地图 `metadata.json`、`map.yaml`、`map.pgm`、`localization.ply`、`localization.json` 的 SHA-256。启用前更新 `LINGXI_REFERENCE_CONFIG_SHA256` 并按维护流程重启。运行中如版本、配置、标定或审查改变，撤销当前输出；即使文件改回，旧进程也不恢复运动。重新核对并确认停稳后再启动新进程。

导航页默认选定点，可在任务之间切换 Nav2。两种模式都要求同一运行代次内新鲜的 GICP 位姿、现场障碍点云、代价地图及 AIMDK 状态。暂停和停止会撤销输出，并等待 Nav2 的终态结果、AIMDK 不再选中本进程的输入源，以及至少 3 组递增的停机后运控和里程计观测在稳定窗口内满足停稳条件。`source_timeout_ms` 必须为 250–1000 ms 的整数，首台按参考默认 1000 ms 做现场验证；配置时间不是实测硬件停车时间。

发送运动前将 `motion-in-progress` 标记落盘。异常退出留下的标记不会因安装、API 重启或改回静态档而被系统当作停稳。静态接口保留故障状态，禁止新建图、切图和路径预览；运动档拒绝启动。不得以进程消失、配置超时或零命令替代独立急停和实测停车。若需恢复，先由现场人员确认实体停稳、旧控制源与进程退出，再由维护人员归档标记。断流及进程退出后的厂商输入源到期、实际停稳和最坏停车距离，仍需在两种模式的现场低速窗口分别取证。

可通过 `LINGXI_REFERENCE_ACTIONS=1` 和 `lingxi-next-actions.service` 接入 AIMDK 白名单动作、表情、灵创资源。该单元与 `lingxi-next-aimdk.service` 共用本机端口，启用前检查并停用同端口的旧桥接单元。AgentSDK 的部署继续使用 `lingxi-next-agent.service`，真实语音会话、打断和播放结束仍需在机器人上检查。讲解只有在本次导航的到达与停稳均确认后才推进。

## 验收范围

保存返回成功前，产品会从磁盘重新读取本次导出，核对建图会话、地图版本、坐标参考、栅格尺寸与朝向、PGM 摘要，以及定位点云的数量、原始字节摘要、有限坐标和有效法向。读取栅格、激活地图和预览路径时同样核对；发现不一致会拒绝操作，不删除原始录制或导出文件。目录记录存在并不代表地图可用。这些检查只验证产物一致性，不测量物理尺度，也不能替代新进程使用独立现场扫描验证 GICP 重定位。

旧 X2 静态录包的前 10 帧原始 PointCloud2 和前 256 帧 IMU 已用隔离的 ROS Humble 反序列化。点云每点 32 字节，包含 `ring` UINT16 和 `timestamp` FLOAT64。相对纳秒转换后的最大点时间偏移为 95.181 ms。点云与 IMU 样本最大头时间戳间隔分别约 102.03 ms 与 10.54 ms。

在隔离的 x86_64 Humble 环境中构建了同一参考提交的 FAST-LIO、录制器、代价地图插件、`grid_export_native` 和 `nav_ground_native`。静态实录回放生成 341 帧配对的 `/Odometry`、`/cloud_registered_body` 和 `/fast_lio/gravity`，并录下机身 `/tf` 与 `/tf_static`。离线工具 `tools/replay_reference_mapping.py` 需要独立的仅环回网络命名空间。输入录包可以只读挂载。

参考 FAST-LIO 配置与该录包的 LiDAR–IMU TF 的矩阵元素最大偏差为 0.01198，超过参考导出器的核验阈值。用 `--use-recorded-calibration` 从同一录包的静态 TF 生成仅供回放使用的外参并重新采集后，地面几何检查通过。Patchwork++/OctoMap 导出 366 × 250、分辨率 0.05 m 的栅格和 30,864 个带法向的定位点。现场仍须核对目标机的安装外参。

在新的 Python 进程中重新加载 PLY，以同一录包的一帧扫描验证 GICP 跟踪匹配。给初值增加 0.08 m 和 −0.04 m 的平面偏移，匹配位姿距录包推导值约 0.003 m，点云配准 RMSE 为 0.024 m，内点比例为 98.18%。该结果测量的是同录包内的匹配一致性。现场独立重定位和尺度测量仍需验收。该次离线试验使用 x86_64 Open3D 0.19.0，参考 ARM64 环境依从固定提交的依赖版本。

旧 X2 动态录包也使用同次静态 TF 提取的外参完成离线回放。该环境以 `--rate 0.5 --use-recorded-calibration` 重放时保留了全部 24,446 条 IMU 样本的头时间戳，最大相邻间隔为 39.796 ms。1 倍速试验中，FAST-LIO 收到 90.754 ms 的 IMU 间隔后退出。半速试验保留原有 50 ms 输入阈值，输出 1,219 帧配对的里程计、机身点云及重力。点云、重力和足踝 TF 的地面几何核验通过，导出了 824 × 688、分辨率 0.05 m 的栅格和 279,167 个带法向的定位点。新进程重载后，对同一录包中分散采样的 5 帧施加相同初值偏移，GICP 配准 RMSE 为 0.025–0.033 m，内点比例为 98.27%–98.59%，平面位姿距录包推导值 0.003–0.015 m。实时吞吐和独立场景地图精度仍须在目标机上验证。

在隔离环境的直线路线仿真中，定点控制器在障碍进入、位姿缺失时停止输出非零速度，恢复后完成 2 m 目标，仿真到点误差 0.188 m。Nav2 使用参考的 SmacPlanner2D、RPP 和代价地图插件，在合成地图上生成 41 个路径位姿，收到障碍点云时发布零速度，清障后恢复并返回成功。实际仿真停位距原始请求点 0.251 m，超过该试验的 0.24 m 检查线。产品网关在确认停稳后检查同一定位运行的新鲜地图位姿与原始目标的距离和朝向。回归测试确认上述超差停位不报到达；这不表示 Nav2 已改善了实际停车精度。两项仿真均使用合成位姿与障碍数据。

### X2 现场验收顺序与记录

按下表逐阶段验收；阶段失败时保留故障记录，停止进入下一阶段。当前只通过了目标机静态联调。开始前记录日期、安装版本、SoC1 固件、实装 AIMDK、匿名设备编号、配置摘要、操作者和见证人。原始点云、场地图、实机标定、语音录音和凭据不放进 Git；记录中只引用受限保存的证据。

| 顺序 | 现场操作 | 必须记录的证据 |
|---|---|---|
| 1. 只读复核 | 检查当前版本、静态档、传感器数据与本机标定。动作和语音保持禁用。 | 本机 HTTPS `referenceStaticReady: true`；只读探测的 IMU 最大间隔、配对 TF、标定和接口结果。静态通过不代表可行走。 |
| 2. 遥控建图 | 现场人员确认独立遥控和急停可用；保持自主运动关闭，从配对网页开始建图，只用外置遥控器移动，停稳后保存。 | 实时扫描、无断流/未处理故障、保存回执和地图 ID；点云、栅格、分辨率、原点与朝向，至少用实测距离和方向核对尺度及坐标。 |
| 3. 重载、定位与路径 | 建图结束后由管理员用新参考服务进程重载同一地图，用独立现场扫描定位；继续禁用运动，预览空旷路线和被障碍阻断的路线。 | 地图文件摘要一致、独立测量的位姿误差、活动地图和规划结果的绑定。服务发现或单张路线截图不是重定位或避障证据。 |
| 4. 两种低速导航 | 以上通过、场地清空、风险及低速阈值由现场核定后，按运动联调章节分别验收定点和 Nav2。现场人员持独立急停；只在停稳且无任务时切换。 | 每种模式分别记录成功数/总试跑数、每次停稳后的实测位置误差和最短角度差、最小净空、触发至停稳的实测移动距离。记录动态障碍下减速/停车与重规划、接管、急停和断流；控制器回执或零速度命令本身不是停稳证据。 |
| 5. 数据与客户端 | 在新图逐点复核后预览迁移；按维护窗口要求应用及演练回滚。在桌面 Web 和 Android 真机验证配对、证书信任、地图和任务以及断网恢复。 | 迁移审查和备份摘要；客户端版本、横屏截图、状态恢复。重连不能重发旧任务。 |
| 6. 讲解、动作和语音 | 在单独的监护窗口验收真实 AgentSDK 回调、讲解打断、安静 5 秒恢复、人工暂停、知识检索/拒答及白名单动作。 | 100 次有效唤醒 ≥95 次成功，8 小时噪声误唤醒 ≤1 次，模糊或越权误执行 0 次。声学回录测量语音结束至首段可听反馈，P95 ≤2 秒；同时记录缺样或无反馈，不能只用 SDK 状态或筛掉失败轮次判定。 |

每条路线先填写 `地图 ID / 模式 / 路线 / 足迹版本 / 限速 / 允许位置与航向误差 / 最小允许净空 / 最大允许停车距离`，再填写 `实测值 / 通过、失败或未验证 / 停止原因 / 证据摘要`。阈值按本机实测足迹及现场条件确认，不使用录包值或仿真门槛。未完成的阶段标记「未验证」，不标记「通过」。

语音阶段的 WAV 取证、采样帧标注、P95 和漏响计分见[现场语音验收取证](X2_ACOUSTIC_ACCEPTANCE.md)。
