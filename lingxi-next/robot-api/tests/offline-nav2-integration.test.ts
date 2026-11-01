import { afterEach, expect, it } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import { StdioOfflineNavigationBackend } from "../src/offline-navigation-backend";
import { buildServer } from "../src/server";
import { JsonStateStore } from "../src/store";
import type { OfflineNavigationState } from "../../shared/offline-navigation";

function expectRetiredPoseUnchanged(before: OfflineNavigationState, after: OfflineNavigationState) {
  expect(after.outputQuiescent).toBe(true);
  expect(after.ticket).toEqual(before.ticket);
  expect(after.commandCount).toBe(before.commandCount);
  // The synthetic integrator normalizes yaw even for a zero Twist.
  for (const axis of ["x", "y", "yaw"] as const) expect(after.pose[axis]).toBeCloseTo(before.pose[axis], 12);
}

// Explicit opt-in: WSL rootfs only; never use the robot or replace it with a mock.
const rootfs = process.env.LINGXI_OFFLINE_NAV2_ROOTFS;
let app: FastifyInstance | undefined, directory: string | undefined;
afterEach(async () => { await app?.close(); app = undefined; if (directory) await rm(directory, { recursive: true, force: true }); });

it.skipIf(!rootfs || process.platform !== "win32")("drives real isolated Nav2 from paired Web API and awakened voice, preserving pause epochs", async () => {
  directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-real-nav2-"));
  const backend = new StdioOfflineNavigationBackend("wsl.exe", ["-u", "root", "--", "bash",
    "tools/nav2-offline-service-isolated.sh", rootfs!, "open", `/api-nav2-${Date.now()}`], process.cwd());
  app = await buildServer({ offlineNavigationBackend: backend, stateDirectory: directory,
    staticDirectory: path.join(directory, "none"), internalAgentToken: "offline-integration-only" });
  const paired = await app.inject({ method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "lingxi-dev-only" } });
  expect(paired.statusCode).toBe(201);
  const headers = { authorization: `Bearer ${paired.json().token}` };
  const state = (await app.inject({ url: "/api/v1/state", headers })).json();
  expect(state.navigationExecution).toMatchObject({ syntheticInputs: true, physicalMotionSent: false, navigationReady: false });
  const point = { id: "target", mapId: state.activeMapId, name: "接待台", x: .9, y: 0, yaw: 0, isStart: false };
  expect(point.mapId).toMatch(/^slam-/);
  const store = new JsonStateStore(directory);
  await store.update((db) => ({ ...db, points: [point], idleReturnSeconds: 0 }));
  const control = (action: string) => app!.inject({ method: "POST", url: "/api/v1/navigation/control", headers, payload: { action } });
  const agent = (event: object) => app!.inject({ method: "POST", url: "/api/v1/agent/events",
    headers: { "x-agent-token": "offline-integration-only" }, payload: event });
  const waitState = async (condition: (state: Awaited<ReturnType<typeof backend.state>>) => boolean, seconds = 35) => {
    const end = Date.now() + seconds * 1000;
    while (Date.now() < end) {
      const value = await backend.state();
      if (condition(value)) return value;
      if (value.requiresStop) throw new Error(`runtime fault: ${value.detail}`);
      await new Promise((resolve) => setTimeout(resolve, 120));
    }
    throw new Error("bounded real Nav2 condition timeout");
  };
  const started = await app.inject({ method: "POST", url: "/api/v1/navigation/point", headers, payload: { pointId: point.id } });
  expect(started.statusCode, started.body).toBe(202);
  const running = await waitState((value) => value.phase === "running" && value.commandCount > 3 && value.pose.x > .025);
  await agent({ kind: "wake", sessionId: "voice", eventId: "wake1", occurredAt: Date.now() });
  const question = await agent({ kind: "recognition", sessionId: "voice", eventId: "question1", occurredAt: Date.now(),
    isFinal: true, confidence: .98, transcript: "请问今天展馆几点关门？" });
  expect(question.json()).toMatchObject({ category: "rejected", accepted: false,
    text: "我暂时无法从已配置知识库或实时状态中核实这项信息。" });
  expect((await backend.state()).ticket?.epoch).toBe(running.ticket!.epoch);
  expect((await control("pause")).statusCode).toBe(200);
  const paused = await backend.state();
  expect(paused.phase).toBe("paused");
  expect(paused.outputQuiescent).toBe(true);
  await new Promise((resolve) => setTimeout(resolve, 400));
  expectRetiredPoseUnchanged(paused, await backend.state());
  expect((await control("resume")).statusCode).toBe(200);
  const resumed = await backend.state();
  expect(resumed.ticket?.epoch).not.toBe(paused.ticket?.epoch);
  const finished = await waitState((value) => value.phase === "finished");
  expect(finished.pose.x).toBeGreaterThan(.74);
  expect(finished.pose.x).toBeLessThan(1.06);
  expect(finished.commandCount).toBeGreaterThan(20);
  expect(finished.outputQuiescent).toBe(true);
  await new Promise((resolve) => setTimeout(resolve, 400));
  const voiceStart = await agent({ kind: "recognition", sessionId: "voice", eventId: "voice-start", occurredAt: Date.now(),
    isFinal: true, confidence: .98, transcript: "去接待台" });
  expect(voiceStart.json().accepted, voiceStart.body).toBe(true);
  expect((await backend.state()).ticket?.epoch).not.toBe(finished.ticket?.epoch);
  expect((await control("stop")).statusCode).toBe(200);
  expect((await backend.state()).phase).toBe("idle");

  const second = { ...point, id: "second", name: "二号展台", x: 1.25 };
  const third = { ...point, id: "third", name: "三号展台", x: 1.6 };
  const tour = { id: "api-tour", mapId: point.mapId, name: "离线多点讲解", repeatCount: 1,
    steps: [second, third].map((value) => ({ pointId: value.id, waitSeconds: 0, narration: `到达${value.name}。` })),
    updatedAt: new Date().toISOString() };
  await store.update((db) => ({ ...db, points: [{ ...point, isStart: true }, second, third], tours: [tour], idleReturnSeconds: 1 }));
  const previousEpoch = (await backend.state()).ticket?.epoch;
  const tourStarted = await app.inject({ method: "POST", url: `/api/v1/tours/${tour.id}/start`, headers });
  expect(tourStarted.statusCode, tourStarted.body).toBe(202);
  const visited: string[] = [];
  const epochs = new Set<string>();
  const touring = await waitState((value) => value.pointId === second.id && value.phase === "running" &&
    value.ticket?.epoch !== previousEpoch);
  visited.push(second.id);
  epochs.add(touring.ticket!.epoch);
  expect((await agent({ kind: "wake", sessionId: "tour-voice", eventId: "tour-wake", occurredAt: Date.now() })).statusCode).toBe(204);
  const tourQuestion = await agent({ kind: "recognition", sessionId: "tour-voice", eventId: "tour-question", occurredAt: Date.now(),
    isFinal: true, confidence: .98, transcript: "请问今天展馆几点关门？" });
  expect(tourQuestion.json()).toMatchObject({ accepted: false, category: "rejected" });
  expect((await backend.state()).ticket?.epoch).toBe(touring.ticket!.epoch);
  const voicePause = await agent({ kind: "recognition", sessionId: "tour-voice", eventId: "tour-pause", occurredAt: Date.now(),
    isFinal: true, confidence: .98, transcript: "暂停讲解" });
  expect(voicePause.json().accepted, voicePause.body).toBe(true);
  const tourPaused = await backend.state();
  expect(tourPaused.phase).toBe("paused");
  expect(tourPaused.outputQuiescent).toBe(true);
  await new Promise((resolve) => setTimeout(resolve, 5300));
  const stillPaused = await backend.state();
  expectRetiredPoseUnchanged(tourPaused, stillPaused);
  expect(stillPaused.phase).toBe("paused");
  const voiceResume = await agent({ kind: "recognition", sessionId: "tour-voice", eventId: "tour-resume", occurredAt: Date.now(),
    isFinal: true, confidence: .98, transcript: "继续讲解" });
  expect(voiceResume.json().accepted, voiceResume.body).toBe(true);
  expect((await backend.state()).ticket?.epoch).not.toBe(tourPaused.ticket!.epoch);
  const tourFinished = await waitState((value) => {
    // A requested tour dispatches asynchronously; the retired prior task is
    // not a visit in this tour, even if its last point remains observable.
    if (value.ticket && value.ticket.epoch !== previousEpoch) {
      if (value.pointId && visited.at(-1) !== value.pointId) visited.push(value.pointId);
      epochs.add(value.ticket.epoch);
    }
    return value.pointId === third.id && value.phase === "finished";
  });
  expect(visited).toEqual([second.id, third.id]);
  expect(epochs.size).toBe(3);
  await new Promise((resolve) => setTimeout(resolve, 1600));
  expect((await backend.state()).ticket).toEqual(tourFinished.ticket);
  const finalState = (await app.inject({ url: "/api/v1/state", headers })).json();
  expect(finalState.navigation).toMatchObject({ status: "finished", detail: "讲解已完成" });
  console.info(JSON.stringify({ purpose: "real_nav2_product_api_integration_not_field_acceptance", passed: true,
    commandCount: finished.commandCount, finalPose: finished.pose, pauseRetired: paused.outputQuiescent,
    resumeChangedEpoch: true, voiceDispatchConfirmed: true, tourVisited: visited, tourEpochs: epochs.size,
    tourQuestionPreservedNavigation: true, tourVoicePauseRetired: tourPaused.outputQuiescent,
    noManualAutoResumeAfter5300ms: true, tourVoiceResumeChangedEpoch: true,
    noTourAutoReturn: true, physicalMotionSent: false, navigationReady: false }));
}, 120_000);

it.skipIf(!rootfs || process.platform !== "win32")("completes a product API detour with real Nav2 controller bounds intact", async () => {
  directory = await mkdtemp(path.join(os.tmpdir(), "lingxi-real-detour-"));
  const output = `/api-detour-${Date.now()}`;
  const backend = new StdioOfflineNavigationBackend("wsl.exe", ["-u", "root", "--", "bash",
    "tools/nav2-offline-service-isolated.sh", rootfs!, "detour", output], process.cwd());
  app = await buildServer({ offlineNavigationBackend: backend, stateDirectory: directory,
    staticDirectory: path.join(directory, "none"), internalAgentToken: "" });
  const paired = await app.inject({ method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "lingxi-dev-only" } });
  const headers = { authorization: `Bearer ${paired.json().token}` };
  const state = (await app.inject({ url: "/api/v1/state", headers })).json();
  const point = { id: "detour", mapId: state.activeMapId, name: "绕障终点", x: 2, y: 0, yaw: 0, isStart: false };
  await new JsonStateStore(directory).update((db) => ({ ...db, points: [point], idleReturnSeconds: 0 }));
  expect((await app.inject({ method: "POST", url: "/api/v1/navigation/point", headers, payload: { pointId: point.id } })).statusCode).toBe(202);
  let latest = await backend.state(), maximumLateral = 0;
  const end = Date.now() + 65000;
  while (Date.now() < end && !["finished", "failed"].includes(latest.phase)) {
    // Match the Web workspace's concurrent point-cloud and state polling.
    await Promise.all([backend.scene(), backend.state(), backend.state()]);
    await new Promise((resolve) => setTimeout(resolve, 100));
    latest = await backend.state();
    maximumLateral = Math.max(maximumLateral, Math.abs(latest.pose.y));
  }
  expect(latest.requiresStop, `${latest.detail}; diagnostics: ${output}`).toBe(false);
  expect(latest.phase, latest.detail).toBe("finished");
  expect(latest.outputQuiescent).toBe(true);
  expect(Math.hypot(latest.pose.x - 2, latest.pose.y)).toBeLessThanOrEqual(.16);
  expect(Math.abs(latest.pose.yaw)).toBeLessThanOrEqual(.16);
  expect(maximumLateral).toBeGreaterThanOrEqual(.7);
  console.info(JSON.stringify({ purpose: "real_nav2_product_detour_not_field_acceptance", passed: true,
    commandCount: latest.commandCount, finalPose: latest.pose, maximumLateral, physicalMotionSent: false, navigationReady: false }));
}, 90_000);
