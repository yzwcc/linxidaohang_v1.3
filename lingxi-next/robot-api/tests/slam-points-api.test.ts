import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import type { FastifyInstance } from "fastify";
import type { LidarFrame } from "../../shared/contracts";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import { DemoAdapter } from "../src/adapters/demo";
import { SlamPointPipeSource } from "../src/slam-points";

describe("isolated point pipe to paired API WebSocket", () => {
  let app: FastifyInstance | undefined;
  let directory: string | undefined;
  let source: SlamPointPipeSource | undefined;
  afterEach(async () => {
    source?.close();
    await app?.close();
    if (directory) await rm(directory, { recursive: true, force: true });
  });

  it("streams synthetic contract points without granting mapping or navigation readiness", async () => {
    const fixture = JSON.parse(readFileSync("shared/fixtures/slam-point-v1.synthetic.json", "utf8"));
    const pipe = new PassThrough();
    let now = 10;
    source = new SlamPointPipeSource(pipe, fixture.session, "live", () => now);
    // The live mode byte is exercised only in this isolated test, never on a robot.
    const bytes = Buffer.from(fixture.hex as string, "hex"); bytes[10] = 1;
    pipe.write(bytes);
    directory = await mkdtemp(path.join(tmpdir(), "lingxi-point-api-"));
    const adapter = new DemoAdapter();
    const snapshot = await adapter.getSnapshot();
    snapshot.connected = false;
    snapshot.checks = snapshot.checks.map((check) => ({ ...check, ready: false }));
    vi.spyOn(adapter, "getSnapshot").mockResolvedValue(snapshot);
    const nativeLidar = vi.spyOn(adapter, "getLidarFrame");
    const startMapping = vi.spyOn(adapter, "startMapping");
    app = await buildServer({
      demoMode: true, adapter, lidarSource: source, stateDirectory: directory,
      staticDirectory: path.join(directory, "no-static"),
      pairingCodeHash: pairingCodeHash("synthetic-point-api-only"),
      agentSpeaker: { speak: async () => ({ firstAudioMs: 1 }), cancel: async () => undefined },
    });
    const paired = await app.inject({
      method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "synthetic-point-api-only" },
    });
    const headers = { authorization: `Bearer ${paired.json().token}` };
    const before = (await app.inject({ url: "/api/v1/state", headers })).json();
    await app.ready();
    const socket = await app.injectWS("/api/v1/lidar/events", {
      headers: { "sec-websocket-protocol": `lingxi, bearer.${paired.json().token}` },
    });
    try {
      const next = () => new Promise<{ type: string; frame?: LidarFrame & { cells?: unknown } }>((resolve) => {
        socket.once("message", (message) => resolve(JSON.parse(message.toString())));
      });
      const message = await next();
      expect(message).toMatchObject({
        type: "lidar", frame: { source: "point-cloud", epoch: fixture.session, points: fixture.points },
      });
      expect(message.frame?.robotPose).toBeUndefined();
      expect(message.frame?.cells).toBeUndefined();
      expect(nativeLidar).not.toHaveBeenCalled();
      expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers })).statusCode).toBe(409);
      expect(startMapping).not.toHaveBeenCalled();
      const after = (await app.inject({ url: "/api/v1/state", headers })).json();
      expect(after.checks).toEqual(before.checks);
      expect(after.navigation).toEqual(before.navigation);
      expect(after.mapping).toBe(before.mapping);
      now = 12.01;
      expect(await next()).toMatchObject({ type: "lidar-unavailable" });
      source.close();
      expect(await next()).toMatchObject({ type: "lidar-unavailable" });
    } finally {
      socket.close();
    }
  });
});
