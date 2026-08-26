import { describe, expect, it } from "vitest";
import { buildStartupSnapshot } from "../src/startupSnapshot.js";

const healthyRobot = {
  bridge_online: true,
  map_web_online: true,
  voice_gateway_online: true,
  navigation_gateway_online: true,
  nav_status: { active_map_name: "map-1", localization: { ok: true } },
  points: { point_names: ["起点"] }
};

const healthyJavaAgent = {
  running: true,
  navBaseUrlMatchesRobot: true,
  lingxiConnected: true,
  robotOnline: true
};

describe("buildStartupSnapshot", () => {
  it("marks a healthy robot-local deployment ready", () => {
    const snapshot = buildStartupSnapshot({
      deploymentMode: "robot",
      connectionMode: "direct",
      proxyHost: "127.0.0.1",
      targetHost: "127.0.0.1",
      robot: healthyRobot,
      relocalization: null,
      javaAgent: healthyJavaAgent,
      javaAgentReachable: true,
      targetIdentity: { ok: true }
    });

    expect(snapshot.ready).toBe(true);
    expect(snapshot.navigation_ready).toBe(true);
    expect(snapshot.checklist).toHaveLength(7);
  });

  it("keeps services ready while reporting localization separately", () => {
    const snapshot = buildStartupSnapshot({
      deploymentMode: "robot",
      connectionMode: "direct",
      proxyHost: "127.0.0.1",
      targetHost: "127.0.0.1",
      robot: {
        ...healthyRobot,
        nav_status: { active_map_name: "map-1", localization: { ok: false, message: "等待重定位" } }
      },
      relocalization: null,
      javaAgent: healthyJavaAgent,
      javaAgentReachable: true,
      targetIdentity: { ok: true }
    });

    expect(snapshot.ready).toBe(true);
    expect(snapshot.navigation_ready).toBe(false);
    expect(snapshot.checklist.at(-1)).toMatchObject({ key: "relocalization", ok: false, detail: "等待重定位" });
  });
});
