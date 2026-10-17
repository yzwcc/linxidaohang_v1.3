# 导航、自动返航与讲解任务编排逻辑说明

本文档说明当前 `robot_python_bridge` 与 `robot_local_navigation_java` 配合后的完整导航逻辑。核心目标是：

1. 单点导航到达后，执行点位配置的动作、表情、TTS，然后结束本次用户导航任务。
2. 自动回起点只是 10 秒无人交互后的后台流程，不能阻止用户发起新的导航或讲解任务。
3. 单张地图只配置一个讲解任务，任务步骤基于该地图已有点位排序。
4. 讲解任务中的每个点复用点位原有的 `business.on_arrival` 动作、表情、TTS。
5. 任务结束或最后一个点讲解完成后，进入 10 秒无人交互返航等待。

## 模块职责

### Python bridge

路径：

```text
C:\Users\lenovo\Desktop\robot_python_bridge\robot_local_navigation_v3
```

Python bridge 负责真实导航状态机：

- 解析点位名称和点位 ID。
- 发布 ROS2/AimDK 底层导航 START/STOP。
- 判断到达目标点。
- 执行到点动作、表情、TTS。
- 管理自动返航状态。
- 管理讲解任务步骤状态。
- 对 Java 暴露 HTTP 接口。

核心文件：

```text
lingxi_nav_bridge.py      HTTP bridge、状态机、自动返航、任务执行
navigation_client.py      单次底层导航会话
map_point_store.py        每张地图 JSON 的点位和 tour_task 存储
map_label_server_v3.py    地图标点 Web 前端和任务编排前端
```

### Java agent

路径：

```text
C:\Users\lenovo\Desktop\robot_local_navigation_java
```

Java agent 负责语音交互和意图分发：

- 接收灵犀 ASR/音频回调。
- 调豆包把用户输入解析成意图 JSON。
- 对真实用户交互调用 Python bridge 的 `/interaction/mark`。
- 交互结束后调用 `/interaction/end`。
- 按意图调用 `/nav/start`、`/nav/stop`、`/task/start`、`/task/stop`。
- 普通问答仍在 Java 侧走豆包和 TTS。

核心文件：

```text
DoubaoArkClient.java      意图解析提示词
NavigationIntent.java     意图 JSON 校验与分类
NavBridgeClient.java      调 Python bridge HTTP 接口
LingxiNavAgentApp.java    回调处理、mark/end 交互、执行意图
```

## 地图 JSON 数据结构

每张地图对应一个 JSON 文件：

```text
C:\Users\lenovo\Desktop\robot_python_bridge\maps\<map_name>.json
```

顶层结构：

```json
{
  "schema_version": 1,
  "kind": "robot_local_nav.map_points",
  "map_name": "1779436804244",
  "map_id": 1779436804244,
  "updated_at": 1780049368.8692973,
  "points": [],
  "tour_task": {}
}
```

### 点位 points

每个点位包含导航坐标、语音别名和到点业务动作：

```json
{
  "id": "1779436804244:电梯口A点",
  "name": "电梯口A点",
  "map_name": "1779436804244",
  "map_id": 1779436804244,
  "is_start": false,
  "aliases": ["电梯口A点"],
  "pose": {
    "position": {"x": -7.05, "y": 17.164, "z": 0.0},
    "orientation": {"x": 0.0, "y": 0.0, "z": -0.7496, "w": 0.6618}
  },
  "navigation": {
    "target_pose_radius": 0.15,
    "pnc_mode": 0,
    "max_forward_speed": 0.3,
    "task_type": 2
  },
  "business": {
    "on_arrival": [
      {"type": "motion", "motion": "hug", "area": "right_hand", "interrupt": false},
      {"type": "emoji", "emotion": "eye_open", "mode": "once"},
      {"type": "tts", "text": "各位领导好，请移步至2楼进行参观"}
    ]
  }
}
```

`is_start=true` 的点是自动返航目标。每张地图只保留一个起点。

### 讲解任务 tour_task

单张地图只支持一个讲解任务。任务只保存点位顺序，不复制动作、表情、TTS：

```json
{
  "name": "默认讲解任务",
  "enabled": true,
  "steps": [
    {"point_id": "1779436804244:大门", "enabled": true},
    {"point_id": "1779436804244:电梯口A点", "enabled": true}
  ]
}
```

任务执行到某个点时，会读取该点自己的 `business.on_arrival` 并执行。这样标点时配置好的动作、表情、TTS 在单点导航和任务编排里都复用。

## HTTP 接口

Python bridge 默认监听：

```text
http://127.0.0.1:18080
```

主要接口：

```text
GET  /points
GET  /nav/status
POST /nav/start
POST /nav/stop
POST /task/start
POST /task/stop
POST /interaction/mark
POST /interaction/end
POST /auto-return/cancel
POST /tts/speak
```

### POST /nav/start

用户单点导航。

请求：

```json
{
  "event_id": "event_xxx",
  "target_name": "电梯口A点"
}
```

逻辑：

1. 记录一次真实用户交互。
2. 如果正在等待自动返航，取消等待。
3. 如果已经开始自动返航，取消返航底层导航。
4. 如果正在执行讲解任务，新导航会抢占讲解任务。
5. 解析 `target_name` 到当前地图点位。
6. 启动底层导航会话。

### POST /nav/stop

停止当前底层导航。

请求：

```json
{
  "event_id": "event_xxx"
}
```

说明：

- 用于停止普通导航或底层导航。
- 如果要明确结束讲解任务，应使用 `/task/stop`，这样会进入 10 秒返航等待。

### POST /task/start

启动当前地图唯一的讲解任务。

请求：

```json
{
  "event_id": "event_xxx"
}
```

逻辑：

1. 记录一次真实用户交互。
2. 如果正在自动返航，先取消返航。
3. 如果当前已有讲解任务在执行，返回 busy。
4. 如果底层导航仍忙，返回 busy。
5. 从当前地图 JSON 读取 `tour_task`。
6. 按 `steps` 顺序启动第一个点位导航。

一张地图只有一个任务，所以 Java 不需要传 `task_name`。

### POST /task/stop

结束当前讲解任务。

请求：

```json
{
  "event_id": "event_xxx"
}
```

逻辑：

1. 记录一次真实用户交互。
2. 如果任务正在前往某个步骤点，立刻取消底层导航。
3. 任务状态清理。
4. 以当前步骤点或最近步骤点作为返航源点，进入 10 秒自动返航等待。

## 状态机

Python bridge 的自动返航和任务执行共用一套状态字段，状态会出现在 `/nav/status` 的 `auto_return.state` 中。

```text
idle
  当前没有用户导航、任务步骤、到点动作等待或自动返航流程。

navigating_user_target
  用户发起的单点导航正在执行。

arrival_action
  用户单点导航已到达，正在执行或等待该点的动作、表情、TTS。

waiting_auto_return
  已完成到点动作或任务，正在等待 10 秒无人交互窗口。

interaction_active
  等待返航期间发生了用户交互，返航被暂停，等待 Java 调 /interaction/end 后重新计时。

returning_home
  10 秒无人交互满足，正在自动导航回 is_start 点。

navigating_task_step
  讲解任务正在前往某个步骤点。

task_arrival_action
  讲解任务某个步骤点已到达，正在执行或等待该点的动作、表情、TTS。
```

## 单点导航完整流程

用户说：“带我去 B 点”。

```text
Java 解析 intent=navigate,target_name=B
-> Java POST /interaction/mark
-> Java POST /nav/start
-> Python state=navigating_user_target
-> 底层导航到 B
-> NavigationEvent COMPLETED
-> Python state=arrival_action
-> 执行 B.business.on_arrival
-> 等待动作/TTS 估算或配置时长
-> Python state=waiting_auto_return
-> 10 秒内无人交互
-> Python state=returning_home
-> 导航到 is_start 点
-> Python state=idle
```

到达 B 后，“用户导航任务”已经结束。后面的返航是后台自动返航流程，不应该被 Java 理解成用户还有一个业务导航任务。

## 新导航打断返航

场景：机器人到 B 后，正在等待返航或已经开始返航，用户说：“去 C 点”。

```text
Java 解析 intent=navigate,target_name=C
-> Java POST /interaction/mark
-> Python 如果 state=waiting_auto_return，取消返航等待
-> Python 如果 state=returning_home，取消底层返航导航
-> Java POST /nav/start C
-> Python 启动 C 点用户导航
```

结果：

- 不再回复“当前已有导航任务”。
- 不要求用户先手动 `/nav/stop`。
- 机器人会按最新用户意图去 C 点。

## 连续交互延迟返航

场景：机器人到 B 后，用户一直和机器人聊天。

```text
B 到达
-> 执行 B 到点动作/TTS
-> waiting_auto_return
-> 用户说话
-> Java POST /interaction/mark
-> Python state=interaction_active
-> Java 完成本轮 ASR/LLM/TTS
-> Java POST /interaction/end
-> Python 重新进入 waiting_auto_return
-> 从 interaction/end 时刻重新等 10 秒
```

结果：

- 用户持续交互时不会返航。
- 每轮交互结束后重新开始 10 秒计时。
- 机器人自己的 TTS 不算用户交互，不刷新计时。

## 讲解任务完整流程

前端配置任务：

```text
大门 -> 电梯口A点 -> 其他点
```

用户说：“开始任务”。

```text
Java 解析 intent=start_task
-> Java POST /interaction/mark
-> Java POST /task/start
-> Python 读取当前地图 tour_task
-> Python state=navigating_task_step，前往第 1 个点
-> 第 1 个点到达
-> Python state=task_arrival_action
-> 执行第 1 个点 business.on_arrival
-> 等待动作/TTS 完成
-> Python state=navigating_task_step，前往第 2 个点
-> ...
-> 最后一个点到达并执行动作/TTS
-> 任务完成
-> Python state=waiting_auto_return
-> 10 秒内无人交互
-> Python state=returning_home
-> 回到 is_start 点
-> Python state=idle
```

任务内部步骤之间不会触发返航。只有最后一个步骤讲解结束后，才进入自动返航等待。

## 结束任务流程

用户说：“结束任务”。

```text
Java 解析 intent=end_task
-> Java POST /interaction/mark
-> Java POST /task/stop
-> Python 如果正在去步骤点，立刻停止底层导航
-> Python 清理任务状态
-> Python state=waiting_auto_return
-> 10 秒内无人交互
-> Python state=returning_home
-> 回到 is_start 点
```

这符合当前约定：如果机器人正在去某个步骤点，结束任务就是立刻停止当前任务导航，然后进入 10 秒返航等待。

## 任务被新导航抢占

场景：讲解任务正在执行，用户说：“去 C 点”。

```text
Java 解析 intent=navigate,target_name=C
-> Java POST /interaction/mark
-> Java POST /nav/start C
-> Python 取消当前讲解任务
-> 如果任务步骤导航正在执行，取消该底层导航
-> Python 启动 C 点用户导航
```

结果：

- 新导航意图优先。
- 不继续后面的讲解任务步骤。
- 这次抢占不会先返航。

## 失败和超时

如果用户导航或任务步骤失败、超时：

```text
NavigationEvent FAILED/TIMEOUT
-> Python 播报导航问题 TTS
-> 清理当前导航或任务状态
-> 不自动回起点
```

原因：失败和超时通常意味着定位、通道、底盘或地图状态异常。此时继续自动发返航导航可能不安全。

自动返航本身失败或超时时：

```text
returning_home FAILED/TIMEOUT
-> Python 播报“自动回起点失败”
-> Python state=idle
```

## 到点动作等待时间

点位 `business.on_arrival` 支持这些动作：

```text
motion
emoji
tts
```

每个动作可配置等待字段：

```json
{
  "type": "tts",
  "text": "已到达目标点",
  "auto_return_wait_sec": 5
}
```

支持字段：

```text
auto_return_wait_sec
wait_sec
duration_sec
expected_duration_sec
```

如果 TTS 未显式配置等待时间，Python 会按文本长度估算，避免 TTS 还没播完就进入返航计时。

## Java 意图分类

当前支持五类意图：

```text
navigate
stop_navigation
start_task
end_task
chat
```

豆包输出格式：

```json
{
  "intent": "navigate|stop_navigation|start_task|end_task|chat",
  "target_name": "地点名或空字符串",
  "question": "普通问答问题或空字符串",
  "reply": "可选短回复"
}
```

典型映射：

```text
带我去 B 点       -> navigate, target_name=B
停下/取消导航     -> stop_navigation
开始任务/开始讲解 -> start_task
结束任务/停止任务 -> end_task
其它问题          -> chat
```

Java 收到真实用户输入后，会先调用：

```text
POST /interaction/mark
```

本轮处理结束后，如果这一轮确实 mark 过，会调用：

```text
POST /interaction/end
```

这两个接口用于让 Python 判断“用户是否还在交互”，从而决定是否重新开始 10 秒返航等待。

## 前端任务编排

Web 标注服务默认端口：

```text
http://127.0.0.1:8002
```

使用流程：

1. 加载地图。
2. 先在“点位参数”里标注点位，并配置每个点的动作、表情、TTS。
3. 在“讲解任务编排”里选择已有点位，添加到任务。
4. 用上移、下移调整顺序。
5. 保存任务编排。
6. 保存后写入该地图 JSON 的 `tour_task`。

任务编排只选择点位顺序，不单独配置动作、表情、TTS。

## 联调检查点

### 查看 bridge 状态

```powershell
curl http://127.0.0.1:18080/nav/status
```

重点看：

```text
navigation.status
auto_return.state
auto_return.reason
task.active
task.step_number
task.step_count
```

### 单点导航

```powershell
curl -X POST http://127.0.0.1:18080/nav/start `
  -H "Content-Type: application/json" `
  -d "{\"event_id\":\"test-nav-b\",\"target_name\":\"电梯口A点\"}"
```

### 开始讲解任务

```powershell
curl -X POST http://127.0.0.1:18080/task/start `
  -H "Content-Type: application/json" `
  -d "{\"event_id\":\"test-task-start\"}"
```

### 结束讲解任务

```powershell
curl -X POST http://127.0.0.1:18080/task/stop `
  -H "Content-Type: application/json" `
  -d "{\"event_id\":\"test-task-stop\"}"
```

### 模拟用户交互

```powershell
curl -X POST http://127.0.0.1:18080/interaction/mark `
  -H "Content-Type: application/json" `
  -d "{\"event_id\":\"test-chat\",\"source\":\"manual\",\"intent\":\"chat\"}"

curl -X POST http://127.0.0.1:18080/interaction/end `
  -H "Content-Type: application/json" `
  -d "{\"event_id\":\"test-chat\",\"source\":\"manual\",\"intent\":\"chat\"}"
```

## 当前行为总结

| 场景 | 当前行为 |
| --- | --- |
| 到 B 后无人交互 | 执行 B 动作/TTS/表情，等待 10 秒，自动回起点 |
| 到 B 后用户说去 C | 取消挂起或已启动的返航，直接去 C |
| 到 B 后用户持续聊天 | 每轮交互结束后重新等待 10 秒 |
| 开始任务 | 按当前地图 `tour_task.steps` 顺序依次导航讲解 |
| 任务步骤到点 | 执行该点自己的 `business.on_arrival` |
| 任务最后一步结束 | 等 10 秒无人交互后回起点 |
| 任务中用户说去 C | 取消任务，去 C |
| 用户说结束任务 | 立刻停止当前任务导航，进入 10 秒返航等待 |
| 导航失败或超时 | 播报问题，不自动返航 |
