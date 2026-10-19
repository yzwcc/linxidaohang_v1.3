import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.js";
import { robotGatewayBaseUrl } from "../src/robotClient.js";

const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
});

describe("robot gateway URL", () => {
  it("uses the voice gateway port instead of the navigation bridge port", () => {
    process.env.DEPLOYMENT_MODE = "robot";
    process.env.ROBOT_BRIDGE_PORT = "18082";
    process.env.ROBOT_JAVA_AGENT_BRIDGE_PORT = "18083";

    expect(robotGatewayBaseUrl(loadConfig())).toBe("http://127.0.0.1:18083");
  });
});
