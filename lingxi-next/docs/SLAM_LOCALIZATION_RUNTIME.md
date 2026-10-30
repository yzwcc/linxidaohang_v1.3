# 保存地图定位运行链路

日期：2026-10-30。机器人保持断电，本阶段只在电脑端使用保留的录制数据。
新增入口为 `robot-runtime/python/controlled_localization.py`；
私有控制和地图加载位于 `localization_control.py`。
这不是导航启动器，也没有更新生产安装包或 Android APK。

## 已实现

运行入口复用既有 `MappingPlatform`、GLIM 纠正点扩展、传感器工作线程和
`ProcessSupervisor`，将保存地图与定位会话组合到同一个受监督子进程中。
small-gicp 固定为 1.0.1，原生配准器由点云工作线程创建、调用和释放，
控制 socket 线程只查询缓存或提交有界请求。
没有复制旧版业务代码，也没有修改冻结的 GLIM 原生适配器。

调用方必须明确提供地图 ID、收据和当前传感器配置的 SHA256。
加载器核对收据字段、帧和子图计数、原生地图文件清单、点云大小及摘要，
拒绝符号链接、重复 JSON 字段、非有限点和超限输入，再创建自有不可写点云副本。
地图树最多 4,096 项、64 MiB，点数另受配准配置限制；
这些是当前诊断入口的容量限制，不是完整场馆地图的产品容量承诺。
结束前再次核对配置、源码、库和地图输入；父进程检查完整回调记账、
输入绑定及定位清理结果，异常或强制终止不能报告成功。

旧地图收据尚未证明建图时使用了当前传感器配置。
本入口只绑定并验证当前提供的配置文件，始终保留
`mapProfileAssociationVerified=false`，不会据此推断标定已经确认。

## 控制与失效

子进程在输出目录创建 `localization.sock`。
目录权限必须为 0700，socket 为 0600，仅接受同 UID 的 Linux 本地连接，
每次请求有大小和读取时间限制；不监听 TCP，不暴露 HTTP。
日志、状态矩阵和完整报告留在私有输出目录，不上传 GitHub。

请求使用 `SLAM_LOCALIZATION_SESSION.md` 中的 `dispatch()` 约定：
查询状态、带当前参考帧设置明确初值、暂停、停止。
写请求同时核对地图/配置/输入身份及 `expectedGeneration`；
没有自动初值、全局搜索或暂停后自动恢复。
暂停和停止立即撤销可见位姿，旧请求和迟到结果不能重新激活跟踪。
接受初值只表示进入请求状态，工作线程确认并连续通过三次配准后才进入诊断跟踪。

本次组合在每次 IMU/frame 原生操作前调用 `tick()`，
收到纠正点时调用 `observe()`；没有为已有 worker 增加空闲 tick 钩子。
状态查询也会独立清空过期位姿，因此原生调用阻塞时不会继续返回过期位置。
硬阻塞的清理由已有进程监督器处理。

位姿仍标记为 `native-raw`、源坐标为 `IMU`，
不是已标定的米制底盘坐标，不发布导航 TF。
所有状态保持 `localizationReady=false`、`navigationReady=false`。

## 离线复现

当前入口明确限制为普通用户 Linux x86_64、录制输入和已验证的纠正点扩展。
ARM64 和实时输入会被拒绝，不能直接复制到机器人运行。
配置沿用现有录制建图的私有 JSON，要求包含 `pointAdapter`。
还需要既有冻结原生依赖、numpy/scipy/rosbags 和 small-gicp 1.0.1；
本入口不下载安装依赖，也不更改库搜索路径。

在隔离 Linux 环境预先设置与 `child_environment(work)` 完全一致的
`LD_LIBRARY_PATH`，即原生工作目录下的 `prefix/lib` 与
`sysroot/usr/lib/x86_64-linux-gnu`，并设置干净的 `LINGXI_GLIM_UPSTREAM_ROOT`。
使用新的绝对输出路径，不能覆盖输入或复用旧结果：

```sh
python -B robot-runtime/python/controlled_localization.py run \
  --config /private/config.json \
  --saved-map /private/saved-map \
  --map-id "slam-REPLACE_WITH_32_HEX_SESSION" \
  --receipt-sha256 "REPLACE_WITH_ACTUAL_RECEIPT_SHA256" \
  --profile-sha256 "REPLACE_WITH_ACTUAL_PROFILE_SHA256" \
  --output /private/new-output \
  --seconds 90
```

地图 ID 实际格式为 `slam-` 后跟收据中的 32 位十六进制 session。
运行期限必须为 5–120 秒。成功退出仅表示录制输入完整处理、原生和定位资源正常关闭，
即使没有提交初值也不能解释为已成功定位。

`tools/check_localization_runtime.py` 使用相同参数，另需
`--seed-sample` 和 `--seed-sha256`，无需 `--seconds`。
该检查器启动真实入口，通过私有 socket 提交录制样本中的明确初值，
依次检查跟踪、暂停、旧代次拒绝、再次明确设置初值、跟踪和停止，
之后等待整段录制处理完成，并持续确认停止后位姿不恢复。
第一参考帧若错过即失败，不隐藏重试或自动重新播种。

初值样本为保留的 `private-first-points.npz`；
第二次初值仍使用同一录制位姿并明确关联当时参考帧。
这是诊断控制，不是独立测量的真实位置。

## 验证范围

聚焦测试：

```sh
python -B -m unittest discover -s tools/tests -p test_localization_control.py -v
```

19 项覆盖地图实际字节与身份、输入变化、非法点/符号链接/重复字段、
真实 Unix socket、初值与代次冲突、线程所有权、初始化/关闭异常、
父进程结果拒绝及失败收尾。父进程负样本使用替身报告；
原生整段执行由独立检查器验证，结果见 `VALIDATION.md`。

最终源码的一次真实原生检查完整处理 4,144 条回调、196 次扫描，
生成 165 个里程计位姿；原生实例创建/关闭各一次。
两次明确初值请求分别在观察到第 6、11 帧时已经进入跟踪，
随后确认暂停、旧代次拒绝和停止；停止后 2,887 次查询均未恢复位姿。
子进程自然退出，未强制终止，目录和报告权限分别为 0700/0600，
检查后未发现该入口的残留进程。摘要和来源散列见
`slam-localization-runtime-evidence.json`；该 JSON 是从私有检查报告选取的聚合，
不是独立精度审计。

第一次检查缺少既有原生库路径，启动即被拒绝；补齐显式环境后通过一次整链路检查。
补充父进程校验后又以最终源码完成上述检查，未放宽定位质量门槛。
新增父进程负样本曾因测试夹具写错层级失败，修正夹具后 19 项全部通过。

保留的短段输入不能等同于此前约 5 米往返采集的动态验收：
物理运动未独立确认，地图可能复用同段数据，初值来自原记录。
没有位置真值、长程闭环、重复场景全局重定位、底座外参、
ARM64 性能或导航控制结果。
下一步仍可离线接入配对管理 API 与网页初值/状态，再准备 ARM64 适配；
这些功能尚未包含在本次定位入口中。
