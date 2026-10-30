import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import type { FastifyInstance } from "fastify";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import type { MappingBackend, MappingBackendState } from "../src/mapping-backend";
import type { LocalizationBackend } from "../src/localization-backend";
import type { LocalizationRuntime } from "../../shared/localization";
import { localizationFixture, localizationRuntimeFixture } from "./fixtures/localization-state";

describe("shared paired SLAM workspace", () => {
  let app: FastifyInstance, root: string, token: string;
  let mapping: MappingBackendState, runtime: LocalizationRuntime;
  let mappingBackend: MappingBackend, localizationBackend: LocalizationBackend;
  const headers = () => ({ authorization: `Bearer ${token}` });
  const options = () => ({ demoMode: true, slamConsole: true, mappingBackend, localizationBackend,
    savedSlamDirectory: path.join(root, "maps"), stateDirectory: path.join(root, "data"),
    staticDirectory: path.join(root, "absent"), pairingCodeHash: pairingCodeHash("synthetic-slam-workspace") });
  const start = () => app.inject({ method: "POST", url: "/api/v1/slam/localization/start", headers: headers(),
    payload: { serviceId: runtime.serviceId, expectedGeneration: runtime.generation,
      mapId: `slam-${"b".repeat(32)}`, receiptSha256: "c".repeat(64) } });
  const state = async () => (await app.inject({ url: "/api/v1/state", headers: headers() })).json();

  beforeEach(async () => {
    root = await mkdtemp(path.join(tmpdir(), "lx-workspace-"));
    await mkdir(path.join(root, "maps"));
    mapping = { backend: "glim", inputMode: "recorded", phase: "idle", session: null,
      mapping: false, cleanupComplete: true };
    runtime = localizationRuntimeFixture();
    mappingBackend = { state: vi.fn(async () => mapping), finish: vi.fn(), start: vi.fn(async () => {
      mapping = { ...mapping, phase: "requested", mapping: true, cleanupComplete: false, session: "a".repeat(32) };
      return mapping;
    }) };
    localizationBackend = { state: vi.fn(async () => localizationFixture()), command: vi.fn(),
      runtime: vi.fn(async () => structuredClone(runtime)), lifecycle: vi.fn(async (command) => {
        runtime = { ...runtime, generation: runtime.generation + 1, phase: "starting", cleanupComplete: false,
          runId: "2".repeat(32), selectedMap: command.action === "start"
            ? { mapId: command.mapId, receiptSha256: command.receiptSha256 } : runtime.selectedMap };
        return structuredClone(runtime);
      }) };
    app = await buildServer(options());
    token = (await app.inject({ method: "POST", url: "/api/v1/session/pair",
      payload: { pairingCode: "synthetic-slam-workspace" } })).json().token;
  });
  afterEach(async () => { await app?.close(); await rm(root, { recursive: true, force: true }); });

  it("advertises both capabilities without claiming vendor, localization or navigation readiness", async () => {
    const external = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Unexpected external request"));
    try {
      const result = await state();
      expect(result).toMatchObject({ slamWorkspace: true, mappingControlReady: true, mapping: false,
        demoMode: false, maps: [], points: [], tours: [], savedSlamMaps: [] });
      for (const id of ["aimdk", "lidar", "odom", "navigation", "linksoul"]) {
        expect(result.checks.find((item: { id: string }) => item.id === id).ready).toBe(false);
      }
      expect(external).not.toHaveBeenCalled();
    } finally { external.mockRestore(); }
  });
  it("allows mapping and on-demand localization through the same paired session", async () => {
    expect((await start()).statusCode).toBe(202);
    expect(localizationBackend.lifecycle).toHaveBeenCalledOnce();
    runtime = { ...runtime, phase: "stopped", generation: runtime.generation + 1, cleanupComplete: true };
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers: headers() })).statusCode).toBe(202);
    expect(mappingBackend.start).toHaveBeenCalledOnce();
  });
  it.each(["starting", "running", "stopping", "failed"] as const)(
    "keeps mapping unavailable while localization %s has unconfirmed cleanup", async (phase) => {
      runtime = { ...runtime, phase, generation: 1, runId: "2".repeat(32), cleanupComplete: false,
        selectedMap: { mapId: `slam-${"b".repeat(32)}`, receiptSha256: "c".repeat(64) },
        ...(phase === "failed" ? { failureCode: "process_cleanup_failed" as const } : {}) };
      expect((await state()).mappingControlReady).toBe(false);
      expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers: headers() })).statusCode).toBe(409);
      expect(mappingBackend.start).not.toHaveBeenCalled();
    });
  it("does not dispatch localization while mapping is running or unconfirmed", async () => {
    mapping = { ...mapping, phase: "running", mapping: true, cleanupComplete: false, session: "a".repeat(32) };
    expect((await start()).statusCode).toBe(409);
    expect(localizationBackend.lifecycle).not.toHaveBeenCalled();
    mapping = { ...mapping, phase: "failed", mapping: false };
    expect((await start()).statusCode).toBe(409);
    mapping = { ...mapping, phase: "idle", cleanupComplete: true };
    expect((await start()).statusCode).toBe(202);
  });
  it("keeps mapping disabled when the localization service cannot confirm its slot", async () => {
    vi.mocked(localizationBackend.runtime!).mockRejectedValue(new Error("Synthetic service unavailable"));
    expect((await state()).mappingControlReady).toBe(false);
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers: headers() })).statusCode).toBe(409);
    expect(mappingBackend.start).not.toHaveBeenCalled();
  });
  it("still requires pairing and does not expose unrelated business commands", async () => {
    expect((await app.inject({ url: "/api/v1/state" })).statusCode).toBe(401);
    for (const url of ["/api/v1/navigation/point", "/api/v1/agent/events", "/api/v1/points", "/api/v1/tours"]) {
      expect((await app.inject({ method: "POST", url, headers: headers(), payload: {} })).statusCode).toBe(403);
    }
  });
  it("requires an explicit workspace and two service-capable backends", async () => {
    await expect(buildServer({ ...options(), slamConsole: false, localizationConsole: true })).rejects.toThrow();
    await expect(buildServer({ ...options(), localizationBackend: {
      state: localizationBackend.state, command: localizationBackend.command } })).rejects.toThrow();
    await expect(buildServer({ ...options(), mappingBackend: undefined })).rejects.toThrow();
  });
});
