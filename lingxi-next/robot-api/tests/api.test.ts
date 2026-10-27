import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import { JsonStateStore } from "../src/store";
import { DemoAdapter } from "../src/adapters/demo";

describe("paired local console API", () => {
  let app: FastifyInstance;
  let directory: string;
  let token: string;
  let adapter: DemoAdapter;
  let speak: ReturnType<typeof vi.fn>;
  let cancel: ReturnType<typeof vi.fn>;
  const headers = () => ({ authorization: `Bearer ${token}` });
  beforeEach(async () => {
    directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-api-"));
    adapter = new DemoAdapter();
    speak = vi.fn(async () => ({ firstAudioMs: 10 }));
    cancel = vi.fn(async () => undefined);
    app = await buildServer({
      demoMode: true, stateDirectory: directory, staticDirectory: path.join(directory, "no-static"),
      pairingCodeHash: pairingCodeHash("test-pairing-only"), internalAgentToken: "internal-test-token",
      adapter, agentSpeaker: { speak, cancel },
    });
    const paired = await app.inject({
      method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "test-pairing-only" },
    });
    expect(paired.statusCode).toBe(201);
    token = paired.json().token;
  });
  afterEach(async () => {
    await app?.close();
    await rm(directory, { recursive: true, force: true });
  });
  it("requires authentication for state and command routes", async () => {
    expect((await app.inject("/api/v1/state")).statusCode).toBe(401);
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start" })).statusCode).toBe(401);
    expect((await app.inject("/api/v1/lidar/events")).statusCode).toBe(401);
  });
  it("rejects wrong pairing codes and remote HTTP pairing", async () => {
    expect((await app.inject({ method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "invalid" } })).statusCode).toBe(401);
    expect((await app.inject({
      method: "POST", url: "/api/v1/session/pair", remoteAddress: "192.168.1.4",
      payload: { pairingCode: "test-pairing-only" },
    })).statusCode).toBe(426);
  });
  it("accepts its own origin and rejects unrelated origins", async () => {
    expect((await app.inject({ url: "/api/v1/state", headers: { ...headers(), origin: "http://localhost:80", host: "localhost:80" } })).statusCode).toBe(200);
    expect((await app.inject({ url: "/api/v1/state", headers: { ...headers(), origin: "https://evil.example" } })).statusCode).toBe(403);
  });
  it("credentials are write-only, merge atomically, and never enter state", async () => {
    const secret = "test-secret-must-not-return";
    const written = await app.inject({
      method: "PUT", url: "/api/v1/settings/credentials", headers: headers(),
      payload: { apiKey: secret, linksoulAppId: "id", linksoulAppKey: "key", linksoulAppSecret: "retriever-secret", agentCode: "agent" },
    });
    expect(written.statusCode).toBe(200);
    for (const url of ["/api/v1/state", "/api/v1/settings", "/api/v1/settings/credentials"]) {
      expect((await app.inject({ url, headers: headers() })).body).not.toContain(secret);
    }
    expect((await app.inject({ url: "/api/v1/settings", headers: headers() })).json()).toMatchObject({
      credentialConfigured: true, retrieverConfigured: true,
    });
    expect(await readdir(path.join(directory, "credentials"))).toEqual(["ai.json"]);
    expect(await readFile(path.join(directory, "state.json"), "utf8")).not.toContain(secret);
  });
  it("does not write credentials over a remote cleartext connection", async () => {
    expect((await app.inject({
      method: "PUT", url: "/api/v1/settings/credentials", headers: headers(),
      remoteAddress: "192.168.1.20", payload: { apiKey: "test-key" },
    })).statusCode).toBe(426);
  });
  it("persists map creation only after adapter confirmation and fetches grid separately", async () => {
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers: headers() })).statusCode).toBe(202);
    const result = await app.inject({ method: "POST", url: "/api/v1/mapping/stop-save", headers: headers(), payload: { name: "测试地图" } });
    expect(result.json().confirmed).toBe(true);
    const id = result.json().map.id;
    const maps = await app.inject({ url: "/api/v1/maps", headers: headers() });
    expect(maps.body).not.toContain('"cells"');
    const grid = await app.inject({ url: `/api/v1/maps/${id}/grid`, headers: headers() });
    expect(grid.json().cells.length).toBe(grid.json().width * grid.json().height);
  });
  it("adding a normal point preserves the existing start point", async () => {
    const state = (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
    const created = await app.inject({
      method: "POST", url: "/api/v1/points", headers: headers(),
      payload: { mapId: state.activeMapId, name: "新点位", x: 0, y: 0, yaw: 0 },
    });
    expect(created.statusCode).toBe(201);
    const points = (await app.inject({ url: "/api/v1/state", headers: headers() })).json().points;
    expect(points.filter((item: { isStart: boolean }) => item.isStart)).toHaveLength(1);
  });
  it("registers existing native map references before creating first-use points", async () => {
    const store = new JsonStateStore(directory);
    await store.update((db) => ({ ...db, maps: [], points: [], tours: [], activeMapId: undefined }));
    const state = (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(state.activeMapId).toBe("demo-hall");
    expect((await store.read()).maps).toHaveLength(2);
    expect((await app.inject({ method: "POST", url: "/api/v1/points", headers: headers(),
      payload: { mapId: state.activeMapId, name: "首次点位", x: 1, y: 2, yaw: 0 } })).statusCode).toBe(201);
  });
  it("revokes sessions", async () => {
    await app.inject({ method: "POST", url: "/api/v1/session/revoke", headers: headers() });
    expect((await app.inject({ url: "/api/v1/state", headers: headers() })).statusCode).toBe(401);
  });
  it("closes an existing WebSocket after its paired session is revoked", async () => {
    await app.ready();
    const socket = await app.injectWS("/api/v1/events", {
      headers: { "sec-websocket-protocol": `lingxi, bearer.${token}` },
    });
    const closed = new Promise<number>((resolve) => socket.once("close", (code) => resolve(code)));
    await app.inject({ method: "POST", url: "/api/v1/session/revoke", headers: headers() });
    expect(await closed).toBe(1008);
  });
  it("streams laser points separately from occupancy grids and closes revoked lidar sessions", async () => {
    await app.ready();
    const socket = await app.injectWS("/api/v1/lidar/events", {
      headers: { "sec-websocket-protocol": `lingxi, bearer.${token}` },
    });
    const payload = await new Promise<string>((resolve) => socket.once("message", (bytes) => resolve(bytes.toString())));
    expect(JSON.parse(payload)).toMatchObject({ type: "lidar", frame: { source: "laser-scan", fixedFrame: true } });
    expect(JSON.parse(payload).frame.points.length).toBeGreaterThan(300);
    expect(payload).not.toContain('"cells"');
    const closed = new Promise<number>((resolve) => socket.once("close", resolve));
    await app.inject({ method: "POST", url: "/api/v1/session/revoke", headers: headers() });
    expect(await closed).toBe(1008);
  });
  it("reports unavailable data instead of streaming a stale cloud", async () => {
    const stale = await adapter.getLidarFrame();
    stale.receivedAtMs -= 5000;
    vi.spyOn(adapter, "getLidarFrame").mockResolvedValue(stale);
    await app.ready();
    const socket = await app.injectWS("/api/v1/lidar/events", {
      headers: { "sec-websocket-protocol": `lingxi, bearer.${token}` },
    });
    const payload = await new Promise<string>((resolve) => socket.once("message", (bytes) => resolve(bytes.toString())));
    expect(JSON.parse(payload).type).toBe("lidar-unavailable");
    expect(payload).not.toContain('"points"');
    socket.close();
  });
  it("rejects unverified agent text and off-robot callbacks", async () => {
    const event = { kind: "recognition", eventId: "asr-1", sessionId: "s1", transcript: "去入口", confidence: 0.99, isFinal: true, occurredAt: Date.now() };
    expect((await app.inject({
      method: "POST", url: "/api/v1/agent/events", headers: { "x-agent-token": "internal-test-token" }, payload: event,
    })).statusCode).toBe(204);
    expect((await app.inject({
      method: "POST", url: "/api/v1/agent/events", remoteAddress: "192.168.1.4",
      headers: { "x-agent-token": "internal-test-token" }, payload: event,
    })).statusCode).toBe(403);
  });
  it("serializes concurrent credentials and state updates without loss", async () => {
    const store = new JsonStateStore(directory);
    await Promise.all([store.writeCredentials({ apiKey: "test-a" }), store.writeCredentials({ agentCode: "test-b" })]);
    expect(await store.readCredentials()).toMatchObject({ apiKey: "test-a", agentCode: "test-b" });
    await Promise.all(Array.from({ length: 15 }, () => store.update((db) => ({ ...db, idleReturnSeconds: db.idleReturnSeconds + 1 }))));
    expect((await store.read()).idleReturnSeconds).toBe(45);
  });
  it("blocks map switching and mapping while navigation is active", async () => {
    const state = (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
    const navigating = await app.inject({ method: "POST", url: "/api/v1/navigation/point",
      headers: headers(), payload: { pointId: state.points[1].id } });
    expect(navigating.statusCode).toBe(202);
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers: headers() })).statusCode).toBe(409);
    expect((await app.inject({ method: "POST", url: "/api/v1/maps/demo-lab/activate",
      headers: headers(), payload: {} })).statusCode).toBe(409);
  });
  it("keeps exactly one active map after switching", async () => {
    expect((await app.inject({ method: "POST", url: "/api/v1/maps/demo-lab/activate",
      headers: headers(), payload: {} })).statusCode).toBe(200);
    const state = (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(state.maps.filter((map: { active: boolean }) => map.active).map((map: { id: string }) => map.id)).toEqual(["demo-lab"]);
  });
  it("preserves a dispatched command when its spoken confirmation fails", async () => {
    const event = (payload: object) => app.inject({ method: "POST", url: "/api/v1/agent/events",
      headers: { "x-agent-token": "internal-test-token" }, payload: { sessionId: "voice", occurredAt: Date.now(), ...payload } });
    await event({ kind: "wake", eventId: "wake" });
    speak.mockRejectedValueOnce(new Error("speaker unavailable"));
    const result = await event({ kind: "recognition", eventId: "command", transcript: "去灵犀展台", confidence: 0.99, isFinal: true });
    expect(result.json()).toMatchObject({ accepted: true, category: "command", speechDelivered: false });
    expect(result.json().deliveryError).toBeTruthy();
    const state = (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(state.navigation.pointId).toBe("point-2");
  });
  it("does not cancel a current reply when a duplicate wake arrives", async () => {
    const wake = { kind: "wake", eventId: "wake", sessionId: "voice", occurredAt: Date.now() };
    const post = (payload: object) => app.inject({ method: "POST", url: "/api/v1/agent/events",
      headers: { "x-agent-token": "internal-test-token" }, payload });
    await post(wake);
    let release!: (value: { firstAudioMs: number }) => void;
    const started = new Promise<void>((resolve) => {
      speak.mockImplementationOnce(() => new Promise((finish) => { release = finish; resolve(); }));
    });
    const answering = post({ kind: "recognition", eventId: "asr", sessionId: "voice",
      occurredAt: Date.now(), transcript: "去不存在地点", confidence: 0.99, isFinal: true });
    await started;
    expect((await post(wake)).statusCode).toBe(204);
    expect(cancel).not.toHaveBeenCalled();
    release({ firstAudioMs: 10 });
    expect((await answering).statusCode).toBe(200);
  });
});
