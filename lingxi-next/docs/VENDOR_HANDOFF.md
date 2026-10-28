# 原生导航接口阻断问题单

检查日期：2026-10-28。对象为用户提供的灵犀 X2 旗舰版。
本报告为 0.1.19 联调包生成后的补充记录；既有安装包和 APK 未重新生成或覆盖。
未安装新产品、停止旧服务、修改厂商配置/授权、切换系统模式或下发运动/建图请求。

后续范围更新：用户已明确确认未选配 SLAM，现要求先验证独立开源方案。
本问题单保留为原生方案的历史诊断，不再把恢复该选装功能授权当作独立开发前提。
新路线仍需确认公开传感器、运动接口及官方开发模式的支持边界，
详见 OPEN_SOURCE_NAV_EVALUATION.md；没有修改或规避厂商授权。

## 需要售后处理的问题

原生 `auto_integrated` 与 `map_manager` 启动日志均报告：

```text
module can't start because license denied
SignatureInvalid(5)
```

错误还涉及授权来源加载失败和未找到授权标识，报告不包含具体标识或文件内容。
请确认相应选装功能与官方授权链，并恢复适配该设备及原生软件的有效官方授权。
该错误不能单独证明用户未购买功能；启动日志也不能证明整机其他组件的授权状态。

## 版本与运行证据

| 检查 | 只读结果 |
|---|---|
| SoC1 软件包标识 | `release-lx2501_3_t2d5-soc1-v1.1.5` |
| SoC1 软件清单 | `version: v1.1.5`，清单仅含 SoC1 |
| 清单中的 AIMDK 来源 release | `v1.1.0-rc10` |
| 用户独立 SDK 附件标识 | `aimdk v1.1.0-gfaf4c94` |
| 当前系统状态 | 公开 `GetSystemState` 成功响应，`Business` |
| `auto_integrated` | 原生进程及 ROS 节点存在，但启动报告订阅表为空，组件有授权启动错误 |
| `map_manager` | 原生进程存在，启动日志同样有授权启动错误 |
| `/integrated_command` | 未发现接收端 |
| `/relocalization_pose` | 未发现接收端 |
| `/aimdk_5Fmsgs/srv/GetStoredMapByName` | 未发现服务端 |
| 旧 `/aimdk_5Fmsgs/srv/GetStoredMap` | 可发现，但不是附件 v1.1.0 的服务类型 |
| DDS | 独立探测使用厂商相同配置，白名单内网接口存在，缺失接口情况不变 |
| 雷达 | 已收到真实 `PointCloud2`；不能据此证明 SLAM/定位或导航就绪 |

软件包版本、SDK 版本和任务组件版本不可直接按数字大小推断兼容关系。
请同时确认该原生固件构建与 AIMDK v1.1.0 正式接口的配套要求。

## 售后核对范围

1. 确认上述两个原生组件授权启动错误的官方修复方案及适用软件构建。
2. 若需更新固件、授权或重启原生组件，先说明备份、地图保留与回滚步骤，再安排现场操作。
3. 修复后核对公开建图命令、重定位与按名称查图接口；不以旧服务改名或内部 Proto RPC 替代。
4. 接口就绪后，现场使用外置遥控器进行建图，检查真实扫描、SLAM 状态、地图坐标 TF、
   保存查询和重载，再做短距导航与急停验收。首次建图前不要求已经存在地图坐标定位数据。

本产品保留厂商导航、安全和地图文件。当前没有执行 SDK 状态迁移示例；
附件文档说明 `Develop_Nav` 会停用原生导航/建图等能力，不作为本方案的修复步骤。

## 证据位置

- `/agibot/software/firmware_version`、`/agibot/software/metadata.yaml`
- `/agibot/log/latest/auto_integrated/auto_integrated.log`
- `/agibot/log/latest/map_manager/map_manager.log`
- 附件 SDK 离线文档 `Interface/perception/SLAM.html`：
  标记 SLAM 为选装，并提示联系售后技术支持。
- 附件 SDK 离线文档 `Interface/system_state/index.html`：系统模式含义。

完整许可证、UUID、签名、密钥、设备序列号、地图及语音业务数据均未纳入本报告。
