import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import type { TourTask } from "../../shared/contracts";
import type { PhysicalNavigationState } from "../../shared/physical-navigation";
import { HttpActionsBackend, ProductActionsAdapter } from "../src/actions-backend";
import { PhysicalNavigationAdapter } from "../src/adapters/physical-navigation";
import { mappingConsoleAdapter, mappingGuard, type MappingBackendState } from "../src/mapping-backend";
import type { PhysicalNavigationBackend } from "../src/physical-navigation-backend";
import { JobManager } from "../src/jobs";
import { MemoryStore, map, point, tour } from "./fixtures";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}
const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

describe("synthetic tour with real composed adapters and loopback expression HTTP", () => {
  let server: Server, jobs: JobManager, adapter: ProductActionsAdapter;
  let stateReached: ReturnType<typeof deferred>, releaseState: ReturnType<typeof deferred>;
  let writeReached: ReturnType<typeof deferred>, releaseAck: ReturnType<typeof deferred>;
  let holdExpression: boolean, heldRead: boolean, badAck: boolean, settledAttempts: number;
  let holdAck: boolean, remoteRevocation: boolean;
  let expressionWrites: Array<Record<string, unknown>>;
  let script: TourTask;
  const speak = vi.fn(async (_text: string, _id: string) => ({ firstAudioMs: 10 }));
  const cancel = vi.fn(async (_id: string) => undefined);
  let navigation: PhysicalNavigationBackend;

  beforeEach(async () => {
    stateReached = deferred();
    releaseState = deferred();
    writeReached = deferred(); releaseAck = deferred();
    holdExpression = true;
    heldRead = badAck = false;
    holdAck = remoteRevocation = false;
    settledAttempts = 0;
    expressionWrites = [];
    speak.mockClear(); cancel.mockClear();
    let sequence = 0;
    const token = "synthetic-tour-expression-token-not-secret";
    server = createServer(async (request, response) => {
      const send = (code: number, value: object) => {
        response.writeHead(code, { "Content-Type": "application/json" });
        response.end(JSON.stringify(value));
      };
      if (request.headers.authorization !== `Bearer ${token}`) return send(401, {});
      if (request.method === "GET" && request.url === "/v1/state") {
        if (heldRead) {
          heldRead = false;
          stateReached.resolve();
          await releaseState.promise;
        }
        return send(200, { bridgeMode: "actions-only", physicalActionsEnabled: false,
          bridgeEpoch: "d".repeat(32), sequence: ++sequence, observedAtMs: Date.now(),
          actionBusy: false, actionStatus: null, motionPresets: [], linkcraftResources: [], expressions: ["开心"],
          actionServices: { presetMotion: false, expression: true, linkcraftCatalog: false,
            linkcraftExecution: false, linkcraftCatalogFresh: false } });
      }
      if (request.method === "POST" && request.url === "/v1/expression") {
        let bytes = "";
        for await (const chunk of request) bytes += chunk;
        const body = JSON.parse(bytes) as Record<string, unknown>;
        expressionWrites.push(body);
        writeReached.resolve();
        if (holdAck) await releaseAck.promise;
        if (remoteRevocation) return send(409, { message: "动作请求已失效", uncertain: false });
        return send(202, { ...body, requestId: badAck ? "0".repeat(32) : body.requestId,
          accepted: true, completed: false });
      }
      send(404, {});
    });
    await new Promise<void>((resolve) => { server.listen(0, "127.0.0.1", resolve); });
    const actions = new HttpActionsBackend(`http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`, token);
    const playExpression = actions.playExpression.bind(actions);
    vi.spyOn(actions, "playExpression").mockImplementation(async (name, authorize) => {
      if (holdExpression) { holdExpression = false; heldRead = true; }
      try { await playExpression(name, authorize); }
      finally { settledAttempts += 1; }
    });
    const mapId = `slam-${"a".repeat(32)}`;
    let state: PhysicalNavigationState = {
      protocol: 1, source: "ros2-nav2", session: "a".repeat(32), sequence: 0,
      identity: { mapId, receiptSha256: "b".repeat(64), calibrationSha256: "c".repeat(64),
        footprintSha256: "d".repeat(64) }, phase: "idle", detail: "synthetic-only-no-robot",
      ticket: null, pointId: null, requiresStop: false, outputQuiescent: true,
      pose: { x: 0, y: 0, yaw: 0 }, checks: { nav2Lifecycle: true, localization: true, tf: true,
        costmap: true, terrain: true, footprint: true, velocityOutput: true },
      navigationReady: true, physicalMotionSent: false,
    };
    let generation = 0;
    navigation = {
      physical: true,
      state: vi.fn(async () => structuredClone({ ...state, sequence: ++state.sequence })),
      command: vi.fn(async (command) => {
        state = { ...state, sequence: state.sequence + 1, phase: command.action === "stop" ? "idle" : "finished",
          ticket: command.action === "stop" ? null : { task_id: command.taskId,
            epoch: (++generation).toString(16).padStart(32, "0") }, pointId: command.action === "stop" ? null : command.pointId!,
          outputQuiescent: true };
        return structuredClone(state);
      }),
      close: vi.fn(async () => undefined),
    };
    const idle: MappingBackendState = { backend: "glim", inputMode: "recorded", phase: "idle",
      session: null, mapping: false, cleanupComplete: true };
    const physical = new PhysicalNavigationAdapter(mappingConsoleAdapter(), navigation);
    const mapping = mappingGuard(physical, { state: async () => idle, start: vi.fn(), finish: vi.fn() }, true,
      undefined, { physicalNavigation: true });
    adapter = new ProductActionsAdapter(mapping, actions);
    // Exercise tour dispatch while a current localization tracker remains active.
    adapter.setActivityBusy(async () => true);
    adapter.setExpressionLocalizationReady(async () => true);
    const store = new MemoryStore();
    store.state.maps = [{ ...map, id: mapId }];
    store.state.activeMapId = mapId;
    store.state.points = [{ ...point, mapId }];
    script = { ...tour, mapId, steps: [{ ...tour.steps[0], expression: "开心" }] };
    jobs = new JobManager(adapter, store, { speak, cancel },
      { pollMs: 5, navigationTimeoutMs: 1000, quietResumeMs: 80 });
    adapter.setJobBusy(() => jobs.hasActiveJob());
  });
  afterEach(async () => {
    releaseState.resolve();
    releaseAck.resolve();
    await jobs?.dispose().catch(() => undefined);
    adapter?.close();
    server?.closeAllConnections();
    if (server?.listening) await new Promise<void>((resolve, reject) => {
      server.close((error) => error ? reject(error) : resolve());
    });
    vi.restoreAllMocks();
  });

  it("revokes a not-yet-written expression on manual pause and issues it once only after resume", async () => {
    await jobs.startTour(script);
    await stateReached.promise;
    await jobs.control("pause");
    releaseState.resolve();
    await vi.waitFor(() => expect(settledAttempts).toBe(1));
    expect(expressionWrites).toHaveLength(0);
    expect(speak).not.toHaveBeenCalled();
    await delay(100);
    expect(jobs.state().status).toBe("paused");
    expect(expressionWrites).toHaveLength(0);
    await jobs.control("resume");
    await vi.waitFor(() => expect(jobs.hasActiveJob()).toBe(false));
    expect(expressionWrites).toHaveLength(1);
    expect(speak).toHaveBeenCalledOnce();
    expect(navigation.command).toHaveBeenCalledOnce();
    expect(jobs.state().detail).toBe("讲解已完成");
  });

  it("revokes before write on dialogue interruption and resumes only after dialogue and quiet time", async () => {
    await jobs.startTour(script);
    await stateReached.promise;
    jobs.beginDialogue();
    await jobs.interruptTour();
    releaseState.resolve();
    await vi.waitFor(() => expect(settledAttempts).toBe(1));
    expect(expressionWrites).toHaveLength(0);
    expect(speak).not.toHaveBeenCalled();
    await delay(100);
    expect(expressionWrites).toHaveLength(0);
    jobs.endDialogue();
    await delay(20);
    expect(expressionWrites).toHaveLength(0);
    await vi.waitFor(() => expect(jobs.hasActiveJob()).toBe(false));
    expect(expressionWrites).toHaveLength(1);
    expect(speak).toHaveBeenCalledOnce();
    expect(navigation.command).toHaveBeenCalledOnce();
  });

  it("retires a stopped generation before a delayed state reply and never replays it in a replacement", async () => {
    await jobs.startTour(script);
    await stateReached.promise;
    await jobs.control("stop");
    releaseState.resolve();
    await vi.waitFor(() => expect(settledAttempts).toBe(1));
    expect(expressionWrites).toHaveLength(0);
    expect(speak).not.toHaveBeenCalled();
    await jobs.startTour(script);
    await vi.waitFor(() => expect(jobs.hasActiveJob()).toBe(false));
    expect(expressionWrites).toHaveLength(1);
    expect(speak).toHaveBeenCalledOnce();
    expect(navigation.command).toHaveBeenCalledTimes(2);
  });

  it("never retries an already-written expression with an invalid acknowledgement", async () => {
    holdExpression = false;
    badAck = true;
    await jobs.startTour(script);
    await vi.waitFor(() => expect(jobs.state().status).toBe("failed"));
    await delay(100);
    expect(expressionWrites).toHaveLength(1);
    expect(speak).not.toHaveBeenCalled();
    expect(jobs.state().requiresStop).toBe(true);
    expect(navigation.command).toHaveBeenCalledOnce();
    await jobs.control("stop");
    await expect(jobs.startTour(script)).rejects.toThrow("动作");
    await adapter.confirmActionsIdle();
    badAck = false;
    await jobs.startTour(script);
    await vi.waitFor(() => expect(jobs.hasActiveJob()).toBe(false));
    expect(expressionWrites).toHaveLength(2);
    expect(speak).toHaveBeenCalledOnce();
  });

  it("does not replay an already-written expression when pause occurs before its valid acknowledgement", async () => {
    holdExpression = false;
    holdAck = true;
    await jobs.startTour(script);
    await writeReached.promise;
    await jobs.control("pause");
    await jobs.control("resume");
    releaseAck.resolve();
    await vi.waitFor(() => expect(jobs.hasActiveJob()).toBe(false));
    expect(expressionWrites).toHaveLength(1);
    expect(speak).toHaveBeenCalledOnce();
    expect(navigation.command).toHaveBeenCalledOnce();
  });

  it("does not confuse matching remote error text with a local pre-write cancellation", async () => {
    holdExpression = false;
    remoteRevocation = true;
    await jobs.startTour(script);
    await vi.waitFor(() => expect(jobs.state().status).toBe("failed"));
    await delay(100);
    expect(expressionWrites).toHaveLength(1);
    expect(speak).not.toHaveBeenCalled();
    expect(navigation.command).toHaveBeenCalledOnce();
    expect(jobs.state().requiresStop).toBe(true);
  });
});
