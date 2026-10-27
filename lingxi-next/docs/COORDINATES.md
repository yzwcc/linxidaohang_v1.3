# X2 地图登记与重定位

核对日期：2026-10-28。依据 X2 AIMDK 1.1.0 官方 SLAM、导航文档及用户提供附件里的
`relocate.py`、`PncTaskRequest.msg`、`TaskMasterState.msg`，独立实现适配。

## 地图登记

默认目录 `/agibot/data/var/MapManagerModule`。地图名称与 ID 来自 `map.db` 的 `map` 表
`map_id/map_name` 字段。本产品使用 SQLite `mode=ro` 和 `query_only`，不写数据库。
不存在数据库的兼容环境才列出该目录的 YAML 文件；数据库存在但权限/结构异常时报告不可用。
地图 ID 不再受 32 位上限限制；通过 JSON 的 ID 要求为正的 JavaScript 安全整数。
当前列表最多读取 100 张地图。地图保存后仍必须由 `GetStoredMapByName` 确认。

## 坐标区别

点位、导航目标与前端编辑始终使用米制 Map 世界坐标和 ROS yaw（弧度）。
重定位的 `/relocalization_pose` 是厂商特定接口，位置使用图像像素。
读取 `<map_id>/grid_map_info.txt` 三行数值：

```text
20
100 150
400 500
```

依次为每米像素数 `s`、建图原点像素 `(u0,v0)`、图像宽高。也接受不含额外数字的字段标签。
对填写的位置 `(x,y)`，发送 `u=u0+x*s`，`v=v0-y*s`。
例如 `(2,3)` 米对应 `(140,90)` 像素。朝向使用 ROS yaw 四元数 `z=sin(yaw/2), w=cos(yaw/2)`；
不把像素 Y 反转应用到世界 yaw。目标机器上的朝向语义须实机核对。
无参数的“从建图起点定位”使用 `(u0,v0)` 与零朝向。
异常格式、非有限数值、无效地图 ID、越界路径或地图范围外的位置均拒绝发送。

## 确认流程

1. 查询地图，检查文件与两个命令话题的接收者，排除建图/导航/未确认切图。
2. 保留切图互斥，清除旧地图、点迹和定位显示，发送 `start_relocalization:<map_id>`。
3. 等待约一秒，单次发布 `geometry_msgs/Pose`，不自动重试不确定的发送。
4. 要求观察到 SLAM 重定位状态，随后是目标地图的定位状态；同时收到初始位姿发送后采样的、
   时间有效的 `frame_id=map` 里程计。旧消息、错误坐标系、无效四元数不能通过。
5. 超时保留互斥并禁止导航；现场检查原生定位后再重启本项目桥接。

起点估计不驱动机器人行走，机器人实际位置必须与估计一致。扫图移动仍由外置遥控器负责。
首次建图只要求地图服务、SLAM 命令/状态链路与激光就绪，不要求先完成定位。

参考：

- https://x2-aimdk.agibot.com/zh-cn/latest/Interface/perception/SLAM.html
- https://x2-aimdk.agibot.com/zh-cn/latest/Interface/perception/navigation.html
