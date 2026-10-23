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
  lidar_data_available: boolean;
  imu_data_available: boolean;
  odom_data_available: boolean;
  mapping_related_topics: string[];
  mapping_related_services: string[];
  mapping_prerequisites: Array<{ name: string; available: boolean }>;
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
      `${name}_info=$(timeout 3 ros2 topic info ${shellQuote(topic)} 2>/dev/null); ${name}_publishers=$(printf '%s\\n' "$${name}_info" | awk '/Publisher count/ {print $3; exit}'); echo ${name}_publishers=$${name}_publishers`
    ),
    "if [ \"$lidar_publishers\" -gt 0 ] 2>/dev/null; then timeout 3 ros2 topic echo --once --qos-reliability best_effort " + shellQuote(lidarTopic) + " sensor_msgs/msg/PointCloud2 >/dev/null 2>&1 && echo lidar_data=1 || echo lidar_data=0; else echo lidar_data=0; fi",
    "if [ \"$imu_publishers\" -gt 0 ] 2>/dev/null; then timeout 3 ros2 topic echo --once --qos-reliability best_effort " + shellQuote(imuTopic) + " sensor_msgs/msg/Imu >/dev/null 2>&1 && echo imu_data=1 || echo imu_data=0; else echo imu_data=0; fi",
    "if [ \"$odom_publishers\" -gt 0 ] 2>/dev/null; then timeout 3 ros2 topic echo --once --qos-reliability best_effort " + shellQuote(odomTopic) + " nav_msgs/msg/Odometry >/dev/null 2>&1 && echo odom_data=1 || echo odom_data=0; else echo odom_data=0; fi",
    "timeout 3 ros2 topic list -t 2>/dev/null | grep -Ei 'map|slam|lidar|imu' | head -80 | sed 's/^/topic=/'",
    "timeout 3 ros2 service list -t 2>/dev/null | grep -Ei 'map|slam|lidar|imu' | head -80 | sed 's/^/service=/'",
    "timeout 2 ros2 pkg prefix py_examples >/dev/null 2>&1 && echo prerequisite_rectifier=1 || echo prerequisite_rectifier=0",
    "timeout 2 ros2 pkg prefix spark_fast_lio >/dev/null 2>&1 && echo prerequisite_fast_lio=1 || echo prerequisite_fast_lio=0",
    "timeout 3 python3 -c 'import open3d' >/dev/null 2>&1 && echo prerequisite_open3d=1 || echo prerequisite_open3d=0",
    "timeout 2 ros2 pkg prefix octomap_server >/dev/null 2>&1 && echo prerequisite_octomap=1 || echo prerequisite_octomap=0",
    "timeout 3 sh -c 'ros2 pkg prefix nav2_map_server >/dev/null 2>&1 && ros2 pkg executables nav2_map_server 2>/dev/null | grep -q map_saver_cli' && echo prerequisite_map_saver=1 || echo prerequisite_map_saver=0"
  ].join("\n");
  const output = await runRobotSystemSshCommand(`bash -lc ${shellQuote(lines)}`, config, 42000);
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
  const lidarData = fields.lidar_data === "1";
  const imuData = fields.imu_data === "1";
  const odomData = fields.odom_data === "1";
  const sensorsHealthy = software === "active" && hal && lidar > 0 && imu > 0 && lidarData && imuData;
  const topics = output.split(/\r?\n/).filter((line) => line.startsWith("topic=")).map((line) => line.slice(6));
  const services = output.split(/\r?\n/).filter((line) => line.startsWith("service=")).map((line) => line.slice(8));
  const prerequisites = [
    ["rectifier", "雷达/IMU 校正包"],
    ["fast_lio", "Spark FAST-LIO"],
    ["open3d", "Open3D PCD 合并依赖"],
    ["octomap", "OctoMap 投影节点"],
    ["map_saver", "Nav2 map_saver_cli"]
  ] as const;
  const diagnosis = !sensorsHealthy
    ? "请先恢复底层服务、雷达点云和 IMU 数据"
    : !slam || odom === 0 || !odomData
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
    lidar_data_available: lidarData,
    imu_data_available: imuData,
    odom_data_available: odomData,
    mapping_related_topics: topics,
    mapping_related_services: services,
    mapping_prerequisites: prerequisites.map(([key, name]) => ({
      name,
      available: fields[`prerequisite_${key}`] === "1"
    })),
    mapping_backend_available: false,
    checked_at: new Date().toISOString(),
    diagnosis
  };
}

export function mappingAdapterUnavailableMessage(): string {
  return "当前机器人建图接口尚未确认或验证；控制台不会启动建图或写入地图。请先完成目标机器人接口与 MapManager 格式验收";
}
