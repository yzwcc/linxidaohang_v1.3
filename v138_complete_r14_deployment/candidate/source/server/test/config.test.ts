import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.js";

const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
});

describe("loadConfig", () => {
  it("uses the local cloud proxy when no transport host is configured", () => {
    delete process.env.ROBOT_HOST;

    expect(loadConfig().robot.host).toBe("127.0.0.1");
  });

  it("accepts any explicitly configured transport host", () => {
    process.env.ROBOT_HOST = "192.168.1.221";

    expect(loadConfig().robot.host).toBe("192.168.1.221");
  });

  it("uses robot Java Agent mode and home from environment", () => {
    process.env.JAVA_AGENT_EXECUTION_MODE = "robot";
    process.env.ROBOT_JAVA_AGENT_HOME = "/opt/lingxi-agent";
    process.env.ROBOT_JAVA_AGENT_BRIDGE_PORT = "19083";

    const config = loadConfig();

    expect(config.robot.javaAgentExecutionMode).toBe("robot");
    expect(config.robot.robotJavaAgentHome).toBe("/opt/lingxi-agent");
    expect(config.robot.robotJavaAgentBridgePort).toBe(19083);
  });

  it("falls back to local mode for an invalid execution mode", () => {
    process.env.JAVA_AGENT_EXECUTION_MODE = "invalid";

    expect(loadConfig().robot.javaAgentExecutionMode).toBe("local");
  });

  it("locks robot product deployments to the local robot", () => {
    process.env.DEPLOYMENT_MODE = "robot";
    process.env.ROBOT_CONNECTION_MODE = "tunnel";
    process.env.ROBOT_HOST = "192.168.0.203";

    const config = loadConfig();

    expect(config.deploymentMode).toBe("robot");
    expect(config.robot.connectionMode).toBe("direct");
    expect(config.robot.host).toBe("127.0.0.1");
  });
});
