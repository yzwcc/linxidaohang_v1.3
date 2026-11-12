# X2 旗舰机身包络与低速导航配置

核对[官方规格表](https://x2-aimdk.agibot.com/zh-cn/latest/about_agibot_X2/robot_specifications.html)：
X2 Ultra（旗舰版）公布尺寸为高 1310 × 宽 460 × 厚 210 mm。
该规格没有给出测量姿态、运动包络或 `base_link` 相对外形中心的偏移；
导航净空采用下面的模型碰撞包络，不直接把公布宽厚作为避障 footprint。

用户授权按官方型号资料选择配置。采用 AIMDK v1.1.0 SDK 附带的
`X2_URDF-v1.3.0/x2_ultra.urdf`，没有采用 v1.4 的 Ultra-N 型号。
该 URDF 的 Git blob SHA-1 为 `bdeab97172e255f057e62fcb9440934ecbebfacb`，
与[官方仓库固定提交](https://github.com/AgibotTech/agibot_x2_urdf/blob/575cc6b988f976c23550e0db85aa1e5475d3652d/X2_URDF-v1.3.0/x2_ultra.urdf)一致。
网格取自用户 SDK，逐文件 SHA-256 随声明记录，不将厂商网格或业务数据加入本仓库。

`deploy/profiles/x2-ultra-v1.3/footprint.json` 记录零关节角下各碰撞网格的保守
包围盒；沿 URDF 关节原点组合变换，坐标单位为米，根坐标系为 `base_link`。
前后约 0.309 m、左右约 0.679 m、上下约 1.315 m，水平包络半径约 0.345 m。
包围盒中心在 `base_link` 下约为 (-0.02588, -0.000053, -0.01803) m。
这不是实测运动包络或质心；不把该偏移作为胸部雷达外参。
动态腰部/雷达变换仍必须来自机器人真实 TF。

| 首轮配置 | 值 |
| --- | --- |
| 机身净空半径 | 0.45 m |
| 额外停车余量 | 0.15 m |
| 前进速度上限 | 0.10 m/s |
| 侧向速度 | 0 |
| 旋转速度上限 | 0.20 rad/s |
| 直线加速度 / 减速度 | 0.10 / 0.20 m/s² |
| 角加速度 | 0.40 rad/s² |
| 指令有效时间 / 首条指令期限 | 0.15 / 0.75 s |
| 障碍物高度范围 | 0.1–1.4 m，沿用产品默认值 |

配置用 footprint 文件的原始字节 SHA-256 绑定现有 Nav2 加载器。
现有加载器据净空半径加停车余量生成外切 16 边形，禁止侧走、倒车及将未知地面视为空闲。
安装包包含这些配置供调试选择；不自动覆盖现场 `/etc` 配置、不修改地图回执、
不将尺度或标定状态设为通过，也不因此启动运动服务。
后续启用导航仍需完整的地图/标定关联、定位、TF、地形和停止回执。

复算方法（输出必须是新目录）：

```powershell
rtk proxy python tools/prepare_x2_navigation_profile.py --sdk-zip <用户SDK压缩包> --output <新目录>
```

社区参考：[Nova-Valley/agibot-x2-navigation](https://github.com/Nova-Valley/agibot-x2-navigation/tree/9ad1ad8af1b47b1d8fda42c3ca9c828bc5625629)
可用于 X2 话题及 AIMDK 接入对照。其示例 footprint 小于本次官方模型包络，
速度较高、允许未知空间且高度范围不同，因此没有直接套用。沿用现有 GLIM / small_gicp / Nav2 实现。

## 本轮部署与验证

2026-11-12 已部署 `0.1.36-independent-commissioning-20261112`。
将上述配置复制至 `/etc/lingxi-next/footprint.json` 和
`/etc/lingxi-next/nav2-profile.json`，备份原 `robot.env` 后只更新对应两项文件路径。
安装前用部署目录中的实际 Nav2 配置加载器核验文件关联及速度参数。
此次配置没有启动导航服务或发送运动指令。

实时定位进程保留 GLIM 的两线程 OpenMP 预算，将微小矩阵运算使用的 OpenBLAS
线程数设为 1，避免额外 BLAS 工作线程争抢传感器处理时间。
在用户确认的地图“11.12”A 点完成 181.73 秒静止持续定位：

- 全程 `tracking`，最后观测帧 1957，worker 故障为空。
- 接收 IMU 39837 条、点云 1992 帧；拒绝消息 0，关闭时丢弃尾部 5 条。
- 最大已观测队列年龄 0.107106 秒；IMU/点云最大操作时间分别为 0.086349/0.109041 秒。
- 正常停止、清理完成，原地图文件校验值未改变。

另一次 20.18 秒只读复测确认 `basePose.status=available`，即实时雷达到机身的
时间匹配 TF 转换可以得到机身位姿。两轮都没有发送运动指令。
这只证明静止定位的持续运行及机身位姿链路；移动精度、地图物理尺度、动态包络、
地形输入与自动导航尚未验收，`physicalScaleValidated` 和 `navigationReady` 仍为 false。
下一步使用用户遥控的 A→B 路线核对位移（用户估计约 3.15 m）及回到 A 的偏差，
通过后再推进低速自主路线测试。

离线检查：TypeScript/Web/API 构建通过；前端/API 测试 771 通过、26 跳过；
Linux 定向 Python 测试 33 通过、10 跳过，包含机身包络和真实 Nav2 配置集成检查。
