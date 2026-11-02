import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import { DemoAdapter } from "../src/adapters/demo";
import type { MappingBackend, MappingBackendState } from "../src/mapping-backend";
import type { LocalizationBackend } from "../src/localization-backend";
import { localizationFixture, localizationRuntimeFixture } from "./fixtures/localization-state";

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
    "/api/v1/tours/legacy/start", "/api/v1/actions/confirm-idle"])("does not enable %s", async (url) => {
    expect((await app.inject({ method: "POST", url, headers: headers(), payload: {} })).statusCode).toBe(403);
  });
  it("requires explicit composition and refuses vendor and isolated-mode mixing", async () => {
    for (const conflict of [{ slamConsole: true }, { mappingConsole: true }, { localizationConsole: true },
      { adapter: new DemoAdapter() }, { mappingBackend: undefined }, { localizationBackend: undefined }]) {
      await expect(buildServer({ ...options(), ...conflict })).rejects.toThrow();
    }
  });
});
