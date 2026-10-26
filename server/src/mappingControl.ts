import type { AppConfig } from "./config.js";
import { requestJson } from "./http.js";

export type MappingHealth = {
  ok: boolean;
  can_start: boolean;
  agibot_software: string;
  hal_sensor_running: boolean;
  slam_running: boolean;
  lidar_publishers: number;
  imu_publishers: number;
  odom_publishers: number;
  mapping_command_subscribers: number;
  lidar_data_available: boolean;
  imu_data_available: boolean;
  odom_data_available: boolean;
  mapping_related_topics: string[];
  mapping_related_services: string[];
  aimdk_v1_1_0_ready: boolean;
  mapping_prerequisites: Array<{ name: string; available: boolean }>;
  mapping_backend_available: boolean;
  mapping_backend_reason: string;
  checked_at: string;
  diagnosis: string;
};

type MappingStatus = {
  mapping_health?: MappingHealth;
  [key: string]: unknown;
};

export async function getMappingHealth(config: AppConfig): Promise<MappingHealth> {
  const host = config.robot.host.includes(":") && !config.robot.host.startsWith("[")
    ? `[${config.robot.host}]`
    : config.robot.host;
  const status = await requestJson<MappingStatus>(
    `http://${host}:${config.robot.bridgePort}/mapping/status`,
    { timeoutMs: 8000 }
  );
  if (!status.mapping_health) {
    throw new Error("机器人建图桥接未返回传感器诊断；请确认 R18 导航桥接已启动并使用端口 18082");
  }
  return status.mapping_health;
}
