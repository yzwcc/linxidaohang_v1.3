import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import { OfflineNavigationAdapter } from "../src/adapters/offline-navigation";
import { StdioOfflineNavigationBackend, type OfflineNavigationBackend } from "../src/offline-navigation-backend";
import { offlineNavigationStateSchema, type OfflineNavigationCommand, type OfflineNavigationState, type OfflineScene } from "../../shared/offline-navigation";
import { buildServer } from "../src/server";
import { JsonStateStore } from "../src/store";
import { DemoAdapter } from "../src/adapters/demo";

const identity = { map_id: "slam-" + "a".repeat(32), receipt_sha256: "b".repeat(64),
  metric_evidence_sha256: "c".repeat(64), traversability_sha256: "d".repeat(64) };
const point = { id: "target", mapId: identity.map_id, name: "接待台", x: 2, y: 0, yaw: 0, isStart: false };
const map = { id: identity.map_id, name: "offline", active: true };

/** Protocol test double only. Real ROS integration is in offline-nav2-integration.test.ts. */
class Backend implements OfflineNavigationBackend {
  readonly offlineOnly = true;
  value: OfflineNavigationState = { protocol: 1, session: "e".repeat(32), sequence: 0, identity,
    phase: "idle", detail: "idle", ticket: null, pointId: null, requiresStop: false, outputQuiescent: true,
    pose: { x: 0, y: 0, yaw: 0 }, commandCount: 0, offlineOnly: true, syntheticInputs: true,
    physicalMotionSent: false, navigationReady: false };
  commands: OfflineNavigationCommand[] = [];
  async state() { return structuredClone({ ...this.value, sequence: ++this.value.sequence }); }
  async scene(): Promise<OfflineScene> { return { protocol: 1, session: this.value.session, identity,
    case: "open", grid: { width: 1, height: 1, resolution: .05, originX: 0, originY: 0, cells: [0] }, points: [],
    offlineOnly: true, syntheticInputs: true, physicalMotionSent: false, navigationReady: false }; }
  async command(command: OfflineNavigationCommand) {
    this.commands.push(command);
    this.value = { ...this.value, ticket: { task_id: command.taskId,
      epoch: command.action === "resume" ? "2".repeat(32) : command.action === "start" ? "1".repeat(32) : command.expectedEpoch! },
      pointId: command.pointId ?? this.value.pointId,
      phase: command.action === "stop" ? "idle" : command.action === "pause" ? "paused" : "requested",
      outputQuiescent: ["pause", "stop"].includes(command.action), requiresStop: false };
    return this.state();
  }
  async close() {}
}

describe("explicit offline Nav2 adapter", () => {
  it("retains non-production flags and exposes synthetic point cloud, not a vendor map", async () => {
    const adapter = new OfflineNavigationAdapter(new Backend());
    const snapshot = await adapter.getSnapshot();
    expect(snapshot.navigationExecution).toEqual({ backend: "nav2-offline", syntheticInputs: true, physicalMotionSent: false, navigationReady: false });
    expect(snapshot.checks.filter((check) => check.ready).map((check) => check.id)).toEqual(["navigation"]);
    expect((await adapter.listMaps())[0].mapId).toBeUndefined();
    expect((await adapter.getLidarFrame()).sensorFrame).toBe("synthetic_world");
  });
  it("binds every control to session, map and epoch; resume requires a new epoch", async () => {
    const backend = new Backend(), adapter = new OfflineNavigationAdapter(backend);
    await adapter.startNavigation(point, map, "task");
    await adapter.controlNavigation("pause", "task");
    await adapter.controlNavigation("resume", "task");
    await adapter.controlNavigation("stop", "task");
    expect(backend.commands.map((command) => command.expectedEpoch)).toEqual([null, "1".repeat(32), "1".repeat(32), "2".repeat(32)]);
  });
  it("rejects wrong-map goals, wrong task controls and every hardware or mapping operation", async () => {
    const backend = new Backend(), adapter = new OfflineNavigationAdapter(backend);
    await expect(adapter.startNavigation({ ...point, mapId: "foreign" }, map, "task")).rejects.toThrow();
    await adapter.startNavigation(point, map, "task");
    await expect(adapter.controlNavigation("stop", "other")).rejects.toThrow();
    for (const operation of [() => adapter.startMapping(), () => adapter.stopAndSaveMap("map"),
      () => adapter.activateMap(map), () => adapter.startPresetMotion("wave"), () => adapter.playExpression("smile"),
      () => adapter.executeLinkcraftResource("id"), () => adapter.confirmActionsIdle()]) await expect(operation()).rejects.toThrow();
    expect(backend.commands).toHaveLength(1);
  });
  it.each(["session", "sequence", "identity"])("latches mismatched %s instead of trusting late state", async (field) => {
    const backend = new Backend(), adapter = new OfflineNavigationAdapter(backend);
    await adapter.getSnapshot();
    if (field === "session") backend.value.session = "f".repeat(32);
    if (field === "sequence") backend.value.sequence = -1;
    if (field === "identity") backend.value.identity = { ...identity, receipt_sha256: "f".repeat(64) };
    await expect(adapter.getSnapshot()).rejects.toThrow();
  });
  it("never confirms pause from an unretired source and requires an explicit confirmed stop", async () => {
    const backend = new Backend(), adapter = new OfflineNavigationAdapter(backend);
    await adapter.startNavigation(point, map, "task");
    const realCommand = backend.command.bind(backend);
    backend.command = async (command) => ({ ...await realCommand(command), outputQuiescent: false });
    await expect(adapter.controlNavigation("pause", "task")).rejects.toThrow();
    expect((await adapter.getSnapshot()).navigation.requiresStop).toBe(true);
    backend.command = realCommand;
    await expect(adapter.controlNavigation("resume", "task")).rejects.toThrow();
    await adapter.controlNavigation("stop", "task");
    expect((await adapter.getSnapshot()).navigation.requiresStop).toBe(false);
  });
  it("does not accept physical or inconsistent readiness from a backend", () => {
    const backend = new Backend();
    expect(offlineNavigationStateSchema.safeParse({ ...backend.value, physicalMotionSent: true }).success).toBe(false);
    expect(offlineNavigationStateSchema.safeParse({ ...backend.value, navigationReady: true }).success).toBe(false);
    expect(offlineNavigationStateSchema.safeParse({ ...backend.value, phase: "running" }).success).toBe(false);
  });
  it("refreshes dynamic obstacle points and latches a changed scene identity", async () => {
    const backend = new Backend(), adapter = new OfflineNavigationAdapter(backend);
    const scene = backend.scene.bind(backend);
    let points: number[] = [];
    backend.scene = async () => ({ ...await scene(), points });
    expect((await adapter.getLidarFrame()).points).toEqual([]);
    points = [1, 0, .7];
    await new Promise((resolve) => setTimeout(resolve, 170));
    expect((await adapter.getLidarFrame()).points).toEqual(points);
    backend.scene = async () => ({ ...await scene(), identity: { ...identity, receipt_sha256: "f".repeat(64) } });
    await new Promise((resolve) => setTimeout(resolve, 170));
    await expect(adapter.getLidarFrame()).rejects.toThrow();
    backend.scene = scene;
    expect((await adapter.getSnapshot()).navigation.requiresStop).toBe(true);
  });
});

describe("stdio offline backend framing", () => {
  const children: StdioOfflineNavigationBackend[] = [];
  afterEach(async () => { await Promise.all(children.splice(0).map((child) => child.close())); });
  function child(source: string) {
    const backend = new StdioOfflineNavigationBackend(process.execPath, ["-e", source], process.cwd());
    children.push(backend); return backend;
  }
  it.each(["wrong-id", "extra-line", "oversize", "eof"])("rejects %s and never retries", async (kind) => {
    const backend = child(`process.stdin.once('data', b => { const q=JSON.parse(b);
      ${kind === "eof" ? "process.exit(0)" : kind === "oversize" ? "process.stdout.write('x'.repeat(270000))"
        : `process.stdout.write(JSON.stringify({id:${kind === "wrong-id" ? "'0'.repeat(32)" : "q.id"},ok:true,value:{}})+'\\n'${kind === "extra-line" ? "+'{}\\n'" : ""})`} });`);
    await expect(backend.state()).rejects.toThrow();
    await expect(backend.state()).rejects.toThrow();
  });
  it("serializes concurrent reads and validates typed responses", async () => {
    const fixture = new Backend();
    const backend = child(`let n=0, buffer=''; process.stdin.on('data', b => {buffer+=b;let end;
      while((end=buffer.indexOf('\\n'))>=0){const q=JSON.parse(buffer.slice(0,end));buffer=buffer.slice(end+1);
      process.stdout.write(JSON.stringify({id:q.id,ok:true,value:{...${JSON.stringify(fixture.value)},sequence:++n}})+'\\n');}});`);
    const results = await Promise.all([backend.state(), backend.state(), backend.state()]);
    expect(results.map((value) => value.sequence)).toEqual([1, 2, 3]);
  });
  it("latches a schema-invalid response even if its framing and request ID are valid", async () => {
    const backend = child(`process.stdin.on('data', b => {const q=JSON.parse(b);
      process.stdout.write(JSON.stringify({id:q.id,ok:true,value:{}})+'\\n');});`);
    await expect(backend.state()).rejects.toThrow();
    await expect(backend.scene()).rejects.toThrow();
  });
  it("bounds an unresponsive cold start and rejects later queued work without replay", async () => {
    vi.useFakeTimers();
    const backend = child("process.stdin.resume()");
    const request = expect(backend.state()).rejects.toThrow();
    try {
      await vi.advanceTimersByTimeAsync(15001);
      await request;
      await expect(backend.state()).rejects.toThrow();
    } finally { vi.useRealTimers(); }
  });
});

describe("offline workspace API and voice isolation", () => {
  let app: FastifyInstance | undefined, directory: string;
  afterEach(async () => { await app?.close(); app = undefined; if (directory) await rm(directory, { recursive: true, force: true }); vi.unstubAllGlobals(); });
  it("routes awakened point commands through the backend, refuses noise/actions and keeps Q&A from canceling navigation", async () => {
    directory = await mkdtemp(path.join(os.tmpdir(), "offline-api-"));
    const backend = new Backend(), fetcher = vi.fn(() => { throw new Error("network forbidden"); });
    vi.stubGlobal("fetch", fetcher);
    app = await buildServer({ offlineNavigationBackend: backend, stateDirectory: directory,
      staticDirectory: path.join(directory, "none"), internalAgentToken: "offline-test-token" });
    const store = new JsonStateStore(directory);
    await store.update((db) => ({ ...db, maps: [map], points: [point], activeMapId: map.id, idleReturnSeconds: 0 }));
    const agent = (event: object) => app!.inject({ method: "POST", url: "/api/v1/agent/events",
      headers: { "x-agent-token": "offline-test-token" }, payload: event });
    let count = 0;
    const recognition = (transcript: string) => ({ kind: "recognition", eventId: `event${++count}`,
      sessionId: "voice", occurredAt: Date.now(), confidence: .99, isFinal: true, transcript });
    expect((await agent(recognition("去接待台"))).statusCode).toBe(204);
    expect(backend.commands).toHaveLength(0);
    await agent({ kind: "wake", eventId: "wake", sessionId: "voice", occurredAt: Date.now() });
    expect((await agent(recognition("去接待台"))).json().accepted).toBe(true);
    expect(backend.commands.map((command) => command.action)).toEqual(["start"]);
    expect((await agent(recognition("请问今天展馆几点关门？"))).json()).toMatchObject({ category: "rejected", accepted: false,
      text: "我暂时无法从已配置知识库或实时状态中核实这项信息。" });
    expect((await agent(recognition("做个后空翻"))).json()).toMatchObject({ category: "clarification" });
    expect(backend.commands.map((command) => command.action)).toEqual(["start"]);
    const paired = await app.inject({ method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "lingxi-dev-only" } });
    const headers = { authorization: `Bearer ${paired.json().token}` };
    expect((await app.inject({ url: "/api/v1/state", headers, remoteAddress: "192.168.1.2" })).statusCode).toBe(403);
    expect((await app.inject({ url: "/api/v1/state", headers })).json().navigationExecution.navigationReady).toBe(false);
    expect((await app.inject({ method: "POST", url: "/api/v1/navigation/control", headers, payload: { action: "stop" } })).statusCode).toBe(200);
    expect(backend.commands.map((command) => command.action)).toEqual(["start", "stop"]);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("rejects mixing offline navigation with robot or shared SLAM adapters", async () => {
    await expect(buildServer({ offlineNavigationBackend: new Backend(), adapter: new DemoAdapter() })).rejects.toThrow("isolated_offline_navigation");
    await expect(buildServer({ offlineNavigationBackend: new Backend(), agentSpeaker: { speak: async () => ({ firstAudioMs: 1 }) } })).rejects.toThrow();
  });
});
