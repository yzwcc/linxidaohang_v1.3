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
  it("checks the documented X2 sensor topics and reports the public-workflow map topic", async () => {
    runRobotSshCommand.mockResolvedValue([
      "agibot_software=active", "hal_sensor_running=1", "slam_running=1",
      "lidar_publishers=1", "imu_publishers=1", "map_publishers=0", "map_saver_available=1"
    ].join("\n"));
    const { getMappingHealth } = await import("../src/mappingControl.js");
    const result = await getMappingHealth(config);
    expect(result.lidar_publishers).toBe(1);
    expect(result.imu_publishers).toBe(1);
    expect(result.map_topic).toBe("/projected_map");
    expect(result.ok).toBe(false);
  });

  it("reports missing sensor feeds without issuing robot mutations", async () => {
    runRobotSshCommand.mockResolvedValue([
      "agibot_software=active", "hal_sensor_running=1", "slam_running=1",
      "lidar_publishers=0", "imu_publishers=1", "map_publishers=0", "map_saver_available=1"
    ].join("\n"));
    const { getMappingHealth } = await import("../src/mappingControl.js");
    const result = await getMappingHealth(config);
    expect(result.ok).toBe(false);
    expect(result.lidar_publishers).toBe(0);
    expect(runRobotSshCommand).toHaveBeenCalledTimes(1);
  });

  it("blocks mapping mutation until a verified robot adapter exists", async () => {
    const { mappingAdapterUnavailableMessage } = await import("../src/mappingControl.js");
    expect(mappingAdapterUnavailableMessage()).toContain("接口尚未确认");
    expect(runRobotSshCommand).not.toHaveBeenCalled();
  });
});
