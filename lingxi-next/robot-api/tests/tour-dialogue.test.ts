import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import type { RobotSnapshot } from "../src/domain";
import { DemoAdapter } from "../src/adapters/demo";
import { buildServer } from "../src/server";
import { JsonStateStore } from "../src/store";

function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("paired tour and passive voice integration (no robot or cloud)", () => {
  let app: FastifyInstance, directory: string, token: string;
  let adapter: DemoAdapter, nav: RobotSnapshot["navigation"];
  const speak = vi.fn(async (_text: string, _id: string) => ({ firstAudioMs: 10 }));
  const cancel = vi.fn(async (_id: string) => undefined);
  const request = (url: string, payload?: object) => app.inject({
    method: "POST", url, headers: { authorization: `Bearer ${token}` }, payload,
  });
  const control = (action: string) => request("/api/v1/navigation/control", { action });
  const state = async () => (await app.inject({ url: "/api/v1/state", headers: { authorization: `Bearer ${token}` } })).json();
  const event = (kind: string, eventId: string, extra: object = {}) => app.inject({
    method: "POST", url: "/api/v1/agent/events", headers: { "x-agent-token": "tour-offline-only" },
    payload: { kind, eventId, sessionId: "tour-session", occurredAt: Date.now(), ...extra },
  });
  const question = (eventId: string, sessionId = "tour-session") => event("recognition", eventId, {
    transcript: "请问展馆几点关门？", confidence: .99, isFinal: true, sessionId,
  });

  beforeEach(async () => {
    directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-tour-dialogue-"));
    adapter = new DemoAdapter();
    const snapshot = await adapter.getSnapshot();
    nav = { status: "idle" };
    vi.spyOn(adapter, "getSnapshot").mockImplementation(async () => ({ ...snapshot, navigation: nav }));
    vi.spyOn(adapter, "startNavigation").mockImplementation(async (_point, _map, taskId) => { nav = { status: "running", taskId }; });
    vi.spyOn(adapter, "controlNavigation").mockImplementation(async (action) => {
      nav = { ...nav, status: action === "stop" ? "idle" : action === "pause" ? "paused" : "running" };
    });
    speak.mockReset().mockResolvedValue({ firstAudioMs: 10 });
    cancel.mockReset().mockResolvedValue(undefined);
    app = await buildServer({ demoMode: true, adapter, agentSpeaker: { speak, cancel },
      stateDirectory: directory, staticDirectory: path.join(directory, "none"), internalAgentToken: "tour-offline-only" });
    const paired = await request("/api/v1/session/pair", { pairingCode: "lingxi-dev-only" });
    expect(paired.statusCode).toBe(201);
    token = paired.json().token;
    await new JsonStateStore(directory).update((db) => ({ ...db, idleReturnSeconds: 1,
      tours: db.tours.map((tour) => ({ ...tour, steps: tour.steps.map((step) => ({
        pointId: step.pointId, narration: step.narration, waitSeconds: 0,
      })) })),
    }));
    // Leave Fastify's nextTick/setImmediate and filesystem I/O real.
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
  });
  afterEach(async () => {
    await app?.close();
    vi.clearAllTimers();
    vi.useRealTimers();
    await rm(directory, { recursive: true, force: true });
  });

  async function startNarration() {
    const playing = deferred<{ firstAudioMs: number }>();
    const entered = deferred<void>();
    speak.mockImplementationOnce(() => { entered.resolve(); return playing.promise; });
    expect((await request("/api/v1/tours/tour-1/start")).statusCode).toBe(202);
    const navigationId = nav.taskId;
    nav = { status: "finished", taskId: navigationId };
    await vi.advanceTimersByTimeAsync(300);
    await entered.promise;
    return { playing, navigationId, speechId: speak.mock.calls[0][1] };
  }

  it("waits for correlated stop before Q&A and replays only after five quiet seconds", async () => {
    const { playing, speechId } = await startNarration();
    const stopping = deferred<void>(), cancelEntered = deferred<void>();
    cancel.mockImplementationOnce(() => { cancelEntered.resolve(); return stopping.promise; });
    const wake = event("wake", "wake-1");
    await cancelEntered.promise;
    expect(cancel).toHaveBeenCalledWith(speechId);
    const answering = question("question-1").then((reply) => reply);
    await new Promise<void>((resolve) => setImmediate(resolve));
    playing.resolve({ firstAudioMs: 10 });
    await vi.advanceTimersByTimeAsync(6000);
    expect(speak).toHaveBeenCalledTimes(1);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
    stopping.resolve();
    expect((await wake).statusCode).toBe(204);
    expect((await answering).json()).toMatchObject({ accepted: false, category: "rejected", speechDelivered: true });
    expect(speak.mock.calls[1][1]).toBe("reply:tour-session:question-1");
    await vi.advanceTimersByTimeAsync(4990);
    expect(speak).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(510);
    expect(speak.mock.calls[2][0]).toBe(speak.mock.calls[0][0]);
    expect(speak.mock.calls[2][1]).not.toBe(speechId);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(2);
    expect((await state()).navigation.pointId).toBe("point-3");
    nav = { status: "finished", taskId: nav.taskId };
    await vi.advanceTimersByTimeAsync(6000);
    expect((await state()).navigation).toMatchObject({ status: "finished", detail: "讲解已完成" });
    expect(adapter.startNavigation).toHaveBeenCalledTimes(2);
  });

  it("does not auto-resume a manual pause or accept a late rejected narration as a new failure", async () => {
    const { playing, speechId } = await startNarration();
    expect((await control("pause")).statusCode).toBe(200);
    expect(cancel).toHaveBeenCalledWith(speechId);
    await event("wake", "wake-paused");
    await question("question-paused");
    await vi.advanceTimersByTimeAsync(30_000);
    expect((await state()).navigation.status).toBe("paused");
    expect(speak).toHaveBeenCalledTimes(2);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
    expect((await control("resume")).statusCode).toBe(200);
    await vi.advanceTimersByTimeAsync(510);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(2);
    playing.reject(new Error("late old AgentSDK completion failure"));
    await vi.advanceTimersByTimeAsync(6000);
    expect((await state()).navigation).toMatchObject({ pointId: "point-3", requiresStop: false });
    expect(speak).toHaveBeenCalledTimes(3);
  });

  it("keeps a failed wake stop visible and retries the same playback on explicit stop", async () => {
    const { playing, speechId } = await startNarration();
    cancel.mockRejectedValueOnce(new Error("unconfirmed AgentSDK stop"));
    expect((await event("wake", "wake-failed")).statusCode).toBe(500);
    expect((await question("blocked-question")).json()).toMatchObject({ accepted: false, speechDelivered: false });
    playing.resolve({ firstAudioMs: 10 });
    await vi.advanceTimersByTimeAsync(30_000);
    expect((await state()).navigation).toMatchObject({ status: "failed", requiresStop: true });
    expect(speak).toHaveBeenCalledTimes(1);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(1);
    expect((await control("resume")).statusCode).toBe(500);
    expect((await control("stop")).statusCode).toBe(200);
    expect(cancel.mock.calls.map(([id]) => id)).toEqual([speechId, speechId]);
    expect((await state()).navigation.status).toBe("idle");
    await event("wake", "wake-recovered", { sessionId: "recovered-session" });
    expect((await question("question-recovered", "recovered-session")).json()).toMatchObject({ speechDelivered: true });
    expect((await request("/api/v1/tours/tour-1/start")).statusCode).toBe(202);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(2);
  });

  it("keeps point navigation idle-return suspended through actual passive-event answer playback", async () => {
    expect((await request("/api/v1/navigation/point", { pointId: "point-2" })).statusCode).toBe(202);
    nav = { status: "finished", taskId: nav.taskId };
    await vi.advanceTimersByTimeAsync(300);
    const playing = deferred<{ firstAudioMs: number }>(), entered = deferred<void>();
    speak.mockImplementationOnce(() => { entered.resolve(); return playing.promise; });
    await event("wake", "point-wake");
    const answering = question("point-long-answer").then((reply) => reply);
    await entered.promise;
    await vi.advanceTimersByTimeAsync(5000);
    expect(adapter.startNavigation).toHaveBeenCalledOnce();
    expect(adapter.controlNavigation).not.toHaveBeenCalled();
    playing.resolve({ firstAudioMs: 10 });
    expect((await answering).json()).toMatchObject({ speechDelivered: true });
    await vi.advanceTimersByTimeAsync(900);
    expect(adapter.startNavigation).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(400);
    expect(adapter.startNavigation).toHaveBeenCalledTimes(2);
    expect(vi.mocked(adapter.startNavigation).mock.calls[1][0].id).toBe("point-1");
  });
});
