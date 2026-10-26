import { describe, expect, it, vi } from "vitest";
import { getMappingHealth } from "../src/mappingControl.js";
import type { AppConfig } from "../src/config.js";

const config = {
  robot: { host: "127.0.0.1", bridgePort: 18082 }
} as AppConfig;

describe("mapping health proxy", () => {
  it("reads the live ROS probe from the robot bridge on the R18 port", async () => {
    const health = {
      ok: false,
      can_start: true,
      agibot_software: "unknown",
      hal_sensor_running: true,
      slam_running: false,
      lidar_publishers: 1,
      imu_publishers: 1,
      odom_publishers: 0,
      mapping_command_subscribers: 1,
      lidar_data_available: true,
      imu_data_available: true,
      odom_data_available: false,
      mapping_related_topics: ["/aima/hal/sensor/lidar_chest_front/lidar_pointcloud"],
      mapping_related_services: ["/aimdk_5Fmsgs/srv/GetStoredMapByName"],
      aimdk_v1_1_0_ready: true,
      mapping_prerequisites: [{ name: "AIMDK 建图命令订阅端", available: true }],
      mapping_backend_available: true,
      mapping_backend_reason: "",
      checked_at: "2026-10-26T00:00:00.000Z",
      diagnosis: "建图条件已就绪"
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ mapping_health: health })));

    await expect(getMappingHealth(config)).resolves.toEqual(health);
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:18082/mapping/status", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("reports a clear error when the R18 bridge response is incompatible", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ active: false })));
    await expect(getMappingHealth(config)).rejects.toThrow("机器人建图桥接未返回传感器诊断");
  });
});
