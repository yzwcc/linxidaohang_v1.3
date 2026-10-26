import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.js";

const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
});

describe("loadConfig", () => {
  it("defaults the robot bridge to the R18 navigation and mapping port", () => {
    delete process.env.ROBOT_BRIDGE_PORT;
    process.env.DEPLOYMENT_MODE = "robot";
    expect(loadConfig().robot.bridgePort).toBe(18082);
  });

  it("uses the local cloud proxy when no transport host is configured", () => {
    delete process.env.ROBOT_HOST;

    expect(loadConfig().robot.host).toBe("127.0.0.1");
  });

  it("accepts any explicitly configured transport host", () => {
    process.env.ROBOT_HOST = "192.168.1.221";

    expect(loadConfig().robot.host).toBe("192.168.1.221");
  });

  it("requires an explicit AIMDK v1.1.0 overlay instead of assuming the legacy install path", () => {
    delete process.env.ROBOT_AIMDK_SETUP;
    expect(loadConfig().robot.aimdkSetup).toBe("");

    process.env.ROBOT_AIMDK_SETUP = "/opt/aimdk-v1.1.0/install/setup.bash";
    expect(loadConfig().robot.aimdkSetup).toBe("/opt/aimdk-v1.1.0/install/setup.bash");
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
