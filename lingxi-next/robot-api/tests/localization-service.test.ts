import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import type { FastifyInstance } from "fastify";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import { confirmedLocalizationLifecycle, type LocalizationBackend } from "../src/localization-backend";
import {
  localizationRuntimeSchema, localizationLifecycleConfirmed, sameLocalizationRuntime, localizationStateMatchesRuntime,
  type LocalizationRuntime, type LocalizationLifecycleCommand,
} from "../../shared/localization";
import { localizationFixture, localizationRuntimeFixture } from "./fixtures/localization-state";

const selection = { mapId: `slam-${"b".repeat(32)}`, receiptSha256: "c".repeat(64) };
const start = { action: "start" as const, serviceId: "1".repeat(32), expectedGeneration: 0, ...selection };
const running = (): LocalizationRuntime => ({ ...localizationRuntimeFixture(), generation: 1,
  runId: "2".repeat(32), selectedMap: selection, phase: "running", cleanupComplete: false });

describe("service lifecycle contract", () => {
  it.each([
    { runId: "2".repeat(32) }, { cleanupComplete: false }, { generation: 1 }, { navigationReady: true },
    { inputMode: "synthetic" }, { failureCode: "internal_path" }, { selectedMap: selection },
    { phase: "running" }, { extra: true }, { serviceId: "../../private" },
  ])("rejects inconsistent runtime metadata %j", (patch) => {
    expect(localizationRuntimeSchema.safeParse({ ...localizationRuntimeFixture(), ...patch }).success).toBe(false);
  });
  it("distinguishes starting, running, shutdown and confirmed cleanup", () => {
    const pending = { ...running(), phase: "starting" as const };
    expect(confirmedLocalizationLifecycle(start, pending)).toEqual(pending);
    expect(localizationLifecycleConfirmed(start, running())).toBe(false);
    expect(localizationStateMatchesRuntime(localizationFixture(), running())).toBe(true);
    expect(localizationStateMatchesRuntime(localizationFixture(), pending)).toBe(false);
    for (const patch of [{ generation: 2 }, { serviceId: "3".repeat(32) }, { runId: "4".repeat(32) },
      { phase: "stopping" as const }, { selectedMap: { ...selection, receiptSha256: "f".repeat(64) } }]) {
      expect(sameLocalizationRuntime(running(), { ...running(), ...patch })).toBe(false);
    }
    const shutdown = { action: "shutdown" as const, serviceId: start.serviceId, expectedGeneration: 1, runId: "2".repeat(32) };
    for (const phase of ["stopping", "stopped", "failed"] as const) {
      const result = { ...running(), generation: 2, phase, cleanupComplete: phase === "stopped",
        ...(phase === "failed" ? { failureCode: "process_cleanup_failed" as const } : {}) };
      expect(confirmedLocalizationLifecycle(shutdown, result)).toEqual(result);
    }
  });
  it.each([
    { serviceId: "3".repeat(32) }, { generation: 0 }, { generation: 2 }, { closed: true },
    { selectedMap: { ...selection, mapId: `slam-${"f".repeat(32)}` } },
    { selectedMap: { ...selection, receiptSha256: "f".repeat(64) } },
  ])("does not accept a stale or unrelated start acknowledgement %j", (patch) => {
    expect(() => confirmedLocalizationLifecycle(start, { ...running(), phase: "starting", ...patch })).toThrow();
  });
});

describe("paired on-demand localization lifecycle", () => {
  let root: string, app: FastifyInstance, token: string, runtime: LocalizationRuntime;
  let backend: LocalizationBackend & Required<Pick<LocalizationBackend, "runtime" | "lifecycle">>;
  const headers = () => ({ authorization: `Bearer ${token}` });
  const options = () => ({ demoMode: true, localizationConsole: true, localizationBackend: backend,
    stateDirectory: path.join(root, "data"), savedSlamDirectory: path.join(root, "maps"),
    staticDirectory: path.join(root, "none"), pairingCodeHash: pairingCodeHash("synthetic-service-test") });
  const post = (command: LocalizationLifecycleCommand, extraHeaders = {}) => {
    const { action, ...payload } = command;
    return app.inject({ method: "POST", url: `/api/v1/slam/localization/${action}`,
      headers: { ...headers(), ...extraHeaders }, payload });
  };
  beforeEach(async () => {
    root = await mkdtemp(path.join(tmpdir(), "lx-service-api-"));
    await mkdir(path.join(root, "maps"));
    runtime = localizationRuntimeFixture();
    backend = {
      state: vi.fn(async () => localizationFixture()), command: vi.fn(),
      runtime: vi.fn(async () => structuredClone(runtime)),
      lifecycle: vi.fn(async (command) => {
        if (command.serviceId !== runtime.serviceId || command.expectedGeneration !== runtime.generation ||
            (command.action === "start" && !runtime.cleanupComplete) ||
            (command.action === "shutdown" && command.runId !== runtime.runId)) {
          throw Object.assign(new Error("Runtime changed"), { statusCode: 409 });
        }
        runtime = command.action === "start" ? { ...running(), phase: "starting" } :
          { ...runtime, generation: runtime.generation + 1, phase: "stopping", cleanupComplete: false };
        return structuredClone(runtime);
      }),
    };
    app = await buildServer(options());
    token = (await app.inject({ method: "POST", url: "/api/v1/session/pair",
      payload: { pairingCode: "synthetic-service-test" } })).json().token;
  });
  afterEach(async () => { await app?.close(); await rm(root, { recursive: true, force: true }); });

  it("reads idle service without requiring a running child", async () => {
    const result = await app.inject({ url: "/api/v1/slam/localization/runtime", headers: headers() });
    expect(result.statusCode).toBe(200);
    expect(result.json()).toEqual(runtime);
    expect(backend.state).not.toHaveBeenCalled();
  });
  it("requires pairing for lifecycle reads and writes", async () => {
    expect((await app.inject({ url: "/api/v1/slam/localization/runtime" })).statusCode).toBe(401);
    for (const action of ["start", "shutdown"]) expect((await app.inject({ method: "POST",
      url: `/api/v1/slam/localization/${action}`, payload: {} })).statusCode).toBe(401);
    expect(backend.lifecycle).not.toHaveBeenCalled();
  });
  it("starts asynchronously, shuts down the same run, and does not retry conflicts", async () => {
    const accepted = await post(start);
    expect(accepted.statusCode).toBe(202);
    expect(accepted.json()).toMatchObject({ phase: "starting", generation: 1, cleanupComplete: false,
      localizationReady: false, navigationReady: false });
    expect(backend.lifecycle).toHaveBeenCalledExactlyOnceWith(start);
    expect((await post(start)).statusCode).toBe(409);
    expect(backend.lifecycle).toHaveBeenCalledTimes(2);
    const result = await post({ action: "shutdown", serviceId: runtime.serviceId,
      expectedGeneration: runtime.generation, runId: runtime.runId! });
    expect(result.statusCode).toBe(202);
    expect(result.json()).toMatchObject({ phase: "stopping", generation: 2, cleanupComplete: false });
    expect(backend.command).not.toHaveBeenCalled();
  });
  it.each([{ expectedGeneration: -1 }, { expectedGeneration: Number.MAX_SAFE_INTEGER }, { serviceId: "" },
    { mapId: "/private/map" }, { receiptSha256: "" }, { path: "/private/map" }])(
    "rejects invalid selection before reaching native service %j", async (patch) => {
      const { action, ...payload } = start;
      expect((await app.inject({ method: "POST", url: `/api/v1/slam/localization/${action}`,
        headers: headers(), payload: { ...payload, ...patch } })).statusCode).toBe(400);
      expect(backend.lifecycle).not.toHaveBeenCalled();
    });
  it("requires HTTPS for both new commands outside loopback development", async () => {
    for (const action of ["start", "shutdown"]) expect((await app.inject({ method: "POST",
      url: `/api/v1/slam/localization/${action}`, headers: { ...headers(), "x-forwarded-proto": "https" },
      remoteAddress: "192.0.2.1", payload: {} })).statusCode).toBe(426);
    expect(backend.lifecycle).not.toHaveBeenCalled();
  });
  it("keeps a fixed-map backend available without offering lifecycle control", async () => {
    const fixed = await buildServer({ ...options(), localizationBackend: { state: backend.state, command: backend.command } });
    try {
      const paired = (await fixed.inject({ method: "POST", url: "/api/v1/session/pair",
        payload: { pairingCode: "synthetic-service-test" } })).json().token;
      const h = { authorization: `Bearer ${paired}` };
      expect((await fixed.inject({ url: "/api/v1/slam/localization", headers: h })).statusCode).toBe(200);
      expect((await fixed.inject({ url: "/api/v1/slam/localization/runtime", headers: h })).statusCode).toBe(501);
      expect((await fixed.inject({ method: "POST", url: "/api/v1/slam/localization/start", headers: h,
        payload: {} })).statusCode).toBe(501);
    } finally { await fixed.close(); }
  });
  it("rejects invalid service snapshots and mismatched acknowledgements", async () => {
    vi.mocked(backend.runtime).mockResolvedValue({ ...runtime, navigationReady: true } as unknown as LocalizationRuntime);
    expect((await app.inject({ url: "/api/v1/slam/localization/runtime", headers: headers() })).statusCode).toBe(503);
    vi.mocked(backend.lifecycle).mockResolvedValue({ ...running(), phase: "starting", serviceId: "f".repeat(32) });
    expect((await post(start)).statusCode).toBe(503);
    expect(backend.lifecycle).toHaveBeenCalledOnce();
  });
});
