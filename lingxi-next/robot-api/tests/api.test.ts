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
  it.each(["point", "tour"] as const)("keeps a faulted %s blocked at the API until an explicit stop", async (kind) => {
    let lateArrival = false;
    const originalSnapshot = adapter.getSnapshot.bind(adapter);
    vi.spyOn(adapter, "getSnapshot").mockImplementation(async () => {
      const snapshot = await originalSnapshot();
      return lateArrival
        ? { ...snapshot, navigation: { ...snapshot.navigation, status: "finished" } }
        : snapshot;
    });
    const navigate = vi.spyOn(adapter, "startNavigation");
    const starting = await app.inject(kind === "point"
      ? { method: "POST", url: "/api/v1/navigation/point", headers: headers(), payload: { pointId: "point-2" } }
      : { method: "POST", url: "/api/v1/tours/tour-1/start", headers: headers() });
    expect(starting.statusCode).toBe(202);
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledTimes(1));

    vi.spyOn(adapter, "controlNavigation").mockRejectedValueOnce(new Error("pause confirmation timeout"));
    expect((await app.inject({
      method: "POST", url: "/api/v1/navigation/control", headers: headers(), payload: { action: "pause" },
    })).statusCode).toBe(500);
    lateArrival = true;
    // Allow the already running navigation poll to observe the late result.
    await new Promise((resolve) => setTimeout(resolve, 600));
    const state = (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(state.navigation).toMatchObject({ status: "failed", requiresStop: true });
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(speak).not.toHaveBeenCalled();
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers: headers() })).statusCode).toBe(409);
    expect((await app.inject({
      method: "POST", url: "/api/v1/maps/demo-lab/activate", headers: headers(), payload: {},
    })).statusCode).toBe(409);

    lateArrival = false;
    expect((await app.inject({
      method: "POST", url: "/api/v1/navigation/control", headers: headers(), payload: { action: "stop" },
    })).statusCode).toBe(200);
    expect((await app.inject({ url: "/api/v1/state", headers: headers() })).json().navigation.status).toBe("idle");
  });
  it("requires an explicit operator confirmation before clearing action interlock", async () => {
    const rejected = await app.inject({
      method: "POST", url: "/api/v1/actions/confirm-idle", headers: headers(),
      payload: { confirmed: false },
    });
    expect(rejected.statusCode).toBe(400);
    const confirmed = await app.inject({
      method: "POST", url: "/api/v1/actions/confirm-idle", headers: headers(),
      payload: { confirmed: true },
    });
    expect(confirmed.statusCode).toBe(200);
    expect(confirmed.json()).toEqual({ confirmed: true, actionBusy: false });
  });
  it("blocks point navigation and mapping while the adapter reports an uncertain action", async () => {
    const original = adapter.getSnapshot.bind(adapter);
    const navigate = vi.spyOn(adapter, "startNavigation");
    vi.spyOn(adapter, "getSnapshot").mockImplementation(async () => ({
      ...await original(), actionBusy: true, actionStatus: "uncertain",
    }));
    expect((await app.inject({
      method: "POST", url: "/api/v1/navigation/point", headers: headers(),
      payload: { pointId: "point-2" },
    })).statusCode).toBe(409);
    expect((await app.inject({
      method: "POST", url: "/api/v1/mapping/start", headers: headers(),
    })).statusCode).toBe(409);
    expect(navigate).not.toHaveBeenCalled();
  });
  it("keeps exactly one active map after switching", async () => {
    expect((await app.inject({ method: "POST", url: "/api/v1/maps/demo-lab/activate",
      headers: headers(), payload: {} })).statusCode).toBe(200);
    const state = (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(state.maps.filter((map: { active: boolean }) => map.active).map((map: { id: string }) => map.id)).toEqual(["demo-lab"]);
  });
  it("passes explicit initial world pose through and rejects invalid bounds", async () => {
    const activate = vi.spyOn(adapter, "activateMap");
    const pose = { x: 1.25, y: -2, yaw: Math.PI / 2 };
    expect((await app.inject({ method: "POST", url: "/api/v1/maps/demo-hall/activate",
      headers: headers(), payload: { initialPose: pose } })).statusCode).toBe(200);
    expect(activate).toHaveBeenCalledWith(expect.objectContaining({ id: "demo-hall" }), pose);
    expect((await app.inject({ method: "POST", url: "/api/v1/maps/demo-hall/activate",
      headers: headers(), payload: { initialPose: { ...pose, x: 1001 } } })).statusCode).toBe(400);
    expect(activate).toHaveBeenCalledTimes(1);
  });
  it("does not require localization output to start the first mapping session", async () => {
    const snapshot = await adapter.getSnapshot();
    snapshot.checks = snapshot.checks.map((check) => check.id === "odom" ? { ...check, ready: false } : check);
    snapshot.pose = undefined;
    vi.spyOn(adapter, "getSnapshot").mockResolvedValue(snapshot);
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers: headers() })).statusCode).toBe(202);
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
  it("publishes bounded, transcript-free voice latency diagnostics to paired state", async () => {
    await app.inject(agentEvent({ kind: "wake", eventId: "wake" }));
    const transcript = "去灵犀展台";
    const result = await app.inject(command("diagnostic", transcript));
    expect(result.statusCode).toBe(200);

    const state = (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(state.voiceDiagnostics).toMatchObject({
      sampleCount: 1,
      audibleSampleCount: 1,
      latest: { category: "command", accepted: true, speechDelivered: true },
    });
    expect(state.voiceDiagnostics.recognitionToFirstAudioP95Ms).toBeGreaterThanOrEqual(10);
    expect(JSON.stringify(state.voiceDiagnostics)).not.toContain(transcript);
    expect(JSON.stringify(state.voiceDiagnostics)).not.toContain("diagnostic");
    const persistedState = await readFile(path.join(directory, "state.json"), "utf8");
    expect(persistedState).not.toContain(transcript);
    expect(persistedState).not.toContain("diagnostic");
  });
  it("measures VAD-end to correlated playback only when a valid EOS timestamp is present", async () => {
    await app.inject(agentEvent({ kind: "wake", eventId: "wake-vad" }));
    const request = command("vad-measured");
    const result = await app.inject({
      ...request,
      payload: { ...request.payload as object, vadEndedAt: Date.now() - 500 },
    });
    expect(result.statusCode).toBe(200);
    expect(result.json().vadToFirstAudioMs).toBeGreaterThanOrEqual(500);

    const nextWake = agentEvent({ kind: "wake", sessionId: "voice-without-vad", eventId: "wake-without-vad" });
    await app.inject(nextWake);
    const withoutVad = await app.inject(command("no-vad", "去入口", "voice-without-vad"));
    expect(withoutVad.statusCode).toBe(200);
    expect(withoutVad.json().vadToFirstAudioMs).toBeUndefined();

    const diagnostics = (await app.inject({ url: "/api/v1/state", headers: headers() })).json().voiceDiagnostics;
    expect(diagnostics.vadMeasuredSampleCount).toBe(1);
    expect(diagnostics.vadToFirstAudioP95Ms).toBeGreaterThanOrEqual(500);
    expect(diagnostics.latest.vadToFirstAudioMs).toBeUndefined();
  });
  it("keeps voice diagnostics bounded to the latest 100 completed replies", async () => {
    for (let index = 0; index < 101; index += 1) {
      const sessionId = `diagnostic-session-${index}`;
      const event = (payload: object) => app.inject({
        method: "POST",
        url: "/api/v1/agent/events",
        headers: { "x-agent-token": "internal-test-token" },
        payload: { sessionId, occurredAt: Date.now(), ...payload },
      });
      await event({ kind: "wake", eventId: `wake-${index}` });
      const result = await event({
        kind: "recognition",
        eventId: `reply-${index}`,
        transcript: "去不存在地点",
        confidence: 0.99,
        isFinal: true,
      });
      expect(result.statusCode).toBe(200);
    }

    const diagnostics = (await app.inject({ url: "/api/v1/state", headers: headers() })).json().voiceDiagnostics;
    expect(diagnostics.sampleCount).toBe(100);
    expect(diagnostics.audibleSampleCount).toBe(100);
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
  const agentEvent = (payload: object) => ({
    method: "POST" as const, url: "/api/v1/agent/events",
    headers: { "x-agent-token": "internal-test-token" },
    payload: { sessionId: "voice", occurredAt: Date.now(), ...payload },
  });
  const command = (eventId: string, transcript = "去灵犀展台", sessionId = "voice") =>
    agentEvent({ kind: "recognition", eventId, transcript, sessionId, confidence: 0.99, isFinal: true });

  it("processes session loss without waiting for a blocked robot snapshot", async () => {
    await app.inject(agentEvent({ kind: "wake", eventId: "wake" }));
    const navigate = vi.spyOn(adapter, "startNavigation");
    const snapshot = await adapter.getSnapshot();
    let release!: () => void;
    const entered = new Promise<void>((resolve) => {
      vi.spyOn(adapter, "getSnapshot").mockImplementationOnce(() => new Promise((finish) => {
        release = () => finish(snapshot);
        resolve();
      }));
    });
    const pending = app.inject(command("late-command"));
    await entered;
    // This must finish while the older request's snapshot is still blocked.
    const ended = await app.inject(agentEvent({ kind: "session-ended", eventId: "end" }));
    release();
    expect(ended.statusCode).toBe(204);
    expect((await pending).statusCode).toBe(204);
    expect(navigate).not.toHaveBeenCalled();
    expect(speak).not.toHaveBeenCalled();
  });
  it("admits wake and revokes the prior turn before a blocked snapshot", async () => {
    await app.inject(agentEvent({ kind: "wake", eventId: "wake-one" }));
    const snapshot = await adapter.getSnapshot();
    let release!: () => void;
    const entered = new Promise<void>((resolve) => {
      vi.spyOn(adapter, "getSnapshot").mockImplementationOnce(() => new Promise((finish) => {
        release = () => finish(snapshot);
        resolve();
      }));
    });
    const old = app.inject(command("old"));
    await entered;
    expect((await app.inject(agentEvent({ kind: "wake", sessionId: "voice-next", eventId: "wake-two" }))).statusCode).toBe(204);
    release();
    expect((await old).statusCode).toBe(204);
    expect(speak).not.toHaveBeenCalled();
  });
  it("rechecks the session inside navigation readiness before native dispatch", async () => {
    await app.inject(agentEvent({ kind: "wake", eventId: "wake" }));
    const navigate = vi.spyOn(adapter, "startNavigation");
    const original = adapter.getSnapshot.bind(adapter);
    let calls = 0;
    let release!: () => void;
    const entered = new Promise<void>((resolve) => {
      vi.spyOn(adapter, "getSnapshot").mockImplementation(async () => {
        const snapshot = await original();
        if (++calls === 2) return new Promise((finish) => {
          release = () => finish(snapshot);
          resolve();
        });
        return snapshot;
      });
    });
    const pending = app.inject(command("readiness-race"));
    await entered;
    await app.inject(agentEvent({ kind: "session-ended", eventId: "end" }));
    release();
    expect((await pending).statusCode).toBe(204);
    expect(navigate).not.toHaveBeenCalled();
  });
  it("never dispatches the middle recognition after a slow earlier cancellation", async () => {
    await app.inject(agentEvent({ kind: "wake", eventId: "wake" }));
    const navigate = vi.spyOn(adapter, "startNavigation");
    let finishSpeech!: (value: { firstAudioMs: number }) => void;
    const speaking = new Promise<void>((resolve) => {
      speak.mockImplementationOnce(() => new Promise((finish) => { finishSpeech = finish; resolve(); }));
    });
    const first = app.inject(command("first", "去不存在地点"));
    await speaking;
    let finishCancel!: () => void;
    const cancelling = new Promise<void>((resolve) => {
      cancel.mockImplementationOnce(() => new Promise<void>((finish) => { finishCancel = finish; resolve(); }));
    });
    const middle = app.inject(command("middle"));
    await cancelling;
    const last = app.inject(command("last", "去另一个不存在地点"));
    finishCancel();
    finishSpeech({ firstAudioMs: 10 });
    const results = await Promise.all([first, middle, last]);
    expect(results.map((result) => result.statusCode)).toEqual([204, 204, 200]);
    expect(navigate).not.toHaveBeenCalled();
    expect(speak.mock.calls.map((call) => call[1])).not.toContain("reply:voice:middle");
  });
  it("does not dispatch navigation when an earlier playback stop is unconfirmed", async () => {
    await app.inject(agentEvent({ kind: "wake", eventId: "wake" }));
    let finishSpeech!: (value: { firstAudioMs: number }) => void;
    const speaking = new Promise<void>((resolve) => {
      speak.mockImplementationOnce(() => new Promise((finish) => { finishSpeech = finish; resolve(); }));
    });
    const first = app.inject(command("first", "去不存在地点"));
    await speaking;
    let finishCancel!: (error?: Error) => void;
    const cancelling = new Promise<void>((resolve) => {
      cancel.mockImplementationOnce(() => new Promise<void>((finish, reject) => {
        finishCancel = (error) => { if (error) reject(error); else finish(); };
        resolve();
      }));
    });
    const wake = app.inject(agentEvent({ kind: "wake", sessionId: "voice-next", eventId: "wake-next" }));
    await cancelling;
    finishCancel(new Error("speaker stop not confirmed"));
    expect((await wake).statusCode).toBe(500);
    const navigate = vi.spyOn(adapter, "startNavigation");
    const next = await app.inject(command("next", "去灵犀展台", "voice-next"));
    finishSpeech({ firstAudioMs: 10 });
    await first;
    expect(next.json()).toMatchObject({ accepted: false, category: "rejected" });
    expect(navigate).not.toHaveBeenCalled();
  });
  it("cancels a model stream on session end without emitting its late output", async () => {
    const { Answerer } = await import("../src/voice/answerer");
    let signal!: AbortSignal;
    let finish!: () => void;
    const entered = new Promise<void>((resolve) => {
      vi.spyOn(Answerer.prototype, "streamAnswer").mockImplementationOnce(async (_text, emit, cancellation) => {
        signal = cancellation!;
        await new Promise<void>((done) => { finish = done; resolve(); });
        emit("这是已经取消的旧回答。");
        return { text: "这是已经取消的旧回答。", source: "model" };
      });
    });
    try {
      await app.inject(agentEvent({ kind: "wake", eventId: "wake" }));
      const answering = app.inject(command("question", "什么是激光雷达"));
      await entered;
      await app.inject(agentEvent({ kind: "session-ended", eventId: "end" }));
      expect(signal.aborted).toBe(true);
      finish();
      expect((await answering).statusCode).toBe(204);
      expect(speak).not.toHaveBeenCalled();
    } finally { vi.restoreAllMocks(); }
  });
});
