import type { AppConfig } from "./config.js";
import { runRobotSystemSshCommand, shellQuote } from "./sshControl.js";

const lidarTopic = "/aima/hal/sensor/lidar_chest_front/lidar_pointcloud";
const imuTopic = "/aima/hal/sensor/lidar_chest_front/imu";
const mapTopic = process.env.ROBOT_MAPPING_MAP_TOPIC || "/projected_map";

export type MappingHealth = {
  ok: boolean;
  agibot_software: string;
  hal_sensor_running: boolean;
  slam_running: boolean;
  lidar_publishers: number;
  imu_publishers: number;
  map_publishers: number;
  map_topic: string;
  map_saver_available: boolean;
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
    ...[["lidar", lidarTopic], ["imu", imuTopic], ["map", mapTopic]].map(([name, topic]) =>
      `info=$(timeout 4 ros2 topic info ${shellQuote(topic)} 2>/dev/null); echo ${name}_publishers=$(printf '%s\\n' "$info" | awk '/Publisher count/ {print $3; exit}')`
    ),
    "command -v map_saver_cli >/dev/null && echo map_saver_available=1 || echo map_saver_available=0"
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
  const map = Number(fields.map_publishers) || 0;
  const software = fields.agibot_software || "unknown";
  const healthy = software === "active" && hal && slam && lidar > 0 && imu > 0;
  const diagnosis = !healthy
    ? "请先恢复底层服务、雷达点云和 IMU 数据"
    : map === 0
    ? "传感器正常；未发现实时占据栅格地图。当前固件的建图模式和地图投影接口尚待确认"
    : "传感器与占据栅格话题正常；建图模式仍需现场确认";
  return {
    ok: healthy && map > 0 && fields.map_saver_available === "1",
    agibot_software: software,
    hal_sensor_running: hal,
    slam_running: slam,
    lidar_publishers: lidar,
    imu_publishers: imu,
    map_publishers: map,
    map_topic: mapTopic,
    map_saver_available: fields.map_saver_available === "1",
    checked_at: new Date().toISOString(),
    diagnosis
  };
}

export function mappingAdapterUnavailableMessage(): string {
  return "当前机器人固件的建图启动、停止与 MapManager 落库接口尚未确认；为保护现有导航地图，控制台暂不执行建图或保存。请先完成文档中的实机接口验收";
}
