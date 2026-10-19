# robot_local_navigation_v3

`robot_local_navigation_v3` 是灵犀 X2 导航层的本地 ROS2/AimDK HTTP 桥接包。它把灵犀 Java Agent 的导航、停止、TTS 请求转换为机器人本机 ROS2 topic/service 调用，同时提供一个地图标点 Web 工具，用于读取 SLAM 地图、维护点位、启动桥接进程和执行一次性重定位。

该仓库是自包含的 ament Python 包，不再依赖旧路径 `/home/agi/aimdk/build/robot_local_nav_v2` 下的业务代码。

## 目录结构

```text
.
├── package.xml                         # ROS2 ament_python 包声明
├── setup.py                            # console_scripts 入口
├── unified_nav_config.json             # 统一导航配置
├── maps/                               # 按地图拆分的点位文件
│   └── <map_name>.json
├── python_bridge/
│   └── lingxi_nav_bridge.py            # 兼容入口，转调包内 bridge
├── robot_local_navigation_v3/
│   ├── lingxi_nav_bridge.py            # Java Agent HTTP -> ROS2/AimDK bridge
│   ├── map_label_server_v3.py          # 地图标点 Web 服务
│   ├── relocalize_once.py              # 一次性重定位节点
│   ├── navigation_client.py            # 单会话导航控制
│   ├── arrival_policy.py               # 基于里程计的到达判定
│   ├── point_catalog.py                # 点位名称、别名解析
│   ├── map_point_store.py              # maps/<map>.json 点位持久化
│   ├── config_store.py                 # unified_nav_config.json 读写
│   ├── sdk_motion_controller.py        # 到达后动作/表情 SDK 适配
│   ├── tts_speaker.py                  # AimDK PlayTts 适配
│   ├── navigation_events.py            # 导航事件模型
│   └── models.py                       # 配置与点位数据模型
└── scripts/
    ├── robot_local_navigation_v3.sh    # 一键管理 bridge + Web
    └── start_lingxi_nav_bridge.sh      # 只启动 bridge
```

运行时会使用：

```text
logs/                                  # bridge、Web、重定位日志
pids/                                  # 后台进程 pid 文件
relocalization_result.json             # 最近一次重定位结果
```

## 运行环境

- 机器人：灵犀 X2 本机环境。
- ROS2：默认加载 `/opt/ros/humble/setup.bash`。
- AimDK：默认加载 `/home/agi/aimdk/install/setup.bash`。
- Python：Python 3，依赖 ROS2 Python、`rclpy`、`aimdk_msgs`、`geometry_msgs`、`nav_msgs`、`std_msgs`。
- SLAM 地图目录：默认 `/agibot/data/var/MapManagerModule/`，每张地图目录名应为数字 map id，目录内需要包含 `occupancy_map.png` 和 `grid_map_info.txt`。

如果 ROS2 或 AimDK 安装路径不同，先手动 `source` 对应的 `setup.bash`，或通过环境变量覆盖启动脚本默认值。

## 一键启动

下文以机器人上的部署目录 `/home/agi/robot_local_navigation_v3` 为例。如果实际目录不同，把命令里的路径替换为真实仓库路径即可。

推荐在机器人上用脚本管理两个进程：

```bash
cd /home/agi/robot_local_navigation_v3
./scripts/robot_local_navigation_v3.sh up
```

默认地址：

```text
导航桥接 HTTP: http://0.0.0.0:18080
本机自测地址: http://127.0.0.1:18080
地图标点 Web: http://<机器人 IP>:8002
Java Agent 配置: nav.baseUrl=http://<机器人 IP>:18080
```

常用命令：

```bash
./scripts/robot_local_navigation_v3.sh status
./scripts/robot_local_navigation_v3.sh stop
./scripts/robot_local_navigation_v3.sh logs bridge
./scripts/robot_local_navigation_v3.sh logs web
```

如果希望 Web 在前台运行，并在页面点击“启动桥接”后实时看到 bridge 日志：

```bash
./scripts/robot_local_navigation_v3.sh up-live
```

只启动 bridge：

```bash
./scripts/start_lingxi_nav_bridge.sh \
  --active-map-name 1779436804244 \
  --host 0.0.0.0 \
  --port 18080
```

## 启动参数和环境变量

`scripts/robot_local_navigation_v3.sh` 支持以下环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ROBOT_LOCAL_NAVIGATION_V3_HOME` | 当前仓库目录 | 配置、maps、logs、pids 的根目录 |
| `ROBOT_LOCAL_NAVIGATION_V3_CONFIG_JSON` | `$ROBOT_LOCAL_NAVIGATION_V3_HOME/unified_nav_config.json` | 统一配置文件路径 |
| `ROBOT_LOCAL_NAVIGATION_V3_ACTIVE_MAP_NAME` | 空 | 指定 bridge 启动时使用的地图 |
| `ROBOT_LOCAL_NAVIGATION_V3_NAV_MODE` | 配置里的 `app.nav_mode` | `dry-run` 或 `execute` |
| `ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_HOST` | `0.0.0.0` | bridge 监听地址 |
| `ROBOT_LOCAL_NAVIGATION_V3_BRIDGE_PORT` | `18080` | bridge 监听端口 |
| `ROBOT_LOCAL_NAVIGATION_V3_WEB_HOST` | `0.0.0.0` | Web 服务监听地址 |
| `ROBOT_LOCAL_NAVIGATION_V3_WEB_PORT` | `8002` | Web 服务监听端口 |
| `ROBOT_LOCAL_NAV_MAP_ROOT` | `/agibot/data/var/MapManagerModule/` | SLAM 地图根目录 |
| `ROBOT_LOCAL_NAV_ROS_SETUP` | `/opt/ros/humble/setup.bash` | ROS2 setup 文件 |
| `ROBOT_LOCAL_NAV_AIMDK_SETUP` | `/home/agi/aimdk/install/setup.bash` | AimDK setup 文件 |

示例：

```bash
ROBOT_LOCAL_NAVIGATION_V3_ACTIVE_MAP_NAME=1779436804244 \
ROBOT_LOCAL_NAVIGATION_V3_NAV_MODE=execute \
ROBOT_LOCAL_NAVIGATION_V3_WEB_PORT=8002 \
./scripts/robot_local_navigation_v3.sh up
```

## colcon 构建运行

也可以作为标准 ROS2 Python 包构建：

```bash
cd /home/agi
source /opt/ros/humble/setup.bash
source /home/agi/aimdk/install/setup.bash
colcon build --base-paths robot_local_navigation_v3 --packages-select robot_local_navigation_v3
source install/setup.bash
```

运行 bridge：

```bash
ros2 run robot_local_navigation_v3 lingxi_nav_bridge \
  --config-json /home/agi/robot_local_navigation_v3/unified_nav_config.json \
  --active-map-name 1779436804244 \
  --nav-mode execute \
  --host 0.0.0.0 \
  --port 18080
```

运行地图标点 Web：

```bash
ros2 run robot_local_navigation_v3 map_label_server_v3 \
  --config-json /home/agi/robot_local_navigation_v3/unified_nav_config.json \
  --app-home /home/agi/robot_local_navigation_v3 \
  --default-map-root /agibot/data/var/MapManagerModule/ \
  --bridge-host 0.0.0.0 \
  --bridge-port 18080 \
  --host 0.0.0.0 \
  --port 8002
```

## 核心运行逻辑

### 1. bridge 初始化

`robot_local_navigation_v3.lingxi_nav_bridge.LingxiNavBridge` 是 ROS2 节点，也是 Java Agent 的 HTTP 服务入口。

启动后会执行以下流程：

1. 读取 `unified_nav_config.json`。
2. 使用 `MapPointStore` 读取 `maps/<map_name>.json`。
3. 如果旧配置里还有 `points` 字段，会自动迁移到 `maps/<map_name>.json`，并清空配置里的 `points`。
4. 解析 active map：
   - 优先使用 `--active-map-name`。
   - 如果配置里只有一张地图，使用该地图。
   - 否则使用第一个存在点位数据的地图。
5. 用 active map 点位构建 `PointCatalog`。
6. 初始化：
   - `NavigationClient`：发布导航任务并监听里程计。
   - `TtsSpeaker`：调用 AimDK TTS 服务。
   - `SdkMotionController`：调用到达后的动作和表情服务。
7. 启动 `ThreadingHTTPServer`，HTTP 服务线程和 `rclpy.spin()` 并行运行。

如果 active map 下没有点位，bridge 会直接启动失败，避免 Java Agent 发起不可解析的导航请求。

### 2. 导航请求

Java Agent 调用：

```http
POST /nav/start
Content-Type: application/json

{"target_name":"目标点位名称或别名"}
```

bridge 的处理链路：

1. 校验 `target_name`。
2. `PointCatalog.resolve()` 按以下顺序解析点位：
   - 点位 `id`。
   - active map 内的精确 `name`。
   - `name`、`voice.display_name`、`aliases`。
3. 若跨地图同名且未指定 active map，会返回歧义错误。
4. `NavigationClient.start()` 创建单个导航会话。
5. 如果已有会话进行中，新请求会返回 `busy`。

`nav_mode` 行为：

- `dry-run`：只产生 accepted、started、completed 事件，不发布 ROS 导航指令，适合联调 Java Agent 和点位解析。
- `execute`：真正发布 AimDK PNC 导航任务。

### 3. ROS2 导航控制

`NavigationClient` 在 `execute` 模式下使用以下 ROS2 接口：

| 方向 | 接口 | 类型 | 说明 |
| --- | --- | --- | --- |
| publish | `/aima/te/pnc_task_request` | `aimdk_msgs/PncTaskRequest` | 发送 START/STOP 导航任务 |
| subscribe | `/slam/lidar_odom` | `nav_msgs/Odometry` | 监听当前位置，用于到达判定 |

发布导航 START 时会填充：

- `task_request = 1`
- `task_type = point.navigation.task_type`
- `pnc_mode = point.navigation.pnc_mode`
- `max_forward_speed = point.navigation.max_forward_speed`
- `task_id = int(time.time() * 1000)`
- `map_id = point.map_id`
- `target_pose_radius = point.navigation.target_pose_radius`
- `target_pose = point.pose`

START/STOP 消息会立即发布一次，并按 1 秒周期重发，直到会话结束。

### 4. 到达判定和停止策略

`ArrivalPolicy` 根据 `/slam/lidar_odom` 判断是否到达目标：

- 位置阈值为 `min(navigation.arrival_policy.position_tolerance_m, point.navigation.target_pose_radius)`。
- 进入半径后需要稳定 `stable_duration_sec` 秒。
- 如果配置了 `yaw_tolerance_rad`，还会检查朝向误差。
- 如果 `require_low_speed=true`，还会检查线速度是否低于 `low_speed_threshold_mps`。

`stop_policy.mode` 支持：

- `never`：不发送 STOP。
- `on_arrived_region`：进入目标半径后即发送 STOP 并完成。
- `on_completed`：满足完整到达条件后发送 STOP，默认模式。

完成后会等待 `stop_policy.hold_sec`，再发出 completed 事件并清理会话。

### 5. 到达后动作

点位的 `business.on_arrival` 会在导航完成后执行，支持三类动作：

```json
{
  "business": {
    "on_arrival": [
      {"type": "motion", "motion": "wave", "area": "right_hand", "interrupt": false},
      {"type": "emoji", "emotion": "happy", "mode": "once"},
      {"type": "tts", "text": "已到达目标点"}
    ]
  }
}
```

对应 AimDK/SDK 服务：

| 动作 | 服务 | 说明 |
| --- | --- | --- |
| `motion` | `/aimdk_5Fmsgs/srv/SetMcPresetMotion` | 预置肢体动作 |
| `emoji` | `/face_ui_proxy/play_emoji` | 面部表情 |
| `tts` | `/aimdk_5Fmsgs/srv/PlayTts` | 到达播报 |

`sdk_motion_controller.py` 内维护了动作、控制区域和表情的别名映射。未知动作或服务不可用时只记录日志，不会阻塞导航会话结束。

### 6. TTS 请求

Java Agent 可直接调用：

```http
POST /tts/speak
Content-Type: application/json

{"text":"播报内容","event_id":"trace-id","force":true}
```

逻辑说明：

- 空文本会返回 `empty_text`。
- 普通 TTS 默认 3 秒节流，`force=true` 可跳过节流。
- 即使请求里传了 `interrupt=false`，bridge 仍会以 `interrupt=true` 调用 AimDK，确保机器人播放通道被接管。
- TTS 服务不可用时返回 `tts_unavailable`。

## bridge HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/points` | 返回 active map 下可被语音使用的点位名称和别名 |
| `GET` | `/nav/status` | 返回 bridge、导航会话、SDK 服务状态 |
| `POST` | `/nav/start` | 按 `target_name` 启动导航 |
| `POST` | `/nav/stop` | 取消当前导航 |
| `POST` | `/nav/mode` | 切换并持久化 `dry-run`/`execute` |
| `POST` | `/nav/map` | 切换 active map 并重载点位 |
| `POST` | `/nav/reload` | 重载配置和当前地图点位 |
| `POST` | `/tts/speak` | 播报 TTS |

自测示例：

```bash
curl http://127.0.0.1:18080/points

curl http://127.0.0.1:18080/nav/status

curl -X POST http://127.0.0.1:18080/nav/start \
  -H 'Content-Type: application/json' \
  -d '{"target_name":"起点"}'

curl -X POST http://127.0.0.1:18080/nav/stop \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"manual-stop"}'

curl -X POST http://127.0.0.1:18080/nav/mode \
  -H 'Content-Type: application/json' \
  -d '{"nav_mode":"execute"}'

curl -X POST http://127.0.0.1:18080/nav/map \
  -H 'Content-Type: application/json' \
  -d '{"active_map_name":"1779436804244"}'

curl -X POST http://127.0.0.1:18080/tts/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"导航桥接已启动","force":true}'
```

## Java Agent 配置

Java 侧只需要访问 bridge HTTP 地址：

```properties
nav.baseUrl=http://<机器人 IP>:18080
nav.timeoutMs=5000
tts.mode=robot
```

如果 Java Agent 和 bridge 在同一台机器人上，可以使用 `http://127.0.0.1:18080`。如果 Java Agent 在 Windows 或其他远端机器上，必须使用远端可访问的机器人网卡 IP，不能写 `127.0.0.1`。

## 地图标点 Web

启动：

```bash
./scripts/robot_local_navigation_v3.sh start-web
```

浏览器访问：

```text
http://<机器人 IP>:8002
```

Web 服务逻辑：

1. 从 SLAM 地图根目录发现地图。
2. 只识别目录名为数字的地图目录。
3. 每张地图必须包含：
   - `occupancy_map.png`
   - `grid_map_info.txt`
4. 加载地图后，在 canvas 上点击设置点位位置，再点击或右键设置朝向。
5. 保存点位时写入 `maps/<map_name>.json`。
6. 同时更新 `unified_nav_config.json` 的 `maps` 列表。
7. 如果 bridge 正在运行，会自动切换 active map 并 `/nav/reload`。

`grid_map_info.txt` 读取规则：

```text
第 1 行: resolution_pixels_per_meter
第 2 行: origin_u origin_v
第 3 行: width height
```

像素和世界坐标转换：

```text
u = x * resolution_pixels_per_meter + origin_u
v = origin_v - y * resolution_pixels_per_meter

x = (u - origin_u) / resolution_pixels_per_meter
y = -(v - origin_v) / resolution_pixels_per_meter
```

Web API：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 标点页面 |
| `GET` | `/api/config` | 当前配置路径和点位目录 |
| `GET` | `/api/settings` | `app` 配置 |
| `POST` | `/api/settings` | 保存 `nav_mode` 等配置 |
| `GET` | `/api/discover_maps?map_root=...` | 发现 SLAM 地图 |
| `GET` | `/api/map?map_root=...&map_name=...` | 读取地图信息和点位 |
| `GET` | `/api/map_image?map_root=...&map_name=...` | 读取地图图片 |
| `POST` | `/api/points` | 新增或更新点位 |
| `DELETE` | `/api/points/<point_id>` | 删除点位 |
| `GET` | `/api/bridge/status` | bridge 进程和远端状态 |
| `POST` | `/api/bridge/start` | 从 Web 启动 bridge |
| `POST` | `/api/bridge/stop` | 从 Web 停止 bridge |
| `GET` | `/api/relocalization/status` | 重定位状态 |
| `POST` | `/api/relocalization/start` | 启动一次性重定位 |

## 重定位流程

Web 侧点击开始重定位时，会启动 `robot_local_navigation_v3.relocalize_once` 子进程。

节点逻辑：

1. 等待 `/integrated_command` 和 `/relocalization_pose` 有订阅者。
2. 发布 `std_msgs/String` 到 `/integrated_command`：

   ```text
   start_relocalization:<map_id>
   ```

3. 延迟 `pose_delay_sec` 秒后发布 `geometry_msgs/Pose` 到 `/relocalization_pose`。
4. 监听 `/slam/lidar_odom`。
5. 收到里程计后判定成功；超过 `timeout_sec` 未收到则判定超时。
6. 结果写入 `relocalization_result.json`。

命令行示例：

```bash
ros2 run robot_local_navigation_v3 relocalize_once \
  --map-id 1779436804244 \
  --x -1.958 \
  --y -0.443 \
  --z 0.0 \
  --qx 0.0 \
  --qy 0.0 \
  --qz -0.3162256238996607 \
  --qw 0.9486840120868857 \
  --timeout-sec 30 \
  --result-json /home/agi/robot_local_navigation_v3/relocalization_result.json
```

## 配置文件

### unified_nav_config.json

主配置文件结构：

```json
{
  "schema_version": 2,
  "kind": "robot_local_nav.unified_config",
  "app": {
    "provider": "doubao_multimodal",
    "doubao_api_key": "",
    "doubao_model": "doubao-seed-2-0-mini-260428",
    "nav_mode": "dry-run"
  },
  "navigation": {
    "default_task_type": 2,
    "default_target_pose_radius": 0.15,
    "default_max_forward_speed": 0.3,
    "default_pnc_mode": 0,
    "arrival_policy": {
      "position_tolerance_m": 0.15,
      "yaw_tolerance_rad": null,
      "stable_duration_sec": 0.8,
      "require_low_speed": false,
      "low_speed_threshold_mps": 0.03
    },
    "stop_policy": {
      "mode": "on_completed",
      "hold_sec": 1.5
    },
    "timeout_sec": 120.0
  },
  "maps": [],
  "points": [],
  "exports": {}
}
```

字段说明：

- `app.nav_mode`：默认导航模式，`dry-run` 或 `execute`。
- `navigation.default_*`：新点位未显式配置时使用的默认导航参数。
- `navigation.arrival_policy`：到达判定策略。
- `navigation.stop_policy`：到达或取消时的 STOP 发送策略。
- `navigation.timeout_sec`：单次导航超时时间。
- `maps`：已登记地图列表。
- `points`：兼容旧结构；启动后会迁移到 `maps/<map_name>.json`。

注意：不要把真实密钥提交到公开仓库。当前配置如果含有真实 `doubao_api_key`，上线前应迁移到本机私有配置或环境变量管理。

### maps/<map_name>.json

每张地图一个点位文件：

```json
{
  "schema_version": 1,
  "kind": "robot_local_nav.map_points",
  "map_name": "1779436804244",
  "map_id": 1779436804244,
  "points": [
    {
      "id": "1779436804244:起点",
      "name": "起点",
      "map_name": "1779436804244",
      "map_id": 1779436804244,
      "pose": {
        "position": {"x": -1.958, "y": -0.443, "z": 0.0},
        "orientation": {"x": 0.0, "y": 0.0, "z": -0.3162, "w": 0.9487}
      },
      "is_start": true,
      "aliases": ["起点"],
      "navigation": {
        "target_pose_radius": 0.15,
        "pnc_mode": 0,
        "max_forward_speed": 0.3,
        "task_type": 2
      },
      "ui": {
        "pixel_u": 395.8,
        "pixel_v": 1017.8,
        "heading_pixel_u": 401.9,
        "heading_pixel_v": 1022.4
      },
      "voice": {
        "enabled": true,
        "display_name": "起点"
      },
      "business": {
        "on_arrival": [
          {"type": "tts", "text": "已到达起点"}
        ]
      },
      "tour": {
        "enabled": false,
        "order": null,
        "summary": ""
      }
    }
  ]
}
```

保存点位时，`MapPointStore` 会按 `id` 或 `name` 更新已有点位。若多个点位被标记为 `is_start=true`，只保留最后保存的那个起点。

## 日志和排障

日志位置：

```text
logs/lingxi_nav_bridge.log
logs/map_label_server_v3.log
logs/relocalize_once.log
```

进程状态：

```bash
./scripts/robot_local_navigation_v3.sh status
```

常见问题：

| 现象 | 排查方向 |
| --- | --- |
| bridge 启动失败，提示没有点位 | 确认 `--active-map-name` 对应的 `maps/<map_name>.json` 存在且 `points` 非空 |
| Java Agent 连不上 | 确认 bridge 监听 `0.0.0.0:18080`，远端配置使用机器人网卡 IP，不是 `127.0.0.1` |
| `/nav/start` 返回 `point_not_found` | 检查 active map、点位 `name`、`voice.display_name`、`aliases` |
| `/nav/start` 返回 `busy` | 当前已有导航会话，先调用 `/nav/stop` |
| execute 模式不动 | 确认 ROS2/AimDK 环境已 source，`/aima/te/pnc_task_request` 有订阅者 |
| 一直不到达 | 检查 `/slam/lidar_odom` 是否更新，目标坐标是否属于当前地图，半径和到达策略是否过严 |
| TTS 不播报 | 检查 `/aimdk_5Fmsgs/srv/PlayTts` 服务是否存在，或使用 `force=true` 跳过节流 |
| 动作/表情不执行 | 检查 `/aimdk_5Fmsgs/srv/SetMcPresetMotion` 和 `/face_ui_proxy/play_emoji` 服务 |
| Web 无法发现地图 | 确认地图目录名为数字，且包含 `occupancy_map.png`、`grid_map_info.txt` |
| 重定位超时 | 检查 `/integrated_command`、`/relocalization_pose` 订阅者和 `/slam/lidar_odom` 输出 |

## Git 工作规范

### 分支

- `main` 只保留可部署状态，不直接在 `main` 上开发。
- 开发前先同步主分支：

  ```bash
  git checkout main
  git pull --ff-only
  ```

- 新功能、修复、文档分别使用清晰分支名：

  ```text
  feature/nav-xxx
  fix/nav-xxx
  docs/readme
  chore/xxx
  ```

- 如果在 Codex 协作环境中创建分支，可使用默认前缀 `new/`，例如 `new/docs-readme`。

### 提交

- 每个 commit 只做一件事，避免把代码、点位数据、日志和格式化混在一起。
- 推荐使用 Conventional Commits：

  ```text
  feat: add arrival motion action
  fix: handle empty active map points
  docs: update navigation README
  chore: ignore runtime logs
  ```

- 提交前检查：

  ```bash
  git status --short
  git diff
  ```

- 不提交运行产物：`logs/`、`pids/`、`build/`、`install/`、`log/`、`__pycache__/`、`.pytest_cache/`。
- 不提交真实密钥、机器人内网敏感地址和临时调试配置。必须提交示例配置时，使用脱敏值。

### 验证

代码改动提交前至少做对应级别验证：

```bash
python3 -m compileall robot_local_navigation_v3 python_bridge
```

在机器人上涉及 ROS2/AimDK 行为时，还应验证：

```bash
./scripts/robot_local_navigation_v3.sh up
curl http://127.0.0.1:18080/nav/status
curl http://127.0.0.1:18080/points
```

涉及真实运动的改动，先用 `dry-run` 联调，再切换 `execute`，并确保现场有人可以接管急停。

### Pull Request

PR 描述应包含：

- 改动目的。
- 主要实现点。
- 影响范围。
- 验证命令和结果。
- 是否涉及真实机器人运动、地图点位、密钥或部署配置。

合并前要求：

- 没有无关文件变化。
- 没有运行日志、pid、缓存文件。
- 配置和点位变更已说明适用地图。
- 至少一名相关维护者 review。

### 同步和回滚

- 长分支每天 rebase 或 merge 最新 `main`，减少冲突堆积。
- 共享分支禁止随意强推；确需改历史时使用 `git push --force-with-lease`，并提前通知协作者。
- 部署版本建议打 tag：

  ```bash
  git tag v0.1.0
  git push origin v0.1.0
  ```

- 出现线上问题时优先回滚到上一个已验证 tag 或 revert 对应 commit，不在机器人现场临时手改代码后长期运行。
