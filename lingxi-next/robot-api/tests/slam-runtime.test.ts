import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import type { FastifyInstance } from "fastify";
import { DemoAdapter } from "../src/adapters/demo";
import { pairingCodeHash } from "../src/auth";
import { buildInheritedPointApi, pointSocketConfiguration } from "../../robot-runtime/src/point-api";

const fixture = JSON.parse(readFileSync("shared/fixtures/slam-point-v1.synthetic.json", "utf8"));
const environment = (mode = "live") => ({
  LINGXI_SLAM_POINT_FD: "3", LINGXI_SLAM_SESSION: fixture.session, LINGXI_SLAM_INPUT_MODE: mode,
});
const packet = (mode: number) => {
  // Live protocol bytes are used only in this explicitly isolated synthetic fixture.
  const bytes = Buffer.from(fixture.hex as string, "hex");
  bytes[10] = mode;
  bytes.writeDoubleLE(Number(process.hrtime.bigint()) / 1e9, 40);
  return bytes;
};

describe("explicit inherited point API startup configuration", () => {
  it("requires the complete descriptor, session and mode without implicit live defaults", () => {
    expect(pointSocketConfiguration(environment())).toEqual({ fd: 3, session: fixture.session, inputMode: "live" });
    for (const key of ["LINGXI_SLAM_POINT_FD", "LINGXI_SLAM_SESSION", "LINGXI_SLAM_INPUT_MODE"]) {
      const config = { ...environment(), [key]: undefined };
      expect(() => pointSocketConfiguration(config)).toThrow();
    }
    for (const fd of ["0", "1", "2", "03", "+3", "3.0", "3 ", "1024", "-3", "Infinity"]) {
      expect(() => pointSocketConfiguration({ ...environment(), LINGXI_SLAM_POINT_FD: fd })).toThrow();
    }
    for (const mode of ["", "LIVE", "auto"]) expect(() => pointSocketConfiguration(environment(mode))).toThrow();
  });

  it("rejects Windows and non-socket descriptors before taking ownership", async () => {
    for (const platform of ["win32", "linux"]) {
      const operations = { platform, isSocket: vi.fn(() => false), open: vi.fn(() => new PassThrough()) };
      await expect(buildInheritedPointApi(environment(), {}, operations)).rejects.toThrow("socket");
      expect(operations.open).not.toHaveBeenCalled();
      if (platform === "win32") expect(operations.isSocket).not.toHaveBeenCalled();
    }
  });

  it("rejects an invalid explicit read-only setting before opening point input", async () => {
    const operations = { platform: "linux", isSocket: () => true, open: vi.fn(() => new PassThrough()) };
    await expect(buildInheritedPointApi({ ...environment(), LINGXI_POINT_READ_ONLY: "yes" }, {}, operations))
      .rejects.toThrow("read_only");
    expect(operations.open).not.toHaveBeenCalled();
  });
});

describe("owned point socket to existing paired API", () => {
  let app: FastifyInstance | undefined, directory: string | undefined, pipe: PassThrough | undefined;
  afterEach(async () => {
    await app?.close();
    pipe?.destroy();
    if (directory) await rm(directory, { recursive: true, force: true });
    vi.unstubAllEnvs();
  });

  async function prepare(mode = "live", readOnly = false) {
    directory = await mkdtemp(path.join(tmpdir(), "lingxi-inherited-point-"));
    pipe = new PassThrough();
    const adapter = new DemoAdapter(), snapshot = await adapter.getSnapshot();
    snapshot.connected = false;
    snapshot.checks = snapshot.checks.map((check) => ({ ...check, ready: false }));
    vi.spyOn(adapter, "getSnapshot").mockResolvedValue(snapshot);
    const nativeLidar = vi.spyOn(adapter, "getLidarFrame");
    app = await buildInheritedPointApi({
      ...environment(mode), ...(readOnly ? { LINGXI_POINT_READ_ONLY: "1" } : {}),
    }, {
      adapter, demoMode: true, stateDirectory: directory, staticDirectory: path.join(directory, "no-static"),
      pairingCodeHash: pairingCodeHash("isolated-point-runtime-test"),
    }, { platform: "linux", isSocket: () => true, open: () => pipe! });
    const pair = await app.inject({
      method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "isolated-point-runtime-test" },
    });
    return { adapter, nativeLidar, token: pair.json().token as string };
  }

  async function nextMessage(socket: Awaited<ReturnType<FastifyInstance["injectWS"]>>) {
    return new Promise<{ type: string; frame?: { points: number[]; robotPose?: unknown; cells?: unknown } }>(
      (resolve) => socket.once("message", (bytes) => resolve(JSON.parse(bytes.toString()))),
    );
  }

  it("streams points through the existing authenticated WebSocket without granting readiness", async () => {
    const { token, nativeLidar } = await prepare();
    const headers = { authorization: `Bearer ${token}` };
    const before = (await app!.inject({ url: "/api/v1/state", headers })).json();
    const socket = await app!.injectWS("/api/v1/lidar/events", {
      headers: { "sec-websocket-protocol": `lingxi, bearer.${token}` },
    });
    try {
      pipe!.write(packet(1));
      const result = await nextMessage(socket);
      expect(result).toMatchObject({
        type: "slam-preview", inputMode: "live", coordinateUnits: "native-raw", frame: { points: fixture.points },
      });
      expect(result.frame?.robotPose).toBeUndefined();
      expect(result.frame?.cells).toBeUndefined();
      expect(nativeLidar).not.toHaveBeenCalled();
      const after = (await app!.inject({ url: "/api/v1/state", headers })).json();
      expect(after.checks).toEqual(before.checks);
      expect(after.navigation).toEqual(before.navigation);
      expect(after.mapping).toBe(before.mapping);
      expect((await app!.inject({ method: "POST", url: "/api/v1/mapping/start", headers })).statusCode).toBe(409);
      pipe!.end();
      expect(await nextMessage(socket)).toMatchObject({ type: "lidar-unavailable" });
    } finally { socket.terminate(); }
  });

  for (const [mode, byte] of [["recorded", 2], ["synthetic", 3]] as const) {
    it(`does not serve ${mode} points as live data`, async () => {
      const { token } = await prepare(mode);
      const socket = await app!.injectWS("/api/v1/lidar/events", {
        headers: { "sec-websocket-protocol": `lingxi, bearer.${token}` },
      });
      try {
        pipe!.write(packet(byte));
        expect(await nextMessage(socket)).toMatchObject({ type: "lidar-unavailable" });
      } finally { socket.terminate(); }
    });
  }

  it("closes the adopted input on application shutdown and does not reopen it", async () => {
    await prepare();
    await app!.close();
    expect(pipe!.destroyed).toBe(true);
    await app!.close();
    expect(pipe!.destroyed).toBe(true);
  });

  it("isolated read-only mode permits pairing and reading but never dispatches mutation routes", async () => {
    const { token, adapter } = await prepare("live", true);
    const headers = { authorization: `Bearer ${token}` };
    const map = vi.spyOn(adapter, "startMapping");
    expect((await app!.inject({ url: "/api/v1/state", headers })).statusCode).toBe(200);
    for (const [method, url] of [
      ["POST", "/api/v1/mapping/start"], ["POST", "/api/v1/navigation/point"],
      ["POST", "/api/v1/navigation/control"], ["POST", "/api/v1/agent/events"],
      ["PUT", "/api/v1/settings/ai"], ["PUT", "/api/v1/points/synthetic"],
      ["DELETE", "/api/v1/tours/synthetic"],
    ] as const) {
      const response = await app!.inject({ method, url, headers });
      expect(response.statusCode).toBe(403);
      expect(response.json().message).toBe("isolated_point_runtime_read_only");
    }
    expect(map).not.toHaveBeenCalled();
    expect((await app!.inject({ method: "POST", url: "/api/v1/session/revoke", headers })).statusCode).toBe(204);
  });

  it("closes adopted input when API construction fails", async () => {
    vi.stubEnv("LINGXI_PAIRING_CODE_SHA256", "");
    pipe = new PassThrough();
    directory = await mkdtemp(path.join(tmpdir(), "lingxi-point-start-failure-"));
    await expect(buildInheritedPointApi(environment(), {
      demoMode: false, pairingCodeHash: "", stateDirectory: directory,
    }, { platform: "linux", isSocket: () => true, open: () => pipe! })).rejects.toThrow("PAIRING");
    expect(pipe.destroyed).toBe(true);
  });
});
