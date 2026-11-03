import { afterEach, describe, expect, it } from "vitest";
import { createServer, type Server, type Socket } from "node:net";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import type { MapPoint, RobotMap } from "../../shared/contracts";
import { physicalNavigationStateSchema, type PhysicalNavigationCommand, type PhysicalNavigationState } from "../../shared/physical-navigation";
import { PhysicalNavigationAdapter } from "../src/adapters/physical-navigation";
import { UnixRosNavigationBackend, type PhysicalNavigationBackend } from "../src/physical-navigation-backend";

const session = "a".repeat(32);
const mapId = `slam-${"b".repeat(32)}`;
const identity = {
  mapId, receiptSha256: "c".repeat(64), calibrationSha256: "d".repeat(64), footprintSha256: "e".repeat(64),
};
const map: RobotMap = { id: mapId, name: "hall", active: true };
const point: MapPoint = { id: "point-a", mapId, name: "A", x: 1, y: 2, yaw: 0, isStart: false };

function state(patch: Partial<PhysicalNavigationState> = {}): PhysicalNavigationState {
  return {
    protocol: 1, source: "ros2-nav2", session, sequence: 0, identity,
    phase: "idle", detail: "", ticket: null, pointId: null, requiresStop: false,
    outputQuiescent: true, pose: { x: 0, y: 0, yaw: 0 },
    checks: { nav2Lifecycle: true, localization: true, tf: true, costmap: true,
      terrain: true, footprint: true, velocityOutput: true },
    navigationReady: true, physicalMotionSent: false, ...patch,
  };
}

function baseAdapter() {
  return {
    demoMode: false,
    getSnapshot: async () => ({ connected: true, checks: [
      { id: "navigation", label: "navigation", ready: false, detail: "" },
    ], mapping: false, actionBusy: false, navigation: { status: "idle" as const },
      motionPresets: [], expressions: [], linkcraftResources: [], updatedAt: new Date().toISOString() }),
    getLidarFrame: async () => ({ frameId: "lidar", stampMs: 1, receivedAtMs: Date.now(), points: [] }),
    listMaps: async () => [map],
    startMapping: async () => undefined,
    stopAndSaveMap: async () => map,
    getStoredMapGrid: async () => ({ width: 1, height: 1, resolution: 1, origin: [0, 0, 0] as [number, number, number], data: [0] }),
    activateMap: async () => undefined,
    startNavigation: async () => undefined,
    controlNavigation: async () => undefined,
    startPresetMotion: async () => "motion",
    waitPresetMotion: async () => undefined,
    playExpression: async () => undefined,
    executeLinkcraftResource: async () => undefined,
    confirmActionsIdle: async () => undefined,
  };
}

function fakeBackend(states: PhysicalNavigationState[], commandResult?: PhysicalNavigationState): PhysicalNavigationBackend {
  return {
    physical: true,
    state: async () => states.shift() ?? state(),
    command: async (command: PhysicalNavigationCommand) => {
      expect(command.session).toBe(session);
      return commandResult ?? state({ sequence: 1, phase: command.action === "stop" ? "idle" : "requested",
        ticket: command.action === "stop" ? null : { task_id: command.taskId, epoch: "f".repeat(32) },
        pointId: command.action === "stop" ? null : point.id,
        outputQuiescent: command.action === "stop", physicalMotionSent: command.action !== "stop" });
    },
    close: async () => undefined,
  };
}

describe("physical navigation admission", () => {
  it.each(Object.keys(state().checks) as (keyof PhysicalNavigationState["checks"])[])(
    "rejects a missing %s gate before sending a command", async (gate) => {
    const notReady = state({ checks: { ...state().checks, [gate]: false }, navigationReady: false });
    let calls = 0;
    const backend = fakeBackend([notReady]);
    backend.command = async () => { calls++; return state(); };
    await expect(new PhysicalNavigationAdapter(baseAdapter(), backend).startNavigation(point, map, "task-1"))
      .rejects.toMatchObject({ message: "navigation_admission_gates_not_ready" });
    expect(calls).toBe(0);
  });

  it("admits a fully evidenced goal and binds its task", async () => {
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([state()]));
    await expect(adapter.startNavigation(point, map, "task-1")).resolves.toBeUndefined();
  });

  it("turns a stale sequence into an uncertain state", async () => {
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([state(), state()]));
    await adapter.getSnapshot();
    const snapshot = await adapter.getSnapshot();
    expect(snapshot.navigation.status).toBe("failed");
    expect(snapshot.navigation.detail).toContain("without_reseed");
  });

  it("requires a confirmed stop after an uncertain acknowledgement", async () => {
    const backend = fakeBackend([state()]);
    backend.command = async (command) => {
      if (command.action !== "stop") throw new Error("lost_ack");
      return state({ sequence: 3 });
    };
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), backend);
    await expect(adapter.startNavigation(point, map, "task-1")).rejects.toMatchObject({ message: "lost_ack" });
    await expect(adapter.startNavigation(point, map, "task-2")).rejects.toMatchObject({
      message: "physical_navigation_requires_confirmed_stop_after_uncertain_state",
    });
    await expect(adapter.controlNavigation("stop")).resolves.toBeUndefined();
  });

  it("rejects an acknowledgement from another session", async () => {
    const result = state({ session: "9".repeat(32), sequence: 1 });
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([state()], result));
    await expect(adapter.startNavigation(point, map, "task-1")).rejects.toMatchObject({
      message: "physical_navigation_command_ack_identity_mismatch",
    });
  });

  it.each([
    { phase: "idle" as const, ticket: null, pointId: null, outputQuiescent: true },
    { ticket: { task_id: "another-task", epoch: "f".repeat(32) } },
    { pointId: "another-point" },
  ])("requires a start acknowledgement bound to the requested task and point: %j", async (patch) => {
    const result = state({ sequence: 1, phase: "requested", outputQuiescent: false,
      ticket: { task_id: "task-1", epoch: "f".repeat(32) }, pointId: point.id, ...patch });
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([state()], result));
    await expect(adapter.startNavigation(point, map, "task-1")).rejects.toMatchObject({
      message: "physical_navigation_command_unconfirmed",
    });
    await expect(adapter.confirmActionsIdle()).rejects.toThrow("requires_confirmed_stop");
  });

  it("preserves the uncertain interlock on later valid polling", async () => {
    const backend = fakeBackend([state(), state({ sequence: 2 })]);
    backend.command = async () => { throw new Error("lost_ack"); };
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), backend);
    await expect(adapter.startNavigation(point, map, "task-1")).rejects.toThrow("lost_ack");
    expect(await adapter.getSnapshot()).toMatchObject({
      navigation: { status: "failed", requiresStop: true, detail: "lost_ack" },
      navigationExecution: { navigationReady: false },
    });
  });

  it.each([
    { phase: "running" as const, outputQuiescent: false },
    { phase: "stopping" as const, outputQuiescent: false },
    { requiresStop: true, navigationReady: false },
  ])("does not confirm stop while output retirement is incomplete: %j", async (patch) => {
    const running = state({ phase: "running", outputQuiescent: false,
      ticket: { task_id: "task-1", epoch: "f".repeat(32) }, pointId: point.id });
    const result = { ...running, sequence: 1, phase: "idle" as const, outputQuiescent: true, ...patch };
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([running], result));
    await expect(adapter.controlNavigation("stop", "task-1")).rejects.toThrow("command_unconfirmed");
    await expect(adapter.startNavigation(point, map, "task-2")).rejects.toThrow("requires_confirmed_stop");
  });

  it("requires stop acknowledgements to preserve the newly read session and evidence", async () => {
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([state()], state({
      session: "9".repeat(32), sequence: 1,
    })));
    await expect(adapter.controlNavigation("stop")).rejects.toThrow("ack_identity_mismatch");
  });

  it("confirms pause only with a retired output and the same epoch", async () => {
    const running = state({ phase: "running", outputQuiescent: false,
      ticket: { task_id: "task-1", epoch: "f".repeat(32) }, pointId: point.id });
    const paused = { ...running, sequence: 1, phase: "paused" as const, outputQuiescent: true };
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([running], paused));
    await expect(adapter.controlNavigation("pause", "task-1")).resolves.toBeUndefined();
  });

  it("rejects a pause acknowledgement with a different epoch", async () => {
    const running = state({ phase: "running", outputQuiescent: false,
      ticket: { task_id: "task-1", epoch: "f".repeat(32) }, pointId: point.id });
    const paused = { ...running, sequence: 1, phase: "paused" as const, outputQuiescent: true,
      ticket: { task_id: "task-1", epoch: "8".repeat(32) } };
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([running], paused));
    await expect(adapter.controlNavigation("pause", "task-1")).rejects.toThrow("command_unconfirmed");
  });

  it.each([false, true])("requires a new resume epoch (new=%s)", async (newEpoch) => {
    const paused = state({ phase: "paused", ticket: { task_id: "task-1", epoch: "f".repeat(32) }, pointId: point.id });
    const resumed = { ...paused, sequence: 1, phase: "requested" as const, outputQuiescent: false,
      ticket: { task_id: "task-1", epoch: (newEpoch ? "8" : "f").repeat(32) } };
    const operation = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([paused], resumed))
      .controlNavigation("resume", "task-1");
    if (newEpoch) await expect(operation).resolves.toBeUndefined();
    else await expect(operation).rejects.toThrow("command_unconfirmed");
  });

  it("does not accept a task epoch changed by a late status callback", async () => {
    const running = state({ phase: "running", outputQuiescent: false,
      ticket: { task_id: "task-1", epoch: "f".repeat(32) }, pointId: point.id });
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([running, {
      ...running, sequence: 1, ticket: { task_id: "task-1", epoch: "8".repeat(32) },
    }]));
    await adapter.getSnapshot();
    expect(await adapter.getSnapshot()).toMatchObject({ navigation: { status: "failed", requiresStop: true } });
  });

  it("retains the last known motion fact if the next state read fails", async () => {
    const running = state({ phase: "running", outputQuiescent: false, physicalMotionSent: true,
      ticket: { task_id: "task-1", epoch: "f".repeat(32) }, pointId: point.id });
    const backend = fakeBackend([running]);
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), backend);
    await adapter.getSnapshot();
    backend.state = async () => { throw new Error("bridge_unavailable"); };
    expect(await adapter.getSnapshot()).toMatchObject({
      navigationExecution: { physicalMotionSent: true, navigationReady: false },
    });
  });

  it.each(["finished", "failed"] as const)("permits the next point after confirmed %s with a fresh epoch", async (phase) => {
    const ticket = { task_id: "task-1", epoch: "f".repeat(32) };
    const backend = fakeBackend([state(), state({ sequence: 2, phase, ticket, pointId: point.id })]);
    let sequence = 1;
    backend.command = async (command) => state({ sequence: sequence++, phase: "requested", outputQuiescent: false,
      ticket: { task_id: command.taskId, epoch: command.taskId === "task-1" ? ticket.epoch : "8".repeat(32) }, pointId: point.id });
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), backend);
    await adapter.startNavigation(point, map, "task-1");
    sequence = 3;
    await expect(adapter.startNavigation(point, map, "task-2")).resolves.toBeUndefined();
  });

  it("serializes polling behind a pending command acknowledgement", async () => {
    let finish!: (value: PhysicalNavigationState) => void;
    let started!: () => void;
    const commandStarted = new Promise<void>((resolve) => { started = resolve; });
    const backend = fakeBackend([state()]);
    backend.command = () => { started(); return new Promise((resolve) => { finish = resolve; }); };
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), backend);
    const start = adapter.startNavigation(point, map, "task-1");
    await commandStarted;
    let polls = 0;
    const requested = state({ sequence: 1, phase: "requested", outputQuiescent: false,
      ticket: { task_id: "task-1", epoch: "f".repeat(32) }, pointId: point.id });
    backend.state = async () => { polls++; return { ...requested, sequence: 2 }; };
    const snapshot = adapter.getSnapshot();
    await Promise.resolve();
    expect(polls).toBe(0);
    finish(requested);
    await start;
    expect(await snapshot).toMatchObject({ navigation: { status: "requested", taskId: "task-1" } });
    expect(polls).toBe(1);
  });

  it("does not silently ignore an initial pose without a map activation endpoint", async () => {
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), fakeBackend([state()]));
    await expect(adapter.activateMap(map, { x: 1, y: 1, yaw: 0 })).rejects.toThrow("activation_unavailable");
  });

  it("only accepts map activation after output and task retirement", async () => {
    const backend = fakeBackend([state()]);
    backend.activateMap = async () => state({ sequence: 1, phase: "running", outputQuiescent: false,
      ticket: { task_id: "task-1", epoch: "f".repeat(32) }, pointId: point.id });
    await expect(new PhysicalNavigationAdapter(baseAdapter(), backend).activateMap(map))
      .rejects.toThrow("activation_identity_mismatch");
  });

  it("rebinds a selected map without carrying over old task or readiness", async () => {
    const nextIdentity = { ...identity, mapId: `slam-${"1".repeat(32)}`, receiptSha256: "2".repeat(64) };
    const nextMap = { ...map, id: nextIdentity.mapId };
    const nextPoint = { ...point, mapId: nextIdentity.mapId };
    const checks = Object.fromEntries(Object.keys(state().checks).map((key) => [key, false])) as PhysicalNavigationState["checks"];
    const next = state({ sequence: 2, identity: nextIdentity, checks, navigationReady: false });
    const backend = fakeBackend([state({ sequence: 1 }), { ...next, sequence: 3 }]);
    let calls = 0;
    backend.activateMap = async (request) => {
      expect(request.mapId).toBe(nextIdentity.mapId);
      expect(request.evidence).toBeUndefined();
      return next;
    };
    backend.command = async () => { calls++; return next; };
    const adapter = new PhysicalNavigationAdapter(baseAdapter(), backend);
    await expect(adapter.activateMap(nextMap)).resolves.toBeUndefined();
    await expect(adapter.startNavigation(nextPoint, nextMap, "new-map-task"))
      .rejects.toThrow("navigation_admission_gates_not_ready");
    expect(calls).toBe(0);
  });

  it("rejects a map-switch reply until the fresh stop completes", async () => {
    const nextIdentity = { ...identity, mapId: `slam-${"1".repeat(32)}` };
    const backend = fakeBackend([state()]);
    backend.activateMap = async () => state({ sequence: 1, identity: nextIdentity, phase: "stopping",
      outputQuiescent: false, requiresStop: true, navigationReady: false });
    await expect(new PhysicalNavigationAdapter(baseAdapter(), backend).activateMap({ ...map, id: nextIdentity.mapId }))
      .rejects.toThrow("activation_identity_mismatch");
  });

  it("rejects inconsistent readiness evidence", () => {
    expect(physicalNavigationStateSchema.safeParse(state({
      checks: { ...state().checks, tf: false }, navigationReady: true,
    })).success).toBe(false);
  });

  it("accepts taskless stopping for initial-stop and map-handoff status only", () => {
    const stopping = state({ phase: "stopping", outputQuiescent: false, requiresStop: true, navigationReady: false });
    expect(physicalNavigationStateSchema.safeParse(stopping).success).toBe(true);
    expect(physicalNavigationStateSchema.safeParse({ ...stopping, outputQuiescent: true }).success).toBe(false);
    for (const phase of ["requested", "running", "paused", "finished"] as const) {
      expect(physicalNavigationStateSchema.safeParse({ ...stopping, phase }).success).toBe(false);
    }
  });
});

describe.skipIf(process.platform !== "linux")("physical navigation Unix transport", () => {
  let server: Server | undefined;
  let root: string | undefined;
  const clients = new Set<Socket>();
  afterEach(async () => {
    for (const client of clients) client.destroy();
    clients.clear();
    if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
    server = undefined;
    if (root) await rm(root, { recursive: true, force: true });
    root = undefined;
  });

  it("serializes independent request connections with matching state replies", async () => {
    root = await mkdtemp(path.join(tmpdir(), "lx-nav-"));
    const endpoint = path.join(root, "navigation.sock");
    let calls = 0;
    server = createServer((socket) => {
      clients.add(socket);
      socket.on("close", () => clients.delete(socket));
      let buffer = "";
      socket.on("data", (chunk) => {
        buffer += chunk.toString();
        if (!buffer.endsWith("\n")) return;
        const request = JSON.parse(buffer);
        expect(request.action).toBe("state");
        socket.end(JSON.stringify({ id: request.id, ok: true, value: state({ sequence: ++calls }) }) + "\n");
      });
    });
    await new Promise<void>((resolve) => server!.listen(endpoint, resolve));
    const backend = new UnixRosNavigationBackend(endpoint);
    try {
      const results = await Promise.all([backend.state(), backend.state()]);
      expect(results.map((result) => result.sequence)).toEqual([1, 2]);
      expect(calls).toBe(2);
    } finally { await backend.close(); }
  });

  it("settles a mismatched response id immediately and retires the transport", async () => {
    root = await mkdtemp(path.join(tmpdir(), "lx-nav-"));
    const endpoint = path.join(root, "navigation.sock");
    let calls = 0;
    server = createServer((socket) => {
      calls++;
      clients.add(socket);
      socket.on("close", () => clients.delete(socket));
      socket.once("data", () => socket.end(JSON.stringify({ id: "0".repeat(32), ok: true, value: state() }) + "\n"));
    });
    await new Promise<void>((resolve) => server!.listen(endpoint, resolve));
    const backend = new UnixRosNavigationBackend(endpoint);
    try {
      await expect(backend.state()).rejects.toThrow("bridge_unavailable");
      await expect(backend.state()).rejects.toThrow("bridge_unavailable");
      expect(calls).toBe(1);
    } finally { await backend.close(); }
  }, 1000);

  it("does not reconnect or replay a command after timeout", async () => {
    root = await mkdtemp(path.join(tmpdir(), "lx-nav-"));
    const endpoint = path.join(root, "navigation.sock");
    let calls = 0;
    server = createServer((socket) => {
      calls++;
      clients.add(socket);
      socket.on("close", () => clients.delete(socket));
      // Deliberately leave the request unacknowledged; timeout is uncertain.
    });
    await new Promise<void>((resolve) => server!.listen(endpoint, resolve));
    const backend = new UnixRosNavigationBackend(endpoint);
    await expect(backend.state()).rejects.toMatchObject({ message: "physical_navigation_bridge_timeout" });
    await expect(backend.state()).rejects.toMatchObject({ message: "physical_navigation_bridge_unavailable" });
    expect(calls).toBe(1);
    await backend.close();
  }, 3000);
});
