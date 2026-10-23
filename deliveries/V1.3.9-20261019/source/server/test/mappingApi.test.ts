import { afterEach, describe, expect, it, vi } from "vitest";
import { buildApp } from "../src/app.js";
import type { AppConfig } from "../src/config.js";
import type { RobotClient } from "../src/robotClient.js";

const runRobotSystemSshCommand = vi.fn();
vi.mock("../src/sshControl.js", async (importOriginal) => ({
  ...await importOriginal<typeof import("../src/sshControl.js")>(),
  runRobotSystemSshCommand: (...args: unknown[]) => runRobotSystemSshCommand(...args)
}));

const config: AppConfig = {
  deploymentMode: "cloud",
  host: "127.0.0.1",
  port: 0,
  publicBaseUrl: "http://127.0.0.1",
  robot: {
    id: "default",
    connectionMode: "direct",
    host: "192.168.0.203",
    bridgePort: 18080,
    webPort: 8002,
    mapRoot: "/maps",
    sshUser: "agi",
    sshPassword: "",
    sshPort: 22,
    systemSshUser: "run",
    systemSshPassword: "",
    systemSshPort: 22,
    navHome: "/home/agi/robot_local_navigation_v3",
    javaAgentHome: "/tmp/java-agent",
    javaAgentExecutionMode: "local",
    robotJavaAgentHome: "/home/run/lingxi_java_agent",
    robotJavaAgentBridgePort: 18083,
    javaAgentAutoStart: false
  },
  auth: { appId: "", appSecret: "", tokenSecret: "test", devBypass: true, tokenTtlSec: 3600 }
};

function robot(): RobotClient {
  return {
    invalidateStatus: () => undefined,
    status: async () => ({ bridge_online: true }),
    navStart: async () => ({ ok: true }),
    navStop: async () => ({ ok: true }),
    taskStart: async () => ({ ok: true }),
    taskCommands: async () => ({ ok: true, tasks: [] }),
    taskPause: async () => ({ ok: true }),
    taskResume: async () => ({ ok: true }),
    taskStop: async () => ({ ok: true }),
    speak: async () => ({ ok: true }),
    setActiveMap: async () => ({ ok: true }),
    reloadNavigation: async () => ({ ok: true }),
    currentLocalization: async () => ({ ok: false }),
    maps: async () => ({ maps: [] }),
    linkcraftResources: async () => ({ ok: true, resources: [] }),
    map: async () => ({}),
    mapImage: async () => ({ data: Buffer.alloc(0), contentType: "image/png" }),
    savePoint: async () => ({}),
    deletePoint: async () => ({ ok: true }),
    saveTask: async () => ({}),
    relocalizationStatus: async () => ({ running: false }),
    startRelocalization: async () => ({ ok: true })
  };
}

afterEach(() => {
  runRobotSystemSshCommand.mockReset();
});

describe("mapping API", () => {
  it("authenticates diagnostics and never sends unverified mapping mutations over SSH", async () => {
    runRobotSystemSshCommand.mockResolvedValue([
      "agibot_software=active", "hal_sensor_running=1", "slam_running=1",
      "lidar_publishers=1", "imu_publishers=1", "odom_publishers=1"
    ].join("\n"));
    const app = buildApp(config, robot());
    try {
      const denied = await app.inject({
        method: "GET",
        url: "/api/robots/default/mapping/health",
        headers: { authorization: "Bearer invalid" }
      });
      expect(denied.statusCode).toBe(401);

      const login = await app.inject({ method: "POST", url: "/api/auth/login", payload: {} });
      const token = login.json().token as string;
      const health = await app.inject({
        method: "GET",
        url: "/api/robots/default/mapping/health",
        headers: { authorization: `Bearer ${token}` }
      });
      expect(health.statusCode).toBe(200);
      expect(runRobotSystemSshCommand).toHaveBeenCalledTimes(1);

      for (const action of ["start", "save"]) {
        const blocked = await app.inject({
          method: "POST",
          url: `/api/robots/default/mapping/${action}`,
          headers: { authorization: `Bearer ${token}` },
          payload: { name: "new-map" }
        });
        expect(blocked.statusCode).toBe(409);
        expect(blocked.json().message).toContain("建图接口尚未确认");
      }
      expect(runRobotSystemSshCommand).toHaveBeenCalledTimes(1);
    } finally {
      await app.close();
    }
  });
});
