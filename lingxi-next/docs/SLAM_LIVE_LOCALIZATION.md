# 实时传感器定位运行链路

日期：2026-10-30。本轮已连接实际 ARM64/Humble 机器人，验证实时雷达/IMU
输入与配对 HTTPS 定位控制。没有发送行走指令，没有替换厂商 AIMDK、服务或配置。
这不是保存地图定位精度、独立导航或完整产品验收，安装包/APK 保持 0.1.19。

## 实现范围

`managed_localization.py` 现在可明确选择普通用户 ARM64/Humble 实时输入，
通过既有冻结 GLIM 原生库、纠正点扩展、SensorWorker 和定位会话组合运行。
录制入口 `controlled_localization.py run` 仍只接受录制；没有默认打开实时订阅。
`live_localization.py` 不创建运动、地图或 TF 发布器。

实时与录制共用同一 HTTPS 状态及初值、暂停、停止接口。
HTTP 适配接受 `live` 和 `recorded`，继续拒绝 `synthetic`。
网页按状态显示实时传感器、录制数据或等待输入，不把实时输入写成录制数据。
收到普通问答或业务指令时如何调度导航，不属于该隔离诊断入口。

父进程先验证文件、原生库、DDS 配置和地图输入，不要求尚未创建的子进程会话 ID。
子进程必须有监督器生成的会话 ID，并再次核对受控 Humble 环境。
ROS 初始化所需 HOME 从本机 POSIX 账户数据库获得，不继承调用者提供的 HOME。
没有放宽 2 秒进度监测、输入完整性或旧代次拒绝规则。

退出时先撤销 HTTP，再撤销定位请求接收，最后由工作线程释放原生资源。
请求接收终止与点云复制完成交错时，迟到帧也不能重建参考帧或位姿。
人工暂停不自动恢复；停止后本会话不再接受初值。

## 有界运行

配置沿用 `MappingPlatform` ARM64 字段：`platform=arm64-humble`、
`inputMode=live`、`root`、`coreRoot`、`mappingRoot`、`pointAdapter`、
`pointManifestSha256`、`hypothesis`，另需 `maxSeconds`，不含录制 `reference/rate`。
依赖仍须是已核对的私有冻结库、配置、DDS 及 small-gicp 1.0.1。
不得把示例或未知硬件配置直接当作已标定生产配置。

`managed_localization.py` 参数与录制统一入口相同，见
[保存地图定位运行链路](SLAM_LOCALIZATION_RUNTIME.md)。
`--seconds` 为 API 就绪后的 5–120 秒验证期限；
实时 `maxSeconds` 须为 5–300 秒且至少比 `--seconds` 长 45 秒，覆盖启动和收尾。
正常到期记录 `intervalComplete=true`、`inputComplete=false`，不声称完整处理无限数据流。
外部停止仍须校验终态报告；缺失、损坏报告或异常退出均不能报告成功。

保存地图的来源可为录制或实时，与当前定位输入模式独立核对。
允许复用录制地图不等于证明其属于当前现场：地图/传感器关联标志仍为 false。
当前没有自动初值、全局定位或自动恢复。需要已知的地图关联和实际初值后，
才能开始有意义的地图跟踪验证。

## 本轮实机结果

`tools/run_live_localization_arm64_trial.py` 复用已保留的 ARM64 库、Node、
隔离 Python 环境及地图，上传源码到新私有目录并逐文件校验。
`tools/check_managed_live_localization.py` 使用一次性证书、显式 CA 校验和随机配对码，
只监听机器人回环，不提交初值。

| 检查 | 实际结果 |
| --- | --- |
| 实时消息 | 接收 3,211 条 IMU、161 帧点云；完成 3,365 条、停止丢弃尾部 7 条，拒收 0；队列清空 |
| 原生处理 | 160 次扫描、129 个里程计位姿；create/close 各 1 次，单一非主工作线程 |
| API | 未认证 401、配对 201；连续观察参考帧 1–5，初值请求为 0 |
| 状态撤销 | 暂停和停止 202，旧代次 409；停止后 222 次查询未恢复位姿 |
| 收尾 | 15 秒 API 验证期限正常结束；API/原生退出 0，无强杀、无进程或监控线程残留；输入重新核对 |
| 队列 | 观察到的最大排队时间约 126 ms；不是端到端传感器延迟或长期性能保证 |

129 个位姿是 GLIM 里程计输出，不是与已保存地图匹配出的机器人位置。
本轮没有提交初值，因此始终不产生地图定位位姿。
日志仍有 IMU prediction quality 警告，可能涉及外参或偏置；本轮没有调参或将其忽略为已解决。
系统 SciPy 1.8.0 对 NumPy 1.26.1 的版本范围警告也保留，未修改系统依赖。

脱敏证据见 [实机摘要](slam-live-localization-arm64-evidence.json)。
私有报告、地图、路径、证书及凭据均不进入仓库。可在保留相同执行源码时离线复核：

```sh
python -B tools/export_live_localization_evidence.py \
  --trial /private/collected-live-trial \
  --output /private/new-aggregate.json
```

该命令不连接机器人、不重跑原生库。另有 4 项保留报告测试，覆盖摘要脱敏、
进程残留、参考帧/初值误报、报告字节变化和上传源码变化。

## 回归与下一步

### 追加实测：原始扫描时间与底盘 TF 输入

已使用独立的 `robot-runtime/dist/localization-main.mjs` 再次完成 ARM64
实时测试，启用显式 `basePose.metresPerMapUnit=1.0`，不提交初值、不发送运动命令。
尺度 1.0 只是本次诊断假设，不代表实测标定。

- 收到 3,239 条 IMU、163 帧雷达；完成 3,398 条消息，停止时丢弃尾部 4 条，拒收 0。
- 原生处理 161 次扫描，输出 131 个里程计位姿，create/close 各 1 次。
- 接收 2 个静态 TF、2,280 个动态 TF；160 个有效扫描时间中，159 次精确时间查询可用，
  1 次不可用并拒绝，没有用最新值或单位矩阵填补；无无效时间、无效变换或查询异常。
- 暂停、停止、旧代次拒绝通过；停止后 220 次查询未恢复位姿。两进程退出 0，
  无强杀、无残留进程，扫描时间和动态 TF 缓存均清空。
- 修复父进程收尾校验漏传可选尺度的问题；子进程与父进程现在使用同一显式配置。

此次通过的是传感器和坐标输入接线，不是保存地图中的底盘位姿精度。
尚未提交初值，`localizationReady`、`navigationReady` 和当前现场地图关联均为 false。
源文件哈希、计数及退出记录见 [追加实测摘要](slam-live-base-arm64-evidence.json)，
不含机器人 IP、私有路径、坐标、会话凭据或证书。

使用同一执行源码可离线复核，命令沿用上文的证据导出工具；追加保留报告测试时设置
`LINGXI_LIVE_LOCALIZATION_EVIDENCE_TRIAL` 为新私有采集目录，并设置
`LINGXI_LIVE_LOCALIZATION_PUBLISHED=docs/slam-live-base-arm64-evidence.json`。
原有实测报告保留，不覆盖不同源码版本的历史结果。

当前源码 Linux Python 全量运行 1,029 项，1,019 通过、10 项显式跳过，
耗时约 168 秒，包含上述追加实测报告的 5 项离线复核。
使用 `LINGXI_GLIM_UPSTREAM_ROOT` 指向已核验干净的 Linux 上游副本，
避免 Windows 工作树换行差异；未修改原副本或放宽源码完整性校验。
首次回归发现的过期 TF2 证据哈希和配置换行问题均已处理，当前复跑无失败。

### 此前阶段记录

- Linux 真实进程测试 19/19，通过到期、中断、双方故障、错误/缺失报告和输入变化清理。
- 定位 API 的 Windows Vitest 36 通过、9 项 Linux 专用检查跳过。
  随后全量 Vitest 258 通过、9 项跳过。
- Python 全量 961 项：950 通过、10 项跳过、1 项本地上游配置清洁度错误。
  该副本只有 CRLF/LF 差异；保留原副本，另建 Linux 干净副本后相关 15 项全部通过。
  没有把首次全量写为全部通过，也没有为此更改生产校验。
- 新增保留实测报告测试 4/4 通过；网页和类型构建通过。
- 11 阶段合成浏览器回归通过，1440×900 桌面和 844×390 横屏点云非空，
  无横向溢出、区域重叠或浏览器错误；不是机器人 LAN 或实际 Android 验收。

后续优先完成当前现场地图与传感器配置绑定、ARM64 原生重载及底盘坐标标定，
再做明确初值下的动态跟踪、重定位和导航集成。
本轮 `localizationReady`、`navigationReady`、`mapProfileAssociationVerified` 均保持 false。
