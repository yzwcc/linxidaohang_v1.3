import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import type { TourTask } from "../../shared/contracts";
import type { PhysicalNavigationCommand, PhysicalNavigationState } from "../../shared/physical-navigation";
import { PhysicalNavigationAdapter } from "../src/adapters/physical-navigation";
import { mappingConsoleAdapter } from "../src/mapping-backend";
import type { PhysicalNavigationBackend } from "../src/physical-navigation-backend";
import { JobManager } from "../src/jobs";
import { JsonStateStore } from "../src/store";

describe("independent tour lifecycle with synthetic bridge and real persistent store (no robot)", () => {
  let root: string, store: JsonStateStore, jobs: JobManager;
  let state: PhysicalNavigationState, generation: number, lost: boolean;
  let pendingOutcome: "failed" | "finished" | undefined;
  let script: TourTask;
  let commands: PhysicalNavigationCommand[];
  let outcomes: Map<string, Array<"failed" | "finished" | "running">>;
  const speak = vi.fn(async (_text: string, _id: string) => ({ firstAudioMs: 10 }));
  const cancel = vi.fn(async (_id: string) => undefined);
  const mapId = `slam-${"a".repeat(32)}`;

  beforeEach(async () => {
    root = await mkdtemp(path.join(tmpdir(), "lingxi-tour-physical-lifecycle-"));
    store = new JsonStateStore(root);
    script = { id: "tour", mapId, name: "合成双点导览", repeatCount: 2, idleReturnSeconds: 30,
      updatedAt: new Date().toISOString(), steps: [
        { id: "step-a", pointId: "point-a", narration: "合成讲解 A", waitSeconds: 0 },
        { id: "step-b", pointId: "point-b", narration: "合成讲解 B", waitSeconds: 0 },
      ] };
    await store.update((db) => ({ ...db, activeMapId: mapId, idleReturnSeconds: 30,
      maps: [{ id: mapId, name: "synthetic map", active: true }], tours: [script], points: [
        { id: "point-a", mapId, name: "A", x: 0, y: 0, yaw: 0, isStart: true },
        { id: "point-b", mapId, name: "B", x: 1, y: 0, yaw: 0, isStart: false },
      ] }));
    state = { protocol: 1, source: "ros2-nav2", session: "b".repeat(32), sequence: 0,
      identity: { mapId, receiptSha256: "c".repeat(64), calibrationSha256: "d".repeat(64), footprintSha256: "e".repeat(64) },
      phase: "idle", detail: "synthetic bridge; no sensors or output transport", ticket: null, pointId: null,
      requiresStop: false, outputQuiescent: true, pose: { x: 0, y: 0, yaw: 0 },
      checks: { nav2Lifecycle: true, localization: true, tf: true, costmap: true,
        terrain: true, footprint: true, velocityOutput: true }, navigationReady: true, physicalMotionSent: false };
    lost = false;
    pendingOutcome = undefined;
    generation = 0;
    commands = [];
    outcomes = new Map();
    const current = () => structuredClone({ ...state, sequence: ++state.sequence, poseObservedAtMs: Date.now() });
    const bridge: PhysicalNavigationBackend = { physical: true,
      state: async () => {
        if (lost) throw new Error("synthetic_bridge_connection_lost");
        if (pendingOutcome) {
          state = { ...state, phase: pendingOutcome, outputQuiescent: true };
          pendingOutcome = undefined;
        }
        return current();
      },
      command: async (command) => {
        commands.push(structuredClone(command));
        if (command.action === "stop") {
          pendingOutcome = undefined;
          state = { ...state, phase: "idle", ticket: null, pointId: null, outputQuiescent: true };
        } else if (command.action === "pause") {
          state = { ...state, phase: "paused", outputQuiescent: true };
        } else {
          const outcome = command.action === "resume" ? "running" : outcomes.get(command.pointId!)?.shift() ?? "finished";
          pendingOutcome = outcome === "running" ? undefined : outcome;
          state = { ...state, phase: "running", pointId: command.pointId!, outputQuiescent: false,
            ticket: { task_id: command.taskId, epoch: (++generation).toString(16).padStart(32, "0") } };
        }
        return current();
      }, close: async () => undefined,
    };
    speak.mockClear(); cancel.mockClear();
    const adapter = new PhysicalNavigationAdapter(mappingConsoleAdapter(), bridge);
    jobs = new JobManager(adapter, store, { speak, cancel },
      { pollMs: 10, navigationTimeoutMs: 120_000, quietResumeMs: 5000 });
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
  });
  afterEach(async () => {
    lost = false;
    await jobs?.dispose().catch(() => undefined);
    vi.clearAllTimers();
    vi.useRealTimers();
    await rm(root, { recursive: true, force: true });
  });

  it("retries one explicit failure, skips only that visit, persists its record and completes the next round", async () => {
    outcomes.set("point-a", ["failed", "failed"]);
    script.steps[0].waitSeconds = 1;
    await jobs.startTour(script);
    await vi.advanceTimersByTimeAsync(0);
    await store.update((db) => db);
    await vi.advanceTimersByTimeAsync(2000);
    expect(commands.map((command) => command.pointId)).toEqual(["point-a", "point-a", "point-b", "point-a", "point-b"]);
    expect(new Set(commands.map((command) => command.taskId)).size).toBe(5);
    expect(speak.mock.calls.map(([text]) => text)).toEqual(["合成讲解 B", "合成讲解 A", "合成讲解 B"]);
    expect(jobs.state()).toMatchObject({ status: "finished", detail: "讲解已结束，存在失败或跳过项" });
    expect(jobs.history()).toHaveLength(1);
    const restored = await new JsonStateStore(root).read();
    expect(restored.jobRecords).toEqual(jobs.history());
    expect(restored.jobRecords[0]).toMatchObject({ task: script.id, point: "point-a", reason: "导航重试一次后失败，已跳过" });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(commands).toHaveLength(5);
  });

  it("runs all twenty two-point rounds with unique task epochs and no automatic home return", async () => {
    script.repeatCount = 20;
    await jobs.startTour(script);
    await vi.advanceTimersByTimeAsync(1000);
    expect(commands.map((command) => command.pointId)).toEqual(Array.from({ length: 20 }, () => ["point-a", "point-b"]).flat());
    expect(new Set(commands.map((command) => command.taskId)).size).toBe(40);
    expect(generation).toBe(40);
    expect(speak).toHaveBeenCalledTimes(40);
    expect(jobs.state()).toMatchObject({ status: "finished", detail: "讲解已完成" });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(commands).toHaveLength(40);
    expect(jobs.history()).toEqual([]);
  });

  it("requires explicit stop after bridge loss and ignores a late arrival after reconnect", async () => {
    outcomes.set("point-a", ["running"]);
    await jobs.startTour(script);
    await vi.advanceTimersByTimeAsync(20);
    expect(commands).toHaveLength(1);
    lost = true;
    await vi.advanceTimersByTimeAsync(50);
    expect(jobs.state()).toMatchObject({ status: "failed", requiresStop: true });
    lost = false;
    state = { ...state, phase: "finished", outputQuiescent: true };
    await vi.advanceTimersByTimeAsync(60_000);
    expect(commands).toHaveLength(1);
    expect(speak).not.toHaveBeenCalled();
    expect(jobs.history()).toEqual([]);
    await expect(jobs.control("resume")).rejects.toThrow("先停止");
    await jobs.control("stop");
    expect(commands.at(-1)?.action).toBe("stop");
    script.repeatCount = 1;
    await jobs.startTour(script);
    await vi.advanceTimersByTimeAsync(1000);
    expect(jobs.state().status).toBe("finished");
    expect(commands.map((command) => command.action)).toEqual(["start", "stop", "start", "start"]);
    expect(speak).toHaveBeenCalledTimes(2);
  });
});
