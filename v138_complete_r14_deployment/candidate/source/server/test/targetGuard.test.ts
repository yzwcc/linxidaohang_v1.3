import { describe, expect, it, vi } from "vitest";
import type { AppConfig } from "../src/config.js";
import type { RobotClient } from "../src/robotClient.js";

const mocks = vi.hoisted(() => ({
  verify: vi.fn(async () => ({
    ok: false,
    expectedHost: "10.115.72.203",
    hostname: "agi-old",
    addresses: ["192.168.0.203"],
    detail: "隧道当前连接 192.168.0.203，不是 10.115.72.203",
    error: "隧道目标与网页填写的机器人 IP 不一致"
  }))
}));

vi.mock("../src/tunnelTarget.js", () => ({
  invalidateTunnelTargetIdentity: vi.fn(),
  verifyTunnelTarget: mocks.verify,
  waitForTunnelTarget: mocks.verify
}));

const { buildApp } = await import("../src/app.js");

const config: AppConfig = {
  deploymentMode: "cloud",
  host: "127.0.0.1",
  port: 0,
  publicBaseUrl: "http://127.0.0.1",
  robot: {
    id: "default",
    connectionMode: "tunnel",
    host: "127.0.0.1",
    bridgePort: 18080,
    webPort: 18002,
    mapRoot: "/maps",
    sshUser: "agi",
    sshPassword: "",
    sshPort: 10022,
    systemSshUser: "run",
    systemSshPassword: "",
    systemSshPort: 10022,
    navHome: "/home/agi/robot_local_navigation_v3",
    javaAgentHome: "../robot_local_nav_java",
    javaAgentExecutionMode: "robot",
    robotJavaAgentHome: "/home/run/lingxi_java_agent",
    robotJavaAgentBridgePort: 18083,
    javaAgentAutoStart: true
  },
  auth: { appId: "", appSecret: "", tokenSecret: "test", devBypass: true, tokenTtlSec: 3600 }
};

describe("tunnel target guard", () => {
  it("blocks robot mutations when the tunnel still points to another robot", async () => {
    const navStart = vi.fn(async () => ({ ok: true }));
    const app = buildApp(config, { navStart } as unknown as RobotClient);
    const response = await app.inject({
      method: "POST",
      url: "/api/robots/default/nav/start",
      payload: { target_name: "起点" }
    });

    expect(response.statusCode).toBe(409);
    expect(response.json().status).toBe("tunnel_target_mismatch");
    expect(navStart).not.toHaveBeenCalled();
    await app.close();
  });
});
