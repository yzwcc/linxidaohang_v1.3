# 本地验证记录

日期：2026-10-28；环境：Windows x64。范围仅为新目录 `lingxi-next/`；
没有部署或移动真实机器人，没有覆盖 AIMDK，也没有恢复或修改旧版本业务文件。

| 检查 | 结果 |
|---|---|
| TypeScript 前端/API 类型检查与 Vite 构建 | 通过 |
| Vitest | 116 项通过，覆盖 WebSocket 恢复、诊断隐私与容量、VAD EOS 到 SDK 播放态测量及缺失/过期证据、迁移/地图 ID、语音会话与白名单、知识依据、任务生命周期、雷达流和动作资源互锁 |
| Python AIMDK 桥接测试 | 36 项通过；验证激光扫描/采样时刻 TF（含 50ms 有界等待）、点迹、真实只读 SQLite 登记、不暴露未提交数据、未知结构拒绝、大地图 ID、像素转换、旧定位拒绝、位姿单次发布、未确认状态互斥，以及预设动作状态、表情回执和灵创服务结果语义 |
| Java JUnit | 19 项通过：唤醒与静默状态次序/断线重放/有效期 8 项、取消先于播报与过期请求拒绝 3 项、TTS 事件绑定/时序/结束确认 6 项、VAD EOS 与对话事件匹配/过期拒绝 2 项 |
| 官方 AgentSDK 编译 | 通过，固定实际 JAR SHA256；不使用文档中旧版 Maven 示例 |
| npm audit（生产依赖） | 0 项漏洞；不代表 Java 或厂商系统漏洞审计 |
| Linux 部署脚本 | WSL Ubuntu x86_64、私有 mount namespace 与临时 chroot 中以 root 运行真实安装/升级/回滚及诊断脚本；1 个生命周期测试含 10 个场景通过，包括自定义 HTTPS 端口读取且不泄露配对码。文件、证书、权限、系统账号、校验和和备份真实；架构、运行时入口、systemd 与健康响应为替身，不验证 ARM64 执行或真实服务 |
| 浏览器界面 | 建图开始/保存、地图切换、点位编辑、讲解重排保存；1440×960、960×540、844×390 视口核对 |
| 语音诊断区块 | 1440×960 与 844×390 均无横向溢出；WCAG 2 A/AA axe 检查 0 项违规。演示 API 未生成真实语音数据，指标只验证展示与接口测试 |
| 浏览器布局 | 横屏无横向文档溢出；地图管理支持缩放、拖动与坐标选取；扫图页绘制独立 LaserScan/PointCloud2 激光点迹，不以栅格生成点云；1440×960 / 844×390 下有实时 Canvas 像素并支持缩放、拖动；844×390 横屏下采集控制与扫描区并排显示 |
| 雷达断流界面 | 停止模拟 API 后画布清空、显示断流提示；重启后重新配对可恢复新扫描 |
| axe WCAG 2 A/AA | 讲解页、导航页、桌面与横屏扫图页检查无违规；不代表人工无障碍认证 |
| 0.1.1 重定位入口 | 浏览器点击所填世界位姿重定位收到模拟 API 200；桌面与 844×390 地图管理截图检查、横向溢出检查和 axe 均通过 |
| 0.1.13 ARM64 commissioning 包 | 构建后由 `tools/verify-release.py` 审计文件数、逐文件 SHA256、ELF64 AArch64 运行时、执行权限和固定 AgentSDK JAR；包标记 `productionAccepted: false` |
| 0.1.13 Android commissioning APK | 构建后由 `tools/verify-android.py` 审计签名、`sensorLandscape`、禁明文流量、禁备份和网页资源；未在 Android 真机验收 |
| VAD 到 SDK 播放态诊断 | 自动测试覆盖 EOS/ASR 同事件关联、缺失/过期/未来时间拒绝、SDK `playing` 首音与独立 P95 样本数；不等同声学首音验收 |

包文件的存在、逐文件校验和、ARM64 ELF 与权限，以 `tools/verify-release.py` 对实际
归档的输出为准。APK 另用 Android Build Tools 检查签名、清单与横屏配置。
散列记录在交付文件旁的 `.sha256` 和机器人包内 `SHA256SUMS`，不在本文嵌入自引用散列。
本次 ARM64 文件为 `artifacts/lingxi-next-0.1.13-commissioning-20261028-linux-arm64.tar.gz`，
Android 文件为 `artifacts/lingxi-next-0.1.13-commissioning.apk`。各自 SHA256 以交付文件旁的
`.sha256` 文件为准。

尚未验证：目标机器人实际话题、保存重载与坐标方向、短距导航/急停、
ASR 置信度或官方拒识证据、目标设备 TTS 关联语义、预设动作/表情/灵创资源的实机调用与停止确认、云端凭据、
Android 真机、目标 ARM64 上的安装/升级/回滚与真实服务启动、声学误唤醒与声学回录
VAD 结束到首段可听反馈 P95。管理台 SDK 事件指标仍需在目标机确认 `vad_status`
EOS 与 TTS `event_id` 的实际关联语义。
因此本版本只允许 commissioning 联调安装，不能据此认定产品交付完成。

可在 Linux 开发环境以 root 执行 `python3 tools/test-deploy-linux.py` 复现部署脚本测试。
脚本只在隔离 mount namespace 中修改一次性 chroot，不访问机器人或宿主 systemd；
namespace 不可用即失败，不会退化为直接安装到宿主。
