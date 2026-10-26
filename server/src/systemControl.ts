import type { AppConfig } from "./config.js";
import { runRobotSshCommand, shellQuote } from "./sshControl.js";

type TopicHealth = {
  topic: string;
  publishers: number | null;
  subscriptions: number | null;
};

export type RobotSystemHealth = {
  ok: boolean;
  aimdk_v1_1_0_ready: boolean;
  agibot_software: string;
  hal_sensor_running: boolean;
  slam_running: boolean;
  auto_integrated_running: boolean;
  map_manager_running: boolean;
  soc1_visible: boolean;
  soc2_visible: boolean;
  ros_nodes: string[];
  system_state: string;
  network_interfaces: string[];
  topics: Record<string, TopicHealth>;
  slam_lidar_odom_data: boolean;
  diagnosis: string;
  advice: string[];
  checked_at: string;
};

const requiredTopics = [
  "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud",
  "/aima/hal/sensor/lidar_chest_front/imu",
  "/aima/mc/leg_odometry",
  "/slam/lidar_odom",
  "/slam/localization/odometry"
];

export async function getRobotSystemHealth(config: AppConfig): Promise<RobotSystemHealth> {
  const output = await runRobotSystemCommand(healthCommand(config), config, 20000);
  const fields = parseKeyValues(output);
  const topics = Object.fromEntries(requiredTopics.map((topic) => {
    const key = topicKey(topic);
    return [topic, {
      topic,
      publishers: readNullableNumber(fields[`${key}_pub`]),
      subscriptions: readNullableNumber(fields[`${key}_sub`])
    }];
  }));
  const halSensorRunning = fields.hal_sensor_running === "1";
  const aimdkV110Ready = fields.aimdk_v1_1_0_ready === "1";
  const slamRunning = fields.slam_running === "1";
  const autoIntegratedRunning = fields.auto_integrated_running === "1";
  const mapManagerRunning = fields.map_manager_running === "1";
  const rosNodes = splitList(fields.ros_nodes);
  const networkInterfaces = splitList(fields.network_interfaces);
  const systemState = fields.system_state || "unknown";
  const soc1Visible = rosNodes.some((node) => node.includes("soc1"));
  const soc2Visible = rosNodes.some((node) => node.includes("soc2"));
  const lidarPointcloudOk = (topics["/aima/hal/sensor/lidar_chest_front/lidar_pointcloud"].publishers || 0) > 0;
  const lidarImuOk = (topics["/aima/hal/sensor/lidar_chest_front/imu"].publishers || 0) > 0;
  const slamOdomPublisherOk = (topics["/slam/lidar_odom"].publishers || 0) > 0;
  const slamOdomDataOk = fields.slam_lidar_odom_data === "1";
  const slamOdomOk = slamOdomPublisherOk && slamOdomDataOk;
  const ok = aimdkV110Ready
    && fields.agibot_software === "active"
    && halSensorRunning
    && slamRunning
    && autoIntegratedRunning
    && mapManagerRunning
    && lidarPointcloudOk
    && lidarImuOk
    && slamOdomOk;

  return {
    ok,
    aimdk_v1_1_0_ready: aimdkV110Ready,
    agibot_software: fields.agibot_software || "unknown",
    hal_sensor_running: halSensorRunning,
    slam_running: slamRunning,
    auto_integrated_running: autoIntegratedRunning,
    map_manager_running: mapManagerRunning,
    soc1_visible: soc1Visible,
    soc2_visible: soc2Visible,
    ros_nodes: rosNodes,
    system_state: systemState,
    network_interfaces: networkInterfaces,
    topics,
    slam_lidar_odom_data: slamOdomDataOk,
    diagnosis: diagnosis({ ok, aimdkV110Ready, lidarPointcloudOk, lidarImuOk, slamOdomOk, halSensorRunning, slamRunning, soc1Visible }),
    advice: advice({ ok, lidarPointcloudOk, lidarImuOk, slamOdomOk, halSensorRunning, slamRunning, soc1Visible, systemState }),
    checked_at: new Date().toISOString()
  };
}

export async function restartRobotSystem(config: AppConfig): Promise<{ ok: boolean; output: string; health: RobotSystemHealth }> {
  const output = await runRobotSystemCommand(restartCommand(config), config, 120000);
  const health = await getRobotSystemHealth(config);
  return {
    ok: health.ok,
    output,
    health
  };
}

export async function recoverRobotLocalization(config: AppConfig): Promise<{
  ok: boolean;
  before: RobotSystemHealth;
  output: string;
  after: RobotSystemHealth;
}> {
  const before = await getRobotSystemHealth(config);
  if (before.ok) {
    return {
      ok: true,
      before,
      output: "底层定位数据已经正常，无需恢复",
      after: before
    };
  }
  const output = await runRobotSystemCommand(recoverCommand(config), config, 150000);
  const after = await getRobotSystemHealth(config);
  return {
    ok: after.ok,
    before,
    output,
    after
  };
}

function healthCommand(config: AppConfig): string {
  const topicLines = requiredTopics.map((topic) => {
    const key = topicKey(topic);
    return [
      `info="$(timeout 4 ros2 topic info ${shellSingle(topic)} 2>/dev/null || true)"`,
      `echo "${key}_pub=$(printf '%s\\n' "$info" | awk '/Publisher count/ {print $3; exit}')"`,
      `echo "${key}_sub=$(printf '%s\\n' "$info" | awk '/Subscription count/ {print $3; exit}')"`
    ].join("\n");
  }).join("\n");

  return [
    "set +e",
    "[ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash",
    "[ -f /agibot/software/common/local_setup.bash ] && source /agibot/software/common/local_setup.bash",
    `AIMDK_SETUP=${shellQuote(config.robot.aimdkSetup || "")}`,
    "if [ -n \"$AIMDK_SETUP\" ] && [ -r \"$AIMDK_SETUP\" ] && source \"$AIMDK_SETUP\"; then aimdk_setup_ok=1; else aimdk_setup_ok=0; fi",
    "[ -f /agibot/software/entry/cfg/ros_dds_configuration.xml ] && export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml",
    "[ -f /agibot/software/nav/bin/librmw_fastrtps.so ] && export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so",
    "echo agibot_software=$(systemctl is-active agibot_software.service 2>/dev/null || echo unknown)",
    "pgrep -f 'aima-hal_sensor-app-main' >/dev/null && echo hal_sensor_running=1 || echo hal_sensor_running=0",
    "pgrep -f './slam --cfg_file_path' >/dev/null && echo slam_running=1 || echo slam_running=0",
    "pgrep -f './auto_integrated --cfg_file_path' >/dev/null && echo auto_integrated_running=1 || echo auto_integrated_running=0",
    "pgrep -f './map_manager --cfg_file_path' >/dev/null && echo map_manager_running=1 || echo map_manager_running=0",
    "nodes=$(timeout 6 ros2 node list 2>/dev/null | grep '^/' | tr '\\n' '|' || true)",
    "echo ros_nodes=${nodes}",
    "ips=$(ip -br addr 2>/dev/null | awk '{print $1\":\"$3}' | tr '\\n' '|' || true)",
    "echo network_interfaces=${ips}",
    "state=$(timeout 8 ros2 service call /aimdk_5Fmsgs/srv/GetSystemState aimdk_msgs/srv/GetSystemState \"{header: {header: {stamp: {sec: 0, nanosec: 0}}}}\" 2>/dev/null | sed -n \"s/.*cur_state='\\([^']*\\)'.*/\\1/p\" | tail -1)",
    "echo system_state=${state:-unknown}",
    "if timeout 8 ros2 topic echo --once /slam/lidar_odom nav_msgs/msg/Odometry 2>/dev/null | grep -Eq '^(header|pose):'; then echo slam_lidar_odom_data=1; else echo slam_lidar_odom_data=0; fi",
    "if [ \"$aimdk_setup_ok\" = 1 ] && python3 -c 'from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import GetStoredMapByName' >/dev/null 2>&1; then echo aimdk_v1_1_0_ready=1; else echo aimdk_v1_1_0_ready=0; fi",
    topicLines
  ].join("\n");
}

function restartCommand(config: AppConfig): string {
  return [
    "set -e",
    "[ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash",
    "[ -f /agibot/software/common/local_setup.bash ] && source /agibot/software/common/local_setup.bash",
    `AIMDK_SETUP=${shellQuote(config.robot.aimdkSetup || "")}`,
    "if [ -n \"$AIMDK_SETUP\" ] && [ -r \"$AIMDK_SETUP\" ]; then source \"$AIMDK_SETUP\"; else echo 'AIMDK v1.1.0 setup missing'; exit 1; fi",
    "python3 -c 'from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import GetStoredMapByName' || { echo 'AIMDK v1.1.0 interfaces unavailable'; exit 1; }",
    "echo '正在重启机器人底层导航与传感器服务：agibot_software.service'",
    "sudo systemctl restart agibot_software.service",
    "echo '底层服务重启命令已提交，等待关键进程恢复'",
    "for i in $(seq 1 45); do",
    "  sleep 2",
    "  count=$(pgrep -f 'aima-hal_sensor-app-main|./slam --cfg_file_path|./auto_integrated --cfg_file_path|./map_manager --cfg_file_path' | wc -l)",
    "  echo \"等待第 ${i} 次：关键进程数量=${count}\"",
    "  [ \"$count\" -ge 4 ] && break",
    "done",
    "systemctl is-active agibot_software.service || true"
  ].join("\n");
}

function recoverCommand(config: AppConfig): string {
  const topicChecks = [
    "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud",
    "/aima/hal/sensor/lidar_chest_front/imu",
    "/slam/lidar_odom"
  ].map((topic) => [
    `  info="$(timeout 3 ros2 topic info ${shellSingle(topic)} 2>/dev/null || true)"`,
    `  pub=$(printf '%s\\n' "$info" | awk '/Publisher count/ {print $3; exit}')`,
    "  echo \"复查 " + topic + "：发布者数量=${pub:-未知}\""
  ].join("\n")).join("\n");

  return [
    "set +e",
    "echo '开始执行底层定位链路恢复'",
    "[ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash",
    "[ -f /agibot/software/common/local_setup.bash ] && source /agibot/software/common/local_setup.bash",
    `AIMDK_SETUP=${shellQuote(config.robot.aimdkSetup || "")}`,
    "if [ -n \"$AIMDK_SETUP\" ] && [ -r \"$AIMDK_SETUP\" ]; then source \"$AIMDK_SETUP\"; else echo 'AIMDK v1.1.0 setup missing'; exit 1; fi",
    "python3 -c 'from aimdk_msgs.msg import PncTaskRequest; from aimdk_msgs.srv import GetStoredMapByName' >/dev/null 2>&1 || { echo 'AIMDK v1.1.0 interfaces unavailable'; exit 1; }",
    "[ -f /agibot/software/entry/cfg/ros_dds_configuration.xml ] && export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml",
    "[ -f /agibot/software/nav/bin/librmw_fastrtps.so ] && export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so",
    "echo '1/4 重启 ROS2 daemon，清理本机发现缓存'",
    "ros2 daemon stop >/dev/null 2>&1 || true",
    "sleep 1",
    "ros2 daemon start >/dev/null 2>&1 || true",
    "echo '2/4 重启 agibot_software.service，让 HAL、SLAM、地图管理重新进入业务状态'",
    "sudo systemctl restart agibot_software.service",
    "echo '3/4 等待关键进程和定位话题恢复'",
    "stable_count=0",
    "for i in $(seq 1 60); do",
    "  sleep 2",
    "  count=$(pgrep -f 'aima-hal_sensor-app-main|./slam --cfg_file_path|./auto_integrated --cfg_file_path|./map_manager --cfg_file_path' | wc -l)",
    "  odom_pub=$(timeout 3 ros2 topic info /slam/lidar_odom 2>/dev/null | awk '/Publisher count/ {print $3; exit}')",
    "  lidar_pub=$(timeout 3 ros2 topic info /aima/hal/sensor/lidar_chest_front/lidar_pointcloud 2>/dev/null | awk '/Publisher count/ {print $3; exit}')",
    "  imu_pub=$(timeout 3 ros2 topic info /aima/hal/sensor/lidar_chest_front/imu 2>/dev/null | awk '/Publisher count/ {print $3; exit}')",
    "  echo \"等待第 ${i} 次：关键进程=${count}，雷达点云发布者=${lidar_pub:-未知}，雷达 IMU 发布者=${imu_pub:-未知}，定位发布者=${odom_pub:-未知}\"",
    "  if [ \"${lidar_pub:-0}\" -gt 0 ] && [ \"${imu_pub:-0}\" -gt 0 ] && [ \"${odom_pub:-0}\" -gt 0 ]; then",
    "    stable_count=$((stable_count + 1))",
    "  else",
    "    stable_count=0",
    "  fi",
    "  [ \"$stable_count\" -ge 3 ] && break",
    "done",
    "echo '4/4 最终复查关键话题'",
    topicChecks,
    "systemctl is-active agibot_software.service || true",
    "echo '底层定位链路恢复流程结束'"
  ].join("\n");
}

function diagnosis(flags: {
  ok: boolean;
  aimdkV110Ready?: boolean;
  lidarPointcloudOk: boolean;
  lidarImuOk: boolean;
  slamOdomOk: boolean;
  halSensorRunning: boolean;
  slamRunning: boolean;
  soc1Visible: boolean;
}): string {
  if (flags.ok) return "底层导航与定位数据正常";
  if (flags.aimdkV110Ready === false) return "未配置或未加载 AIMDK v1.1.0；请检查机器人端 ROBOT_AIMDK_SETUP 与新版导航/建图消息接口";
  if (!flags.halSensorRunning) return "底层传感器服务未运行，请重启机器人底层服务";
  if (!flags.soc1Visible) return "SOC1 导航/传感器节点未出现在 ROS 图中，可能是底层业务模块未激活或 DDS 网络发现异常";
  if (!flags.lidarPointcloudOk || !flags.lidarImuOk) {
    return "底层传感器服务已运行，但雷达点云或雷达 IMU 没有发布，重定位无法成功";
  }
  if (!flags.slamRunning) return "SLAM 服务未运行，请重启机器人底层服务";
  if (!flags.slamOdomOk) return "雷达/IMU 输入存在，但 SLAM 暂未输出 /slam/lidar_odom";
  return "底层服务未完全就绪";
}

function advice(flags: {
  ok: boolean;
  lidarPointcloudOk: boolean;
  lidarImuOk: boolean;
  slamOdomOk: boolean;
  halSensorRunning: boolean;
  slamRunning: boolean;
  soc1Visible: boolean;
  systemState: string;
}): string[] {
  if (flags.ok) return ["可以执行重定位和导航"];
  const result: string[] = [];
  if (flags.systemState !== "Business" && flags.systemState !== "unknown") {
    result.push(`当前系统状态是 ${flags.systemState}，请先让机器人进入 Business/定位业务状态`);
  }
  if (!flags.halSensorRunning || !flags.slamRunning) {
    result.push("点击“定位链路一键恢复”，让底层服务重新启动");
  }
  if (!flags.soc1Visible) {
    result.push("如果一键恢复后仍看不到 SOC1 节点，请检查机器人内部 10.0.1.x 网络/DDS 配置，必要时整机断电重启");
  }
  if (!flags.lidarPointcloudOk || !flags.lidarImuOk) {
    result.push("雷达点云或雷达 IMU 未发布时，不要继续重定位；先恢复传感器输出");
  }
  if (flags.lidarPointcloudOk && flags.lidarImuOk && !flags.slamOdomOk) {
    result.push("传感器已有数据但 SLAM 未输出定位，请重启底层服务后再检查是否进入定位模式");
  }
  return result.length ? result : ["请先检查机器人底层导航服务和网络连接"];
}

function parseKeyValues(output: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of output.split(/\r?\n/)) {
    const match = line.match(/^([A-Za-z0-9_]+)=(.*)$/);
    if (match) result[match[1]] = match[2].trim();
  }
  return result;
}

function readNullableNumber(value: string | undefined): number | null {
  if (!value) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function splitList(value: string | undefined): string[] {
  return (value || "").split("|").map((item) => item.trim()).filter(Boolean);
}

function topicKey(topic: string): string {
  return topic.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function shellSingle(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

function runRobotSystemCommand(command: string, config: AppConfig, readyTimeout: number): Promise<string> {
  return runRobotSshCommand(command, {
    ...config,
    robot: {
      ...config.robot,
      sshUser: config.robot.systemSshUser,
      sshPassword: config.robot.systemSshPassword,
      sshPort: config.robot.systemSshPort
    }
  }, readyTimeout);
}
