import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import https from "node:https";
import { tmpdir } from "node:os";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import { DemoAdapter } from "../src/adapters/demo";
import { ReferenceNavigationAdapter } from "../src/reference-navigation";
import type { MappingBackend, MappingBackendState } from "../src/mapping-backend";
import type { LocalizationBackend } from "../src/localization-backend";
import { identityMatrix, localizationFixture, localizationRuntimeFixture } from "./fixtures/localization-state";
import { savedSlamFixture } from "./fixtures/saved-slam-map";
import type { PhysicalNavigationBackend } from "../src/physical-navigation-backend";
import type { PhysicalNavigationState } from "../../shared/physical-navigation";
import type { ActionsBackend, ActionsState } from "../src/actions-backend";

async function tlsMaterial(root: string) {
  const keyPath = path.join(root, "test.key"), certPath = path.join(root, "test.crt");
  const windowsGitOpenSsl = path.join(process.env.ProgramFiles ?? "C:/Program Files", "Git/usr/bin/openssl.exe");
  const candidates = process.platform === "win32" ? ["openssl", ...(existsSync(windowsGitOpenSsl) ? [windowsGitOpenSsl] : [])]
    : ["openssl"];
  for (const executable of candidates) {
    try {
      execFileSync(executable, ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", keyPath,
        "-out", certPath, "-days", "1", "-subj", "/CN=localhost"], { stdio: "ignore" });
      return { key: await readFile(keyPath), cert: await readFile(certPath) };
    } catch { /* Try the next local OpenSSL installation. */ }
  }
  throw new Error("local_openssl_required_for_production_https_test");
}

function secureRequest(origin: string, method: string, route: string, payload?: object, token?: string) {
  return new Promise<{ statusCode: number; body: string; json: () => unknown }>((resolve, reject) => {
    const body = payload === undefined ? undefined : JSON.stringify(payload);
    const request = https.request(new URL(route, origin), {
      method, rejectUnauthorized: false,
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } : {}) },
    }, (response) => {
      let text = "";
      response.setEncoding("utf8");
      response.on("data", (part: string) => { text += part; });
      response.on("end", () => resolve({ statusCode: response.statusCode ?? 0, body: text,
        json: () => JSON.parse(text) as unknown }));
    });
    request.on("error", reject);
    if (body) request.write(body);
    request.end();
  });
}

it("correlates reference map saves on the paired HTTPS API and rejects actuation", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "lingxi-reference-api-"));
  const session = "a".repeat(32);
  const map = { id: `fastlio-${session}`, name: "展厅", active: false, resolution: 0.05, origin: [0, 0, 0] };
  const save = vi.fn(async () => map);
  const actuate = vi.fn();
  const reference = Object.assign(Object.create(ReferenceNavigationAdapter.prototype), {
    demoMode: false,
    getSnapshot: async () => ({ connected: true, mapping: true, mappingBackend: "fastlio", mappingSession: session,
      actionBusy: false, navigation: { status: "idle" }, checks: [], motionPresets: [], expressions: [], linkcraftResources: [] }),
    listMaps: async () => [], stopAndSaveMap: save, startNavigation: actuate,
  }) as ReferenceNavigationAdapter;
  let server: FastifyInstance | undefined;
  try {
    const tls = await tlsMaterial(root);
    server = await buildServer({ referenceNavigation: reference, stateDirectory: path.join(root, "state"),
      pairingCodeHash: pairingCodeHash("synthetic-reference"), tlsKey: tls.key, tlsCert: tls.cert });
    const origin = await server.listen({ host: "127.0.0.1", port: 0 });
    const pairing = await secureRequest(origin, "POST", "/api/v1/session/pair", { pairingCode: "synthetic-reference" });
    expect(pairing.statusCode).toBe(201);
    const { token } = pairing.json() as { token: string };
    expect((await secureRequest(origin, "POST", "/api/v1/mapping/stop-save", { name: map.name }, token)).statusCode).toBe(400);
    expect(save).not.toHaveBeenCalled();
    expect((await secureRequest(origin, "POST", "/api/v1/mapping/stop-save", { name: map.name, session }, token)).json())
      .toEqual({ confirmed: true, map });
    expect(save).toHaveBeenCalledExactlyOnceWith(map.name, session);
    expect((await secureRequest(origin, "POST", "/api/v1/navigation/point", { pointId: "target" }, token)).statusCode).toBe(409);
    expect((await secureRequest(origin, "POST", "/api/v1/tours/tour/start", {}, token)).statusCode).toBe(409);
    expect(actuate).not.toHaveBeenCalled();
  } finally {
    await server?.close();
    await rm(root, { recursive: true, force: true });
  }
});

it("gates commissioning health on decoding the actual static reference status shape", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "lingxi-reference-health-"));
  const status = { phase: "idle", session: null, sensorsReady: true, aimdkReady: true,
    calibration: "matched", nav2Available: true, mappingReady: true, planningReady: false,
    motionRecoveryRequired: undefined as boolean | undefined };
  const call = vi.fn(async () => ({ ...status }));
  const reference = Object.assign(Object.create(ReferenceNavigationAdapter.prototype), {
    call, demoMode: false,
  }) as ReferenceNavigationAdapter;
  let server: FastifyInstance | undefined;
  try {
    server = await buildServer({ referenceNavigation: reference, stateDirectory: root,
      pairingCodeHash: pairingCodeHash("synthetic-reference-health") });
    expect((await server.inject("/health")).json()).toMatchObject({ ok: true, referenceStaticReady: true });
    status.mappingReady = false;
    expect((await server.inject("/health")).json()).toMatchObject({ ok: true, referenceStaticReady: false });
    status.mappingReady = true;
    call.mockResolvedValueOnce({ ...status, nav2Available: "wrong" } as unknown as typeof status);
    expect((await server.inject("/health")).json()).toMatchObject({ ok: true, referenceStaticReady: false });
    status.motionRecoveryRequired = true;
    expect((await server.inject("/health")).json()).toMatchObject({ ok: true, referenceStaticReady: false });
    expect(await reference.getSnapshot()).toMatchObject({ connected: true, mappingControlReady: false,
      navigation: { status: "failed", requiresStop: true, detail: expect.stringContaining("未确认停稳") } });
    await expect(reference.selectMode("nav2")).rejects.toThrow("停止当前任务");
    expect((await server.inject({ url: "/health", remoteAddress: "192.0.2.42" })).json())
      .not.toHaveProperty("referenceStaticReady");
  } finally {
    await server?.close();
    await rm(root, { recursive: true, force: true });
  }
});

it("keeps controller changes idle-only and correlates navigation commands to the reference task", async () => {
  const session = "a".repeat(32);
  const map = { id: `fastlio-${session}`, name: "展厅" };
  const point = { id: "p1", mapId: map.id, name: "展台", x: 1, y: 2, yaw: 0, isStart: false };
  let navigation = { session, ready: true, physicalMotionSent: false, status: "idle",
    requiresStop: false, taskId: null as string | null, pointId: null as string | null, detail: "" };
  let calibration: "matched" | "mismatch" | undefined = "matched";
  const call = vi.fn(async (route: string, payload?: { taskId: string; action?: string }) => {
    if (route === "status") return { phase: "idle", session: null, sensorsReady: true, mappingReady: true,
      aimdkReady: true, planningReady: true, calibration, pose: { x: 0, y: 0, yaw: 0 }, poseAtMs: Date.now(), navigation };
    navigation = { ...navigation, taskId: payload!.taskId, pointId: point.id,
      status: payload?.action === "pause" ? "paused" : payload?.action === "stop" ? "idle" : "running" };
    return navigation;
  });
  const adapter = Object.assign(Object.create(ReferenceNavigationAdapter.prototype), {
    call, mode: "point", pendingCommands: 0,
  }) as ReferenceNavigationAdapter;
  expect((await adapter.getSnapshot()).referenceNavigation?.mode).toBe("point");
  for (const unusable of ["mismatch", undefined] as const) {
    calibration = unusable;
    const snapshot = await adapter.getSnapshot();
    expect(snapshot.checks).toContainEqual(expect.objectContaining({ id: "calibration", ready: false }));
    expect(snapshot.mappingControlReady).toBe(false);
    expect(snapshot.referenceNavigation?.planningReady).toBe(false);
    expect(snapshot.poseEvidence).toBeUndefined();
    await expect(adapter.startNavigation(point, map, "not-dispatched")).rejects.toThrow(/标定/);
    expect(call.mock.calls.every(([route]) => route === "status")).toBe(true);
  }
  calibration = "matched";
  await adapter.selectMode("nav2");
  await adapter.startNavigation(point, map, "test-task");
  expect(call).toHaveBeenLastCalledWith("navigation/start", expect.objectContaining({
    session, taskId: "test-task", pointId: point.id, mapId: map.id, mode: "nav2",
  }), 21_000);
  await expect(adapter.selectMode("point")).rejects.toThrow();
  await expect(adapter.controlNavigation("pause", "old-task")).rejects.toThrow();
  await adapter.controlNavigation("pause", "test-task");
  calibration = "mismatch";
  await expect(adapter.controlNavigation("resume", "test-task")).rejects.toThrow(/标定/);
  calibration = "matched";
  await adapter.controlNavigation("resume", "test-task");
  calibration = "mismatch";
  await adapter.controlNavigation("stop", "test-task");
  await adapter.selectMode("point");
});


describe("explicit independent product composition with synthetic services", () => {
  let app: FastifyInstance, root: string, token: string;
  let mapping: MappingBackendState, backend: MappingBackend, localization: LocalizationBackend;
  const speak = vi.fn(async (_text: string, _correlationId: string) => ({ firstAudioMs: 12 }));
  const headers = () => ({ authorization: `Bearer ${token}` });
  const options = () => ({ independentProduct: true, demoMode: true, mappingBackend: backend,
    localizationBackend: localization, savedSlamDirectory: path.join(root, "maps"),
    stateDirectory: path.join(root, "persistent"), staticDirectory: path.join(root, "absent"),
    pairingCodeHash: pairingCodeHash("synthetic-product"), internalAgentToken: "synthetic-product-agent",
    agentSpeaker: { speak, cancel: vi.fn(async () => undefined) } });
  const pair = async () => {
    token = (await app.inject({ method: "POST", url: "/api/v1/session/pair",
      payload: { pairingCode: "synthetic-product" } })).json().token;
  };
  const state = async () => (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
  const event = (payload: object) => app.inject({ method: "POST", url: "/api/v1/agent/events",
    headers: { "x-agent-token": "synthetic-product-agent" }, payload });
  beforeEach(async () => {
    speak.mockClear();
    root = await mkdtemp(path.join(tmpdir(), "lx-product-"));
    await mkdir(path.join(root, "maps"));
    mapping = { backend: "glim", inputMode: "recorded", phase: "idle", session: null,
      mapping: false, cleanupComplete: true };
    backend = { state: vi.fn(async () => mapping), start: vi.fn(async () => mapping), finish: vi.fn() };
    localization = { state: vi.fn(async () => localizationFixture()), command: vi.fn(),
      runtime: vi.fn(async () => localizationRuntimeFixture()), lifecycle: vi.fn() };
    app = await buildServer(options());
    await pair();
  });
  afterEach(async () => { await app?.close(); await rm(root, { recursive: true, force: true }); });

  it("advertises product modules without importing vendor maps or claiming physical readiness", async () => {
    const external = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Synthetic offline boundary"));
    try {
      expect(await state()).toMatchObject({ independentProduct: true, demoMode: false, maps: [],
        savedSlamMaps: [], mappingControlReady: true, motionPresets: [], linkcraftResources: [],
        navigation: { status: "idle", detail: "独立导航待标定与实机验收" } });
      expect((await state()).checks.find((check: { id: string }) => check.id === "navigation").ready).toBe(false);
      expect(external).not.toHaveBeenCalled();
    } finally { external.mockRestore(); }
  });
  it("persists write-only credentials and model settings across the shared API restart", async () => {
    expect((await app.inject({ method: "PUT", url: "/api/v1/settings/ai", headers: headers(),
      payload: { provider: "openai-compatible", model: "synthetic-model", baseUrl: "https://model.invalid" } })).statusCode).toBe(200);
    expect((await app.inject({ method: "PUT", url: "/api/v1/settings/credentials", headers: headers(),
      payload: { apiKey: "synthetic-write-only-key" } })).statusCode).toBe(200);
    await app.close();
    app = await buildServer(options());
    await pair();
    const result = await app.inject({ url: "/api/v1/settings", headers: headers() });
    expect(result.json()).toMatchObject({ model: "synthetic-model", credentialConfigured: true });
    expect(result.body).not.toContain("synthetic-write-only-key");
    expect(JSON.stringify(await state())).not.toContain("synthetic-write-only-key");
  });
  it("still rejects remote cleartext credential submission", async () => {
    expect((await app.inject({ method: "PUT", url: "/api/v1/settings/credentials", headers: headers(),
      remoteAddress: "192.168.1.20", payload: { apiKey: "synthetic-key" } })).statusCode).toBe(426);
  });
  it("routes independent action readiness, paired expressions and idle confirmation through the actions-only backend", async () => {
    const bridge: ActionsState = {
      bridgeMode: "actions-only", physicalActionsEnabled: false, bridgeEpoch: "b".repeat(32),
      sequence: 0, observedAtMs: Date.now(), actionBusy: true, actionStatus: "linkcraft-awaiting-operator",
      motionPresets: [], linkcraftResources: [], expressions: ["开心"],
      actionServices: { presetMotion: true, expression: true, linkcraftCatalog: true,
        linkcraftExecution: true, linkcraftCatalogFresh: true },
    };
    const actions: ActionsBackend = { state: vi.fn(async () => ({ ...bridge, sequence: ++bridge.sequence })),
      playExpression: vi.fn(async () => undefined), confirmIdle: vi.fn(async () => { bridge.actionBusy = false; bridge.actionStatus = null; }) };
    const external = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 503 }));
    const tls = await tlsMaterial(root);
    const isolated = await buildServer({ ...options(), demoMode: false, actionsBackend: actions,
      tlsKey: tls.key, tlsCert: tls.cert });
    const origin = await isolated.listen({ host: "127.0.0.1", port: 0 });
    const paired = await secureRequest(origin, "POST", "/api/v1/session/pair", { pairingCode: "synthetic-product" });
    expect(paired.statusCode).toBe(201);
    const pairedValue = paired.json() as { token: string };
    token = pairedValue.token;
    try {
      const locked = await secureRequest(origin, "GET", "/api/v1/state", undefined, token);
      expect(locked.json()).toMatchObject({ actionBusy: true,
        checks: expect.arrayContaining([expect.objectContaining({ id: "actions", ready: false })]),
        motionPresets: [], linkcraftResources: [] });
      expect((await secureRequest(origin, "POST", "/api/v1/actions/confirm-idle", { confirmed: false }, token)).statusCode).toBe(400);
      const confirmed = await secureRequest(origin, "POST", "/api/v1/actions/confirm-idle", { confirmed: true }, token);
      expect(confirmed.statusCode).toBe(200);
      expect(actions.confirmIdle).toHaveBeenCalledOnce();
      expect((await secureRequest(origin, "GET", "/api/v1/state", undefined, token)).json()).toMatchObject({
        actionBusy: false, expressions: ["开心"] });
      expect(actions.playExpression).not.toHaveBeenCalled();
      expect((await secureRequest(origin, "POST", "/api/v1/actions/expression", { name: "开心" })).statusCode).toBe(401);
      expect((await isolated.inject({ method: "POST", url: "/api/v1/actions/expression",
        headers: headers(), payload: { name: "开心" } })).statusCode).toBe(426);
      for (const payload of [{ name: "" }, { name: "开心", extra: true }, {}]) {
        expect((await secureRequest(origin, "POST", "/api/v1/actions/expression", payload, token)).statusCode).toBe(400);
      }
      expect(actions.playExpression).not.toHaveBeenCalled();
      const expression = await secureRequest(origin, "POST", "/api/v1/actions/expression", { name: "开心" }, token);
      expect(expression.statusCode).toBe(202);
      expect(expression.json()).toEqual({ accepted: true, completed: false });
      expect(actions.playExpression).toHaveBeenCalledOnce();
      expect(actions.playExpression).toHaveBeenCalledWith("开心", expect.any(Function));
      vi.mocked(localization.runtime!).mockResolvedValue({ ...localizationRuntimeFixture(), phase: "running",
        generation: 1, runId: "f".repeat(32), selectedMap: { mapId: `slam-${"b".repeat(32)}`,
          receiptSha256: "c".repeat(64) }, cleanupComplete: false });
      expect((await secureRequest(origin, "GET", "/api/v1/state", undefined, token)).json()).toMatchObject({
        actionBusy: false, expressions: [], checks: expect.arrayContaining([
          expect.objectContaining({ id: "actions", ready: false, detail: "定位未稳定跟踪，表情暂不可用" }),
        ]) });
      expect((await secureRequest(origin, "POST", "/api/v1/actions/expression", { name: "开心" }, token)).statusCode).toBe(409);
      vi.mocked(localization.state).mockResolvedValue({ ...localizationFixture(), generation: 7,
        state: "tracking", reason: "tracking", consecutiveMatches: 3, mapFromSensor: identityMatrix });
      expect((await secureRequest(origin, "GET", "/api/v1/state", undefined, token)).json()).toMatchObject({
        actionBusy: false, expressions: ["开心"], checks: expect.arrayContaining([
          expect.objectContaining({ id: "actions", ready: true }),
        ]) });
      expect((await secureRequest(origin, "POST", "/api/v1/actions/expression", { name: "开心" }, token)).statusCode).toBe(202);
      expect(actions.playExpression).toHaveBeenCalledTimes(2);
    } finally { external.mockRestore(); await isolated.close(); }
  });
  it("uses verified local voice events for evidence-bound Q&A without stopping mapping", async () => {
    mapping = { ...mapping, mapping: true, phase: "running", cleanupComplete: false, session: "a".repeat(32) };
    const recognition = { kind: "recognition", eventId: "recognition-1", sessionId: "synthetic-session",
      occurredAt: Date.now(), transcript: "请问展馆的开放时间是什么？", confidence: 0.99, isFinal: true };
    expect((await event(recognition)).statusCode).toBe(204);
    expect(speak).not.toHaveBeenCalled();
    expect((await event({ kind: "wake", eventId: "wake-1", sessionId: recognition.sessionId,
      occurredAt: Date.now() })).statusCode).toBe(204);
    recognition.eventId = "recognition-2";
    recognition.occurredAt = Date.now();
    const result = await event(recognition);
    expect(result.statusCode).toBe(200);
    expect(result.json()).toMatchObject({ accepted: false, category: "rejected", speechDelivered: true });
    expect(speak).toHaveBeenCalledOnce();
    expect(speak.mock.calls[0]?.[0]).toContain("无法从已配置知识库");
    expect((await event(recognition)).statusCode).toBe(204);
    expect(backend.start).not.toHaveBeenCalled();
    expect(backend.finish).not.toHaveBeenCalled();
    expect(localization.command).not.toHaveBeenCalled();
  });
  it("keeps internal voice authentication separate from paired browser authorization", async () => {
    for (const changes of [{ headers: headers() }, { remoteAddress: "192.168.1.20",
      headers: { "x-agent-token": "synthetic-product-agent" } }]) {
      const result = await app.inject({ method: "POST", url: "/api/v1/agent/events", ...changes,
        payload: { kind: "wake", eventId: "wake", sessionId: "session", occurredAt: Date.now() } });
      expect([401, 403]).toContain(result.statusCode);
    }
    expect(speak).not.toHaveBeenCalled();
  });
  it.each(["/api/v1/navigation/point", "/api/v1/navigation/control", "/api/v1/maps/legacy/activate",
    "/api/v1/tours/legacy/start", "/api/v1/actions/confirm-idle", "/api/v1/actions/expression"])("does not enable %s", async (url) => {
    expect((await app.inject({ method: "POST", url, headers: headers(), payload: {} })).statusCode).toBe(403);
  });
  it("requires explicit composition and refuses vendor and isolated-mode mixing", async () => {
    for (const conflict of [{ slamConsole: true }, { mappingConsole: true }, { localizationConsole: true },
      { adapter: new DemoAdapter() }, { mappingBackend: undefined }, { localizationBackend: undefined }]) {
      await expect(buildServer({ ...options(), ...conflict })).rejects.toThrow();
    }
  });
  it("rejects a navigation socket outside the explicit independent product before creating a client", async () => {
    vi.stubEnv("LINGXI_NAVIGATION_SOCKET", "/private/navigation.sock");
    try {
      await expect(buildServer({ demoMode: true })).rejects.toThrow("physical_navigation_requires_independent_product");
    } finally { vi.unstubAllEnvs(); }
  });
});

describe("physical navigation server composition", () => {
  let app: FastifyInstance;
  let root: string;

  function physicalState(mapId: string, session: string, sequence: number): PhysicalNavigationState {
    return {
      protocol: 1, source: "ros2-nav2", session, sequence,
      identity: {
        mapId, receiptSha256: "c".repeat(64), calibrationSha256: "d".repeat(64),
        footprintSha256: "e".repeat(64),
      },
      phase: "idle", detail: "", ticket: null, pointId: null, requiresStop: false,
      outputQuiescent: true, pose: { x: 0, y: 0, yaw: 0 }, poseObservedAtMs: Date.now(),
      checks: { nav2Lifecycle: true, localization: true, tf: true, costmap: true,
        terrain: true, footprint: true, velocityOutput: true },
      navigationReady: true, physicalMotionSent: false,
    };
  }

  afterEach(async () => {
    await app?.close();
    await rm(root, { recursive: true, force: true });
  });

  it("promotes a verified saved map only with a physical bridge", async () => {
    root = await mkdtemp(path.join(tmpdir(), "lx-physical-product-"));
    const maps = path.join(root, "maps");
    await mkdir(maps);
    const session = "a".repeat(32);
    const fixture = await savedSlamFixture(maps, { session });
    let sequence = 0;
    const physical: PhysicalNavigationBackend = {
      physical: true,
      state: vi.fn(async () => physicalState(fixture.id, session, ++sequence)),
      command: vi.fn(async () => physicalState(fixture.id, session, ++sequence)),
      activateMap: vi.fn(async () => physicalState(fixture.id, session, ++sequence)),
      close: vi.fn(async () => undefined),
    };
    const mapping: MappingBackend = {
      state: vi.fn(async () => ({ backend: "glim", inputMode: "recorded", phase: "idle", session: null,
        mapping: false, cleanupComplete: true })),
      start: vi.fn(async () => ({ backend: "glim", inputMode: "recorded", phase: "running", session,
        mapping: true, cleanupComplete: false })),
      finish: vi.fn(async () => ({ session, saved: false, confirmed: true as const })),
    };
    const localization: LocalizationBackend = {
      state: vi.fn(async () => localizationFixture()), command: vi.fn(),
      runtime: vi.fn(async () => localizationRuntimeFixture()), lifecycle: vi.fn(),
    };
    app = await buildServer({ independentProduct: true, demoMode: true, mappingBackend: mapping,
      localizationBackend: localization, physicalNavigationBackend: physical,
      savedSlamDirectory: maps, stateDirectory: path.join(root, "state"),
      staticDirectory: path.join(root, "absent"), pairingCodeHash: pairingCodeHash("physical-product"),
      internalAgentToken: "synthetic-physical-agent" });
    const token = (await app.inject({ method: "POST", url: "/api/v1/session/pair",
      payload: { pairingCode: "physical-product" } })).json().token;
    const headers = { authorization: `Bearer ${token}` };

    const activate = await app.inject({ method: "POST", url: `/api/v1/maps/${fixture.id}/activate`, headers,
      payload: {} });
    expect(activate.statusCode).toBe(200);
    expect(physical.activateMap).toHaveBeenCalledOnce();
    const publicState = (await app.inject({ url: "/api/v1/state", headers })).json();
    expect(publicState.maps)
      .toEqual(expect.arrayContaining([expect.objectContaining({ id: fixture.id, active: true })]));
    expect(publicState.poseEvidence).toMatchObject({ source: "independent-localization", coordinateUnits: "declared-metres" });

    const point = await app.inject({ method: "POST", url: "/api/v1/points", headers,
      payload: { mapId: fixture.id, name: "入口", x: 0, y: 0, yaw: 0 } });
    expect(point.statusCode).toBe(201);

    const wake = await app.inject({ method: "POST", url: "/api/v1/agent/events",
      headers: { "x-agent-token": "synthetic-physical-agent" },
      payload: { kind: "wake", eventId: "physical-wake", sessionId: "physical-voice-session", occurredAt: Date.now() } });
    expect(wake.statusCode).toBe(204);
    const question = await app.inject({ method: "POST", url: "/api/v1/agent/events",
      headers: { "x-agent-token": "synthetic-physical-agent" },
      payload: { kind: "recognition", eventId: "physical-question", sessionId: "physical-voice-session",
        occurredAt: Date.now(), transcript: "请问展馆的开放时间是什么？", confidence: 0.99, isFinal: true } });
    expect(question.statusCode).toBe(200);
    expect(question.json()).toMatchObject({ category: "rejected", speechDelivered: true });
    expect(physical.command).not.toHaveBeenCalled();
  });
});
