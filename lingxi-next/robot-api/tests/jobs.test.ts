import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RobotSnapshot } from "../src/domain";
import { DemoAdapter } from "../src/adapters/demo";
import { JobManager } from "../src/jobs";
import { MemoryStore, map, point, tour } from "./fixtures";

describe("navigation and tour lifecycle", () => {
  let adapter: DemoAdapter;
  let store: MemoryStore;
  let jobs: JobManager;
  let nav: RobotSnapshot["navigation"];
  let snapshot: RobotSnapshot;
  const speak = vi.fn(async () => ({ firstAudioMs: 20 }));
  const cancel = vi.fn(async () => undefined);
  beforeEach(async () => {
    vi.useFakeTimers();
    adapter = new DemoAdapter();
    store = new MemoryStore();
    snapshot = await adapter.getSnapshot();
    nav = { status: "idle" };
    vi.spyOn(adapter, "getSnapshot").mockImplementation(async () => ({ ...snapshot, navigation: nav }));
    vi.spyOn(adapter, "startNavigation").mockImplementation(async (_point, _map, taskId) => { nav = { status: "running", taskId }; });
    vi.spyOn(adapter, "controlNavigation").mockImplementation(async (action) => { nav = { ...nav, status: action === "stop" ? "idle" : action === "pause" ? "paused" : "running" }; });
    speak.mockClear();
    cancel.mockClear();
    jobs = new JobManager(adapter, store, { speak, cancel }, { pollMs: 10, navigationTimeoutMs: 120_000, quietResumeMs: 5000 });
  });
  afterEach(async () => {
    await jobs.dispose().catch(() => undefined);
    vi.clearAllTimers();
    vi.useRealTimers();
  });
  it("ignores stale completion IDs and never advances a stopped tour", async () => {
    await jobs.startTour(tour);
    await vi.advanceTimersByTimeAsync(20);
    nav = { status: "finished", taskId: "old-task" };
    await vi.advanceTimersByTimeAsync(100);
    expect(speak).not.toHaveBeenCalled();
    await jobs.control("stop");
    nav = { status: "finished", taskId: "old-task" };
    await vi.advanceTimersByTimeAsync(6000);
    expect(speak).not.toHaveBeenCalled();
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
  });
  it("retries an explicit failure once, skips it, and records it", async () => {
    vi.mocked(adapter.startNavigation).mockImplementation(async (_point, _map, taskId) => { nav = { status: "failed", taskId }; });
    await jobs.startTour(tour);
    await vi.advanceTimersByTimeAsync(100);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(2);
    expect(speak).not.toHaveBeenCalled();
    expect(jobs.history()).toHaveLength(1);
    expect(store.state.jobRecords).toHaveLength(1);
    expect(jobs.state().status).toBe("finished");
  });
  it("never retries an ambiguous command timeout", async () => {
    vi.mocked(adapter.startNavigation).mockRejectedValue(new Error("timeout"));
    await jobs.startTour(tour);
    await vi.advanceTimersByTimeAsync(200);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
    expect(jobs.state().status).toBe("failed");
    await expect(jobs.startTour(tour)).rejects.toThrow();
  });
  it("manual pause cannot be resumed by wake or elapsed quiet time", async () => {
    await jobs.startTour(tour);
    await vi.advanceTimersByTimeAsync(20);
    await jobs.control("pause");
    await jobs.interruptTour();
    nav = { status: "finished", taskId: nav.taskId };
    await vi.advanceTimersByTimeAsync(10_000);
    expect(jobs.state().status).toBe("paused");
    expect(speak).not.toHaveBeenCalled();
    await jobs.control("resume");
    nav = { status: "finished", taskId: nav.taskId };
    await vi.advanceTimersByTimeAsync(100);
    expect(speak).toHaveBeenCalledTimes(1);
  });
  it("questions do not cancel ongoing point navigation", async () => {
    await jobs.startPoint(point);
    jobs.beginDialogue();
    await jobs.interruptTour();
    jobs.endDialogue();
    await vi.advanceTimersByTimeAsync(100);
    expect(adapter.controlNavigation).not.toHaveBeenCalled();
    expect(jobs.state().status).toBe("running");
  });
  it("tour resumes narration only after dialogue and five seconds quiet", async () => {
    let finishSpeech: (() => void) | undefined;
    speak.mockImplementationOnce(() => new Promise((resolve) => { finishSpeech = () => resolve({ firstAudioMs: 20 }); }));
    await jobs.startTour(tour);
    await vi.advanceTimersByTimeAsync(20);
    nav = { status: "finished", taskId: nav.taskId };
    await vi.advanceTimersByTimeAsync(20);
    expect(speak).toHaveBeenCalledTimes(1);
    jobs.beginDialogue();
    await jobs.interruptTour();
    finishSpeech!();
    await vi.advanceTimersByTimeAsync(6000);
    expect(speak).toHaveBeenCalledTimes(1);
    jobs.endDialogue();
    await vi.advanceTimersByTimeAsync(4990);
    expect(speak).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(30);
    expect(speak).toHaveBeenCalledTimes(2);
    expect(cancel).toHaveBeenCalledTimes(1);
  });
  it("does not automatically return home after a tour", async () => {
    await jobs.startTour(tour);
    await vi.advanceTimersByTimeAsync(20);
    nav = { status: "finished", taskId: nav.taskId };
    await vi.advanceTimersByTimeAsync(60_000);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
  });
  it("supports the configurable point-navigation idle return", async () => {
    store.state.idleReturnSeconds = 30;
    await jobs.startPoint(point);
    nav = { status: "finished", taskId: nav.taskId };
    await vi.advanceTimersByTimeAsync(20);
    await vi.advanceTimersByTimeAsync(29_900);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
    jobs.markInteraction();
    await vi.advanceTimersByTimeAsync(29_900);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(200);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(2);
    expect(vi.mocked(adapter.startNavigation).mock.calls[1][0].isStart).toBe(true);
  });
  it("refuses wrong-map or unavailable native navigation", async () => {
    store.state.activeMapId = "other";
    await expect(jobs.startPoint(point)).rejects.toThrow("活动地图");
    store.state.activeMapId = map.id;
    snapshot.checks = [];
    await expect(jobs.startTour(tour)).rejects.toThrow("尚未就绪");
    expect(adapter.startNavigation).not.toHaveBeenCalled();
  });
  it("dispatches an allowlisted voice motion without waiting for its playback state", async () => {
    const start = vi.spyOn(adapter, "startPresetMotion").mockResolvedValue("motion-9");
    const wait = vi.spyOn(adapter, "waitPresetMotion");
    await jobs.runAllowlistedMotion("挥手");
    expect(start).toHaveBeenCalledWith("挥手");
    expect(wait).not.toHaveBeenCalled();
  });
  it("rejects navigation before creating a job while a preset action is busy", async () => {
    snapshot.actionBusy = true;
    await expect(jobs.startPoint(point)).rejects.toThrow("仍在执行");
    await expect(jobs.startTour(tour)).rejects.toThrow("仍在执行");
    expect(adapter.startNavigation).not.toHaveBeenCalled();
  });
  it("waits for preset motion completion before speaking a tour step", async () => {
    let finishMotion!: () => void;
    vi.spyOn(adapter, "startPresetMotion").mockResolvedValue("motion-10");
    const wait = vi.spyOn(adapter, "waitPresetMotion").mockImplementation(
      () => new Promise<void>((resolve) => { finishMotion = resolve; }),
    );
    await jobs.startTour({
      ...tour,
      steps: [{ ...tour.steps[0], presetMotion: "挥手" }],
    });
    await vi.advanceTimersByTimeAsync(10);
    nav = { status: "finished", taskId: nav.taskId };
    await vi.advanceTimersByTimeAsync(20);
    expect(wait).toHaveBeenCalledWith("motion-10");
    expect(speak).not.toHaveBeenCalled();
    finishMotion();
    await vi.advanceTimersByTimeAsync(20);
    expect(speak).toHaveBeenCalledTimes(1);
  });
  it("limits LinkCraft resources to the last step of a single-pass tour", async () => {
    snapshot.linkcraftResources = [{ id: "resource-1", name: "展厅动作" }];
    await expect(jobs.startTour({
      ...tour,
      repeatCount: 2,
      steps: [{ ...tour.steps[0], linkcraftResourceId: "resource-1" }],
    })).rejects.toThrow("单轮讲解的最后一步");
    expect(adapter.startNavigation).not.toHaveBeenCalled();
  });
  it("ends after LinkCraft service acceptance and does not report playback completion", async () => {
    snapshot.linkcraftResources = [{ id: "resource-1", name: "展厅动作" }];
    const execute = vi.spyOn(adapter, "executeLinkcraftResource");
    await jobs.startTour({
      ...tour,
      steps: [{ ...tour.steps[0], linkcraftResourceId: "resource-1" }],
    });
    await vi.advanceTimersByTimeAsync(20);
    nav = { status: "finished", taskId: nav.taskId };
    await vi.advanceTimersByTimeAsync(100);
    expect(execute).toHaveBeenCalledWith("resource-1");
    expect(jobs.state().detail).toContain("播放完成状态不可见");
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
  });
  it("waits for an in-flight dispatch before issuing stop and ignores its late resolution", async () => {
    let release!: () => void;
    vi.mocked(adapter.startNavigation).mockImplementationOnce(() => new Promise<void>((resolve) => { release = resolve; }));
    const starting = jobs.startPoint(point);
    await vi.advanceTimersByTimeAsync(1);
    const stopping = jobs.control("stop");
    expect(adapter.controlNavigation).not.toHaveBeenCalled();
    release();
    await Promise.all([starting, stopping]);
    expect(adapter.controlNavigation).toHaveBeenCalledWith("stop", expect.any(String));
    expect(jobs.state().status).toBe("idle");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
  });
  it("sends stop after an in-flight resume so a late resume cannot restart navigation", async () => {
    await jobs.startPoint(point);
    await jobs.control("pause");

    const commands: string[] = [];
    let applyResume!: () => void;
    vi.mocked(adapter.controlNavigation).mockImplementation((action) => {
      commands.push(action);
      if (action === "resume") {
        return new Promise<void>((resolve) => {
          applyResume = () => {
            nav = { ...nav, status: "running" };
            resolve();
          };
        });
      }
      nav = { ...nav, status: action === "stop" ? "idle" : "paused" };
      return Promise.resolve();
    });

    const resuming = jobs.control("resume");
    await vi.advanceTimersByTimeAsync(1);
    const stopping = jobs.control("stop");
    await vi.advanceTimersByTimeAsync(1);
    expect(commands).toEqual(["resume"]);
    await expect(jobs.control("resume")).rejects.toThrow("状态已失效");

    applyResume();
    await Promise.all([resuming, stopping]);
    expect(commands).toEqual(["resume", "stop"]);
    expect(nav.status).toBe("idle");
    expect(jobs.state().status).toBe("idle");
  });
  it("does not send a stop after its voice turn becomes stale while dispatch is pending", async () => {
    let release!: () => void;
    let authorized = true;
    vi.mocked(adapter.startNavigation).mockImplementationOnce(() => new Promise<void>((resolve) => { release = resolve; }));
    const starting = jobs.startPoint(point);
    await vi.advanceTimersByTimeAsync(1);
    const stopping = jobs.control("stop", () => authorized);
    authorized = false;
    release();
    await starting;
    await expect(stopping).rejects.toThrow("失效");
    expect(adapter.controlNavigation).not.toHaveBeenCalled();
    expect(jobs.hasActiveJob()).toBe(true);
    await jobs.control("stop");
  });
  it("does not dispatch a delayed pause after its voice turn becomes stale", async () => {
    await jobs.startPoint(point);
    const job = (jobs as unknown as { current: { speechId?: string } }).current;
    job.speechId = "current-speech";
    let finishCancel!: () => void;
    const cancelled = new Promise<void>((resolve) => { finishCancel = resolve; });
    cancel.mockImplementationOnce(() => cancelled);
    let authorized = true;
    const pausing = jobs.control("pause", () => authorized);
    await vi.advanceTimersByTimeAsync(1);
    authorized = false;
    finishCancel();
    await expect(pausing).rejects.toThrow("失效");
    expect(adapter.controlNavigation).not.toHaveBeenCalledWith("pause", expect.any(String));
    expect(jobs.state().status).toBe("running");
  });
  it("does not claim a confirmed pause after a control timeout", async () => {
    await jobs.startPoint(point);
    vi.mocked(adapter.controlNavigation).mockRejectedValueOnce(new Error("timeout"));
    await expect(jobs.control("pause")).rejects.toThrow("timeout");
    expect(jobs.state().status).toBe("failed");
    await expect(jobs.control("resume")).rejects.toThrow("先停止");
  });
  it.each(["point", "tour"] as const)("keeps a halted %s from advancing after a late arrival", async (kind) => {
    store.state.idleReturnSeconds = 30;
    if (kind === "point") await jobs.startPoint(point);
    else await jobs.startTour(tour);
    await vi.advanceTimersByTimeAsync(20);
    const taskId = nav.taskId;
    vi.mocked(adapter.controlNavigation).mockRejectedValueOnce(new Error("pause timeout"));

    await expect(jobs.control("pause")).rejects.toThrow("pause timeout");
    nav = { status: "finished", taskId };
    await vi.advanceTimersByTimeAsync(60_000);

    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
    expect(speak).not.toHaveBeenCalled();
    expect(jobs.state()).toMatchObject({ status: "failed", requiresStop: true });
    expect(jobs.hasActiveJob()).toBe(true);
    await expect(jobs.startPoint(point)).rejects.toThrow("已有任务");
    await jobs.control("stop");
    expect(jobs.state().status).toBe("idle");
  });
  it.each(["point", "tour"] as const)("keeps a halted %s from advancing after an unconfirmed resume", async (kind) => {
    store.state.idleReturnSeconds = 30;
    if (kind === "point") await jobs.startPoint(point);
    else await jobs.startTour(tour);
    await vi.advanceTimersByTimeAsync(20);
    await jobs.control("pause");
    const taskId = nav.taskId;
    vi.mocked(adapter.controlNavigation).mockRejectedValueOnce(new Error("resume timeout"));

    await expect(jobs.control("resume")).rejects.toThrow("resume timeout");
    nav = { status: "finished", taskId };
    await vi.advanceTimersByTimeAsync(60_000);

    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
    expect(speak).not.toHaveBeenCalled();
    expect(jobs.state()).toMatchObject({ status: "failed", requiresStop: true });
    await expect(jobs.control("resume")).rejects.toThrow("先停止");
    await expect(jobs.startTour(tour)).rejects.toThrow("已有任务");
    await jobs.control("stop");
    expect(jobs.state().status).toBe("idle");
  });
  it("still stops native navigation when speech cancellation fails and blocks overlap", async () => {
    await jobs.startPoint(point);
    // A pending narration/confirmation can overlap a native stop.
    const job = (jobs as unknown as { current: { speechId?: string } }).current;
    job.speechId = "pending-speech";
    cancel.mockRejectedValueOnce(new Error("speech stop timeout"));
    await expect(jobs.control("stop")).rejects.toThrow("speech stop timeout");
    expect(adapter.controlNavigation).toHaveBeenCalledWith("stop", expect.any(String));
    expect(jobs.state().status).toBe("failed");
    await expect(jobs.startPoint(point)).rejects.toThrow("已有任务");
    await jobs.control("stop");
    expect(jobs.state().status).toBe("idle");
  });
});
