import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildApp } from "../src/app.js";
import { loadConfig } from "../src/config.js";
import type { RobotClient } from "../src/robotClient.js";

const controls = vi.hoisted(() => ({
  bridge: vi.fn(), voice: vi.fn(), startJava: vi.fn(), statusJava: vi.fn()
}));
vi.mock("../src/javaAgentControl.js", () => ({
  getJavaAgentStatus: controls.statusJava, startJavaAgent: controls.startJava, stopJavaAgent: vi.fn()
}));
vi.mock("../src/sshControl.js", async (original) => ({
  ...await original<typeof import("../src/sshControl.js")>(),
  runBridgeAction: controls.bridge, restartRobotVoiceStack: controls.voice
}));
vi.mock("../src/tunnelTarget.js", async (original) => ({
  ...await original<typeof import("../src/tunnelTarget.js")>(),
  verifyTunnelTarget: vi.fn(async () => ({ ok: true }))
}));

function fixture() {
  const config = loadConfig();
  config.deploymentMode = "robot";
  config.auth.devBypass = true;
  config.robot.connectionMode = "direct";
  const live = {
    bridge_online: true, map_web_online: true, navigation_gateway_online: true,
    voice_gateway_online: false
  };
  const agent = { running: true, lingxiConnected: true, robotOnline: true };
  controls.statusJava.mockImplementation(async () => ({ ...agent }));
  controls.startJava.mockImplementation(async () => ({ ...agent }));
  controls.bridge.mockImplementation(async () => {
    Object.assign(live, { bridge_online: true, map_web_online: true, navigation_gateway_online: true });
    return { output: "started" };
  });
  controls.voice.mockImplementation(async () => {
    live.voice_gateway_online = true;
    return { output: "voice started" };
  });
  const robot = {
    status: vi.fn(async () => ({ ...live })), invalidateStatus: vi.fn(),
    relocalizationStatus: vi.fn(async () => ({ running: false }))
  } as unknown as RobotClient;
  return { app: buildApp(config, robot), live, agent };
}

describe("functional service startup", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.useRealTimers());

  it("repairs voice without restarting healthy navigation or its active tour", async () => {
    const { app } = fixture();
    try {
      const result = await app.inject({ method: "POST", url: "/api/robots/default/start-all" });
      expect(result.json().ok).toBe(true);
      expect(controls.bridge).not.toHaveBeenCalled();
      expect(controls.voice).toHaveBeenCalledTimes(1);
    } finally { await app.close(); }
  });

  it("shares simultaneous starts and releases the guard for a later operation", async () => {
    const { app, live } = fixture();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    controls.voice.mockImplementation(async () => {
      await gate; live.voice_gateway_online = true;
      return { output: "voice started" };
    });
    try {
      const first = app.inject({ method: "POST", url: "/api/robots/default/start-all" });
      const second = app.inject({ method: "POST", url: "/api/robots/default/start-all" });
      await vi.waitFor(() => expect(controls.voice).toHaveBeenCalledTimes(1));
      release();
      expect((await first).json().ok).toBe(true);
      expect((await second).json().ok).toBe(true);
      expect(controls.startJava).toHaveBeenCalledTimes(1);
      expect((await app.inject({ method: "POST", url: "/api/robots/default/start-all" })).json().ok).toBe(true);
      expect(controls.startJava).toHaveBeenCalledTimes(1);
    } finally { release(); await app.close(); }
  });

  it("starts offline navigation and confirms all required endpoints", async () => {
    const { app, live } = fixture();
    Object.assign(live, { bridge_online: false, map_web_online: false, navigation_gateway_online: false });
    try {
      expect((await app.inject({ method: "POST", url: "/api/robots/default/start-all" })).json().ok).toBe(true);
      expect(controls.bridge).toHaveBeenCalledTimes(1);
    } finally { await app.close(); }
  });

  it("does not report success when a gateway stays offline; retry can recover", async () => {
    const { app, live } = fixture();
    live.navigation_gateway_online = false;
    controls.bridge.mockResolvedValue({ output: "command submitted" });
    try {
      await app.ready();
      vi.useFakeTimers();
      const request = app.inject({ method: "POST", url: "/api/robots/default/start-all" });
      await vi.advanceTimersByTimeAsync(32000);
      const failed = (await request).json();
      expect(failed.ok).toBe(false);
      live.navigation_gateway_online = true;
      const retry = app.inject({ method: "POST", url: "/api/robots/default/start-all" });
      await vi.advanceTimersByTimeAsync(1000);
      expect((await retry).json().ok).toBe(true);
    } finally { vi.useRealTimers(); await app.close(); }
  });
});
