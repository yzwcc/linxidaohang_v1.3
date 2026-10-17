import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.js";
import { robotGatewayBaseUrl } from "../src/robotClient.js";
import { createRobotClient } from "../src/robotClient.js";
import { createServer } from "node:http";

const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
});

describe("gateway partial readiness", () => {
  it("keeps navigation available while voice is offline and refreshes recovery", async () => {
    let voice = false;
    const server = createServer((request, response) => {
      response.setHeader("content-type", "application/json");
      if (request.url === "/gateway/health") {
        response.statusCode = voice ? 200 : 503;
        response.end(JSON.stringify({ status: voice ? "online" : "degraded", ok: voice,
          services: { navigation: { ok: true }, voice: { ok: voice } } }));
      } else { response.end('{"ok":true,"maps":[],"point_names":[]}'); }
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
      const config = loadConfig();
      const address = server.address() as { port: number };
      config.robot.host = "127.0.0.1";
      config.robot.bridgePort = config.robot.webPort = config.robot.robotJavaAgentBridgePort = address.port;
      const client = createRobotClient(config);
      const partial = await client.status(true);
      expect(partial.navigation_gateway_online).toBe(true);
      expect(partial.voice_gateway_online).toBe(false);
      voice = true;
      const recovered = await client.status(true);
      expect(recovered.navigation_gateway_online).toBe(true);
      expect(recovered.voice_gateway_online).toBe(true);
    } finally { await new Promise<void>((resolve) => server.close(() => resolve())); }
  });

  it("does not reinterpret an arbitrary HTTP 503 as a healthy gateway", async () => {
    const server = createServer((_request, response) => { response.statusCode = 503; response.end("unavailable"); });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
      const config = loadConfig();
      const address = server.address() as { port: number };
      config.robot.host = "127.0.0.1";
      config.robot.bridgePort = config.robot.webPort = config.robot.robotJavaAgentBridgePort = address.port;
      const result = await createRobotClient(config).status(true);
      expect(result.navigation_gateway_online).toBe(false);
      expect(result.voice_gateway_online).toBe(false);
    } finally { await new Promise<void>((resolve) => server.close(() => resolve())); }
  });
});

describe("robot gateway URL", () => {
  it("uses the voice gateway port instead of the navigation bridge port", () => {
    process.env.DEPLOYMENT_MODE = "robot";
    process.env.ROBOT_BRIDGE_PORT = "18082";
    process.env.ROBOT_JAVA_AGENT_BRIDGE_PORT = "18083";

    expect(robotGatewayBaseUrl(loadConfig())).toBe("http://127.0.0.1:18083");
  });
});
