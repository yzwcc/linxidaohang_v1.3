import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.js";
import { shouldExecuteRobotCommandLocally } from "../src/sshControl.js";

const originalEnv = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnv };
});

describe("robot command transport", () => {
  it("uses local execution for a robot-local console", () => {
    process.env.DEPLOYMENT_MODE = "robot";

    expect(shouldExecuteRobotCommandLocally(loadConfig())).toBe(true);
  });

  it("keeps SSH transport for a cloud console", () => {
    delete process.env.DEPLOYMENT_MODE;
    process.env.ROBOT_HOST = "127.0.0.1";

    expect(shouldExecuteRobotCommandLocally(loadConfig())).toBe(false);
  });
});
