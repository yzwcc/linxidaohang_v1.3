import { afterEach, describe, expect, it, vi } from "vitest";
import type { AppConfig } from "../src/config.js";

const runRobotSshCommand = vi.fn();
vi.mock("../src/sshControl.js", () => ({
  runRobotSshCommand: (...args: unknown[]) => runRobotSshCommand(...args),
  runRobotSystemSshCommand: (...args: unknown[]) => runRobotSshCommand(...args),
  shellQuote: (value: string) => `'${value.replace(/'/g, "'\\''")}'`
}));

const config = {
  deploymentMode: "robot",
  host: "0.0.0.0",
  port: 3000,
  publicBaseUrl: "http://127.0.0.1:3000",
  robot: {
    id: "default",
    connectionMode: "direct",
    host: "127.0.0.1",
    bridgePort: 18080,
    webPort: 8002,
    mapRoot: "/agibot/data/var/MapManagerModule/",
    sshUser: "agi",
    sshPassword: "",
    sshPort: 22,
    systemSshUser: "run",
    systemSshPassword: "",
    systemSshPort: 22,
    navHome: "/home/agi/robot_local_navigation_v3",
    javaAgentHome: "/tmp/java-agent",
    javaAgentExecutionMode: "robot",
    robotJavaAgentHome: "/home/run/lingxi_java_agent",
    robotJavaAgentBridgePort: 18083,
    voiceRestartCommand: "/bin/true",
    javaAgentAutoStart: false
  },
  auth: { appId: "", appSecret: "", tokenSecret: "test", devBypass: true, tokenTtlSec: 60 }
} as AppConfig;

afterEach(() => {
  runRobotSshCommand.mockReset();
  delete process.env.ROBOT_MAPPING_START_COMMAND;
  delete process.env.ROBOT_MAPPING_STOP_COMMAND;
  delete process.env.ROBOT_MAPPING_MAP_TOPIC;
});

describe("robot mapping control", () => {
  it("checks confirmed X2 sensor topics and exposes discovered map-related ROS interfaces", async () => {
    runRobotSshCommand.mockResolvedValue([
      "agibot_software=active", "hal_sensor_running=1", "slam_running=1",
      "lidar_publishers=1", "imu_publishers=1", "odom_publishers=1",
      "topic=/slam/lidar_odom [nav_msgs/msg/Odometry]", "service=/map/save [example/srv/Save]",
      "prerequisite_rectifier=1", "prerequisite_fast_lio=1", "prerequisite_open3d=0",
      "prerequisite_octomap=1", "prerequisite_map_saver=1"
    ].join("\n"));
    const { getMappingHealth } = await import("../src/mappingControl.js");
    const result = await getMappingHealth(config);
    expect(result.lidar_publishers).toBe(1);
    expect(result.imu_publishers).toBe(1);
    expect(result.odom_publishers).toBe(1);
    expect(result.mapping_related_topics).toContain("/slam/lidar_odom [nav_msgs/msg/Odometry]");
    expect(result.mapping_related_services).toContain("/map/save [example/srv/Save]");
    expect(result.mapping_backend_available).toBe(false);
    expect(result.mapping_prerequisites).toEqual([
      { name: "雷达/IMU 校正包", available: true },
      { name: "Spark FAST-LIO", available: true },
      { name: "Open3D PCD 合并依赖", available: false },
      { name: "OctoMap 投影节点", available: true },
      { name: "Nav2 map_saver_cli", available: true }
    ]);
    expect(result.ok).toBe(true);
  });

  it("reports missing sensor feeds without issuing robot mutations", async () => {
    runRobotSshCommand.mockResolvedValue([
      "agibot_software=active", "hal_sensor_running=1", "slam_running=1",
      "lidar_publishers=0", "imu_publishers=1", "odom_publishers=1"
    ].join("\n"));
    const { getMappingHealth } = await import("../src/mappingControl.js");
    const result = await getMappingHealth(config);
    expect(result.ok).toBe(false);
    expect(result.lidar_publishers).toBe(0);
    expect(runRobotSshCommand).toHaveBeenCalledTimes(1);
  });

  it("does not report a healthy mapping chain without a live localization output", async () => {
    runRobotSshCommand.mockResolvedValue([
      "agibot_software=active", "hal_sensor_running=1", "slam_running=1",
      "lidar_publishers=1", "imu_publishers=1", "odom_publishers=0"
    ].join("\n"));
    const { getMappingHealth } = await import("../src/mappingControl.js");
    const result = await getMappingHealth(config);
    expect(result.ok).toBe(false);
    expect(result.diagnosis).toContain("未确认 SLAM 定位输出");
  });

  it("blocks mapping mutation until a verified robot adapter exists", async () => {
    const { mappingAdapterUnavailableMessage } = await import("../src/mappingControl.js");
    expect(mappingAdapterUnavailableMessage()).toContain("接口尚未确认");
    expect(runRobotSshCommand).not.toHaveBeenCalled();
  });
});
