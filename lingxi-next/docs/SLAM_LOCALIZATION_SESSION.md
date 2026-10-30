# 定位会话与管理请求

日期：2026-10-30。机器人保持断电，本阶段只完成电脑端离线实现和测试。
实现为 `robot-slam/lingxi_slam/localization_session.py`，复用既有
`LocalizationTracker` 和固定版本的 small-gicp，不改动原生建图及已归档证据。

## 行为

每个实例固定绑定输入会话、输入模式、地图 ID、地图收据摘要、地图点云摘要及
传感器配置摘要。调用者必须先验证实际文件；本模块只检查身份一致性，
不把“提供一个 SHA256 字符串”当成文件完整性证明。
切换地图、配置或输入会话需要关闭旧实例并创建新实例。

管理调用只读缓存或写入一个有界的最新请求槽，不构造、调用或销毁原生配准器。
工作线程负责构造配准器、应用初值、处理纠正点云、空闲检查和清理。

- 设置初值时必须提交明确的 `map_from_sensor` 及当前显示的参考帧号。
  工作线程仅接受仍新鲜且帧号相同的自有点云副本；过期或错帧则失定位。
- 初值请求被接受后先显示 `seed_requested`；工作线程确认后才进入
  `acquiring`，连续三次配准通过后才显示诊断跟踪位姿。
- 暂停、停止和重设初值立即递增管理代次并清空可见位姿。
  已在计算的旧结果、旧异常和旧确认次数不能覆盖新状态。
- 暂停后需要明确的新初值才能恢复；停止为该实例的终态。
  多个尚未应用的请求只保留最新一项，不排队执行旧初值。
- 每次写操作都检查 `expectedGeneration`。两个客户端同时提交、
  重试旧请求或使用旧地图身份时，落后的操作会被拒绝。
- 无新点云时，工作线程的 `tick()` 和管理状态查询均可撤销过期位姿；
  配准卡住时，管理查询也不会继续展示过期位置。
- 错会话或错模式点云不能提供初值参考；重复帧不能刷新定位期限。
  非法或过期的本会话点云撤销当前跟踪。
- 只有原生清理返回且引用在所属线程释放后，才报告 `cleanupComplete`；
  清理异常报告失败。原生硬阻塞仍由外层进程监督终止。

## 内部请求约定

`dispatch(session, value)` 接受严格 JSON 形状，核心本身不开放网络接口。
现有离线运行接入通过私有 Unix socket 使用它，详见 `SLAM_LOCALIZATION_RUNTIME.md`；
尚未开放 HTTP。
状态查询为 `{"action":"state"}`；返回绑定身份、管理代次、状态、
新鲜参考帧、确认次数及诊断位姿的独立副本。
所有状态始终包含 `localizationReady=false`、`navigationReady=false`。

暂停和停止请求字段为 `action`、`binding`、`expectedGeneration`。
初值请求另外包含 `mapFromSensor`（4×4 有限刚体矩阵）及 `referenceFrame`。
拒绝多余字段、布尔代次、布尔或字符串矩阵、非有限数值及非法变换。
错误响应是明确的内部错误码；接受请求不等于完成定位或完成原生清理。

绑定字段为 `session`、`input_mode`、`map_id`、`receipt_sha256`、
`points_sha256`、`profile_sha256`，来自受信任的运行管理方，
不能由网页提供任意文件路径。

运行接入时，在现有受监督子进程的同一原生点云工作线程调用：

```python
session.open_worker()
try:
    # On corrected points: session.observe(packet, session=..., input_mode=...,
    #                                      received_at=host_monotonic_time)
    # On idle worker iterations: session.tick()
    pass
finally:
    session.close_worker()
```

传感器回调仍只负责已有的有界输入传输；管理线程调用 `dispatch()`。
`received_at` 必须来自同机单调时钟，不能用录制算法时间替代。
本模块没有另建工作线程或进程监督器，不能脱离现有进程监督直接用于机器人。

## 验证与范围

`tools/tests/test_slam_localization_session.py` 使用真实 small-gicp 和已知运动的
合成房间点云。并发测试在独立工作线程执行真实配准，并在计算期间注入暂停、
停止、重设初值、超时或旧调用异常，检查立即撤销及迟到结果隔离。
另覆盖绑定全部字段、代次冲突、过期初值、输入来源、独立副本、
初始化期间停止以及清理失败。

在已有隔离环境中复现：

```sh
python -B -m unittest discover -s tools/tests -p test_slam_localization_session.py -v
```

首次 16 项测试因内部状态字段与 `state()` 方法重名而失败，修正为独立的
`phase` 字段后 16 项通过；补充并发与清理检查后，完整 Linux 工具回归共
911 项，907 项通过、4 项明确跳过，其中本模块 20 项全部通过。
完整命令与范围见 `VALIDATION.md`。
没有修改测试门槛、放宽定位质量要求或将首轮失败计为通过。

这是一段可复用的定位会话核心；后续已加入受监督的离线录制定位子进程和私有
Unix 控制，见 `SLAM_LOCALIZATION_RUNTIME.md`。尚未接入管理 API、网页初值设置
或 ARM64 交付包。此前录制序列结果仍是
`SLAM_RECORDED_LOCALIZATION.md` 记载的独立历史阶段，本轮没有重跑或
宣称其覆盖新模块。产品与 APK 保持 0.1.19。

进程监督和地图目录校验的后续结果见运行链路文档，下一阶段接管理 API、
网页初值与状态显示。
实机阶段仍需核对时间、外参和传感器到底座坐标，验证独立位置对照、
重复场景拒绝及 ARM64 资源；本次并发测试不替代这些验收。
