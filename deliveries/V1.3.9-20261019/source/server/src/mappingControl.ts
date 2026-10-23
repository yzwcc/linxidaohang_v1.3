import type { AppConfig } from "./config.js";
import { runRobotSystemSshCommand, shellQuote } from "./sshControl.js";

const lidarTopic = "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud";
const imuTopic = "/aima/hal/sensor/lidar_chest_front/imu";
const odomTopic = "/slam/lidar_odom";

export type MappingHealth = {
  ok: boolean;
  agibot_software: string;
  hal_sensor_running: boolean;
  slam_running: boolean;
  lidar_publishers: number;
  imu_publishers: number;
  odom_publishers: number;
  mapping_related_topics: string[];
  mapping_related_services: string[];
  mapping_backend_available: false;
  checked_at: string;
  diagnosis: string;
};

export async function getMappingHealth(config: AppConfig): Promise<MappingHealth> {
  const lines = [
    "set +e",
    "[ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash",
    "[ -f /agibot/software/common/local_setup.bash ] && source /agibot/software/common/local_setup.bash",
    "[ -f /home/agi/aimdk/install/setup.bash ] && source /home/agi/aimdk/install/setup.bash",
    "[ -f /agibot/software/entry/cfg/ros_dds_configuration.xml ] && export FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml",
    "[ -f /agibot/software/nav/bin/librmw_fastrtps.so ] && export RMW_LIBRARY_PATH=/agibot/software/nav/bin/librmw_fastrtps.so",
    "echo agibot_software=$(systemctl is-active agibot_software.service 2>/dev/null || echo unknown)",
    "pgrep -f 'aima-hal_sensor-app-main' >/dev/null && echo hal_sensor_running=1 || echo hal_sensor_running=0",
    "pgrep -f './slam --cfg_file_path' >/dev/null && echo slam_running=1 || echo slam_running=0",
    ...[["lidar", lidarTopic], ["imu", imuTopic], ["odom", odomTopic]].map(([name, topic]) =>
      `info=$(timeout 4 ros2 topic info ${shellQuote(topic)} 2>/dev/null); echo ${name}_publishers=$(printf '%s\\n' "$info" | awk '/Publisher count/ {print $3; exit}')`
    ),
    "timeout 5 ros2 topic list -t 2>/dev/null | grep -Ei 'map|slam|lidar|imu' | head -80 | sed 's/^/topic=/'",
    "timeout 5 ros2 service list -t 2>/dev/null | grep -Ei 'map|slam|lidar|imu' | head -80 | sed 's/^/service=/'"
  ].join("\n");
  const output = await runRobotSystemSshCommand(`bash -lc ${shellQuote(lines)}`, config, 20000);
  const fields: Record<string, string> = {};
  for (const line of output.split(/\r?\n/)) {
    const index = line.indexOf("=");
    if (index > 0) fields[line.slice(0, index)] = line.slice(index + 1).trim();
  }
  const hal = fields.hal_sensor_running === "1";
  const slam = fields.slam_running === "1";
  const lidar = Number(fields.lidar_publishers) || 0;
  const imu = Number(fields.imu_publishers) || 0;
  const odom = Number(fields.odom_publishers) || 0;
  const software = fields.agibot_software || "unknown";
  const sensorsHealthy = software === "active" && hal && lidar > 0 && imu > 0;
  const topics = output.split(/\r?\n/).filter((line) => line.startsWith("topic=")).map((line) => line.slice(6));
  const services = output.split(/\r?\n/).filter((line) => line.startsWith("service=")).map((line) => line.slice(8));
  const diagnosis = !sensorsHealthy
    ? "请先恢复底层服务、雷达点云和 IMU 数据"
    : !slam || odom === 0
    ? "雷达与 IMU 输入正常，但未确认 SLAM 定位输出。建图模式和地图生成接口仍未确认"
    : "已确认传感器输入及定位里程计；定位输出不等于实时建图。请核对下方 ROS 图，再接入已验证的建图后端";
  return {
    ok: sensorsHealthy && slam && odom > 0,
    agibot_software: software,
    hal_sensor_running: hal,
    slam_running: slam,
    lidar_publishers: lidar,
    imu_publishers: imu,
    odom_publishers: odom,
    mapping_related_topics: topics,
    mapping_related_services: services,
    mapping_backend_available: false,
    checked_at: new Date().toISOString(),
    diagnosis
  };
}

export function mappingAdapterUnavailableMessage(): string {
  return "当前机器人建图接口尚未确认或验证；控制台不会启动建图或写入地图。请先完成目标机器人接口与 MapManager 格式验收";
}
