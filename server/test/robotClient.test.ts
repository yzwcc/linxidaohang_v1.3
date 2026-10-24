import { afterEach, describe, expect, it, vi } from "vitest";
import { loadConfig } from "../src/config.js";
import { createRobotClient, robotGatewayBaseUrl } from "../src/robotClient.js";

const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
  vi.restoreAllMocks();
});

describe("robot gateway URL", () => {
  it("uses the voice gateway port instead of the navigation bridge port", () => {
    process.env.DEPLOYMENT_MODE = "robot";
    process.env.ROBOT_BRIDGE_PORT = "18082";
    process.env.ROBOT_JAVA_AGENT_BRIDGE_PORT = "18083";

    expect(robotGatewayBaseUrl(loadConfig())).toBe("http://127.0.0.1:18083");
  });
});

describe("global path safety settings", () => {
  it("reads and saves safety settings through the robot map service", async () => {
    process.env.DEPLOYMENT_MODE = "robot";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      if (init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          path_safety: { robot_radius_m: 0.253, extra_clearance_m: 0.18 }
        });
        return new Response(JSON.stringify({
          nav_mode: "execute",
          path_safety: { robot_radius_m: 0.253, extra_clearance_m: 0.18 }
        }), { status: 200 });
      }
      return new Response(JSON.stringify({
        nav_mode: "execute",
        path_safety: { robot_radius_m: 0.253, extra_clearance_m: 0.1 }
      }), { status: 200 });
    });
    const client = createRobotClient(loadConfig());

    await expect(client.pathSafety()).resolves.toMatchObject({
      path_safety: { robot_radius_m: 0.253, extra_clearance_m: 0.1 }
    });
    await expect(client.savePathSafety({
      robot_radius_m: 0.253,
      extra_clearance_m: 0.18
    })).resolves.toMatchObject({
      path_safety: { robot_radius_m: 0.253, extra_clearance_m: 0.18 }
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toBe("http://127.0.0.1:8002/api/settings");
    expect(String(fetchMock.mock.calls[1][0])).toBe("http://127.0.0.1:8002/api/settings");
  });
});
