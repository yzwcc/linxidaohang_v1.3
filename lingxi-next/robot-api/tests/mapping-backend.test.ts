import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import type { FastifyInstance } from "fastify";
import { buildServer } from "../src/server";
import { DemoAdapter } from "../src/adapters/demo";
import { pairingCodeHash } from "../src/auth";
import { mappingGuard, type MappingBackend, type MappingBackendState } from "../src/mapping-backend";
import { savedSlamFixture } from "./fixtures/saved-slam-map";

const identity = "bbaaccdd00112233445566778899aabb";
const idle: MappingBackendState = {
  backend: "glim", inputMode: "recorded", phase: "idle", session: null,
  mapping: false, cleanupComplete: true,
};

describe("independent recorded mapping controls", () => {
  let root: string, maps: string, token: string;
  let app: FastifyInstance | undefined;
  let state: MappingBackendState;
  let backend: MappingBackend;
  let adapter: DemoAdapter;
  const headers = () => ({ authorization: `Bearer ${token}` });

  beforeEach(async () => {
    root = await mkdtemp(path.join(tmpdir(), "lingxi-mapping-control-"));
    maps = path.join(root, "maps");
    await mkdir(maps);
    state = { ...idle };
    adapter = new DemoAdapter();
    backend = {
      state: vi.fn(async () => state),
      start: vi.fn(async () => {
        state = { ...idle, phase: "running", session: identity, mapping: true, cleanupComplete: false };
        return state;
      }),
      finish: vi.fn(async (session, save, name) => {
        if (session !== state.session) throw Object.assign(new Error("Session changed"), { statusCode: 409 });
        if (save) {
          const fixture = await savedSlamFixture(maps, { session, inputMode: state.inputMode });
          await writeFile(fixture.ready, JSON.stringify({ ...fixture.receipt, name }));
        }
        state = { ...idle, session };
        return { session, saved: save, confirmed: true, ...(save ? { mapId: `slam-${session}` } : {}) };
      }),
    };
    app = await buildServer({
      demoMode: true, stateDirectory: path.join(root, "state"), savedSlamDirectory: maps,
      staticDirectory: path.join(root, "absent"), mappingBackend: backend, adapter,
      pairingCodeHash: pairingCodeHash("synthetic-mapping-control-only"),
    });
    const paired = await app.inject({
      method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "synthetic-mapping-control-only" },
    });
    token = paired.json().token;
  });

  afterEach(async () => {
    await app?.close();
    await rm(root, { recursive: true, force: true });
  });

  it("requires pairing for every mapping control", async () => {
    for (const route of ["start", "stop-save", "stop-discard"]) {
      expect((await app!.inject({
        method: "POST", url: `/api/v1/mapping/${route}`, payload: {},
      })).statusCode).toBe(401);
    }
    expect(backend.start).not.toHaveBeenCalled();
    expect(backend.finish).not.toHaveBeenCalled();
  });

  it("starts, exposes session identity and rejects a second start", async () => {
    expect((await app!.inject({
      method: "POST", url: "/api/v1/mapping/start", headers: headers(),
    })).statusCode).toBe(202);
    const result = (await app!.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(result).toMatchObject({
      mapping: true, mappingStatus: "running", mappingSession: identity,
      mappingBackend: "glim", mappingInputMode: "recorded",
    });
    expect(result.checks.find((check: { id: string }) => check.id === "navigation").ready).toBe(false);
    expect((await app!.inject({
      method: "POST", url: "/api/v1/mapping/start", headers: headers(),
    })).statusCode).toBe(409);
    expect(backend.start).toHaveBeenCalledTimes(1);
  });

  it("confirms a save only through the catalog and never activates an AIMDK map", async () => {
    const nativeSave = vi.spyOn(adapter, "stopAndSaveMap");
    await backend.start();
    const result = await app!.inject({
      method: "POST", url: "/api/v1/mapping/stop-save", headers: headers(),
      payload: { session: identity, name: " Recorded control map " },
    });
    expect(result.statusCode).toBe(200);
    expect(result.json()).toMatchObject({
      confirmed: true, savedSlamMap: {
        id: `slam-${identity}`, name: "Recorded control map", inputMode: "recorded",
        navigationReady: false, localizationReady: false, coordinateUnits: "native-raw",
      },
    });
    expect(result.json()).not.toHaveProperty("map");
    const snapshot = (await app!.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(snapshot.mapping).toBe(false);
    expect(snapshot.savedSlamMaps).toHaveLength(1);
    expect(snapshot.maps.some((map: { id: string }) => map.id === `slam-${identity}`)).toBe(false);
    expect(snapshot.activeMapId).not.toBe(`slam-${identity}`);
    expect(nativeSave).not.toHaveBeenCalled();
  });

  it("does not treat a backend acknowledgement without a validated map as saved", async () => {
    await backend.start();
    vi.mocked(backend.finish).mockResolvedValue({
      session: identity, saved: true, confirmed: true, mapId: `slam-${identity}`,
    });
    const result = await app!.inject({
      method: "POST", url: "/api/v1/mapping/stop-save", headers: headers(),
      payload: { session: identity, name: "Missing map" },
    });
    expect(result.statusCode).toBe(503);
    expect(result.json()).not.toHaveProperty("confirmed");
  });

  it("validates live save receipts against the authoritative session mode", async () => {
    state = { ...idle, inputMode: "live", phase: "running", mapping: true, session: identity };
    const snapshot = await mappingGuard(adapter, backend).getSnapshot();
    expect(snapshot.mappingInputMode).toBe("live");
    expect(snapshot.checks.find((check) => check.id === "slam")?.detail).toContain("实时采集");
    const result = await app!.inject({
      method: "POST", url: "/api/v1/mapping/stop-save", headers: headers(),
      payload: { session: identity, name: "Live control map" },
    });
    expect(result.statusCode).toBe(200);
    expect(result.json()).toMatchObject({
      confirmed: true, savedSlamMap: { inputMode: "live", navigationReady: false, localizationReady: false },
    });
  });

  it("exposes the configured live limit before and during a session without guessing a countdown", async () => {
    state = { ...idle, inputMode: "live", captureLimitSeconds: 73.25 };
    let snapshot = (await app!.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(snapshot.mappingCaptureLimitSeconds).toBe(73.25);
    expect(snapshot).not.toHaveProperty("mappingRemainingSeconds");
    state = { ...state, phase: "running", session: identity, mapping: true };
    snapshot = (await app!.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(snapshot.mappingCaptureLimitSeconds).toBe(73.25);
    expect(snapshot.mappingSession).toBe(identity);
  });

  it("does not show a capture limit for recorded or disconnected backends", async () => {
    state = { ...idle, captureLimitSeconds: 300 };
    expect((await mappingGuard(adapter, backend).getSnapshot()).mappingCaptureLimitSeconds).toBeUndefined();
    vi.mocked(backend.state).mockRejectedValue(new Error("Synthetic unavailable"));
    expect((await mappingGuard(adapter, backend).getSnapshot()).mappingCaptureLimitSeconds).toBeUndefined();
  });

  it("keeps full mapper backlog and observation age distinct from input progress", async () => {
    state = { ...idle, phase: "running", session: identity, mapping: true,
      completedMessages: 1000, processedScans: 100, estimatedPoses: 90,
      mapper: { engine: "glim-full-async-v1", acceptedFrames: 70, completedFrames: 60, frameCapacity: 128,
        acceptedImu: 800, completedImu: 780, imuCapacity: 8192,
        maximumWaitMs: 6000, maximumTaskMs: 5000, ageSeconds: .2 } };
    const snapshot = (await app!.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(snapshot.mappingProgress).toEqual({ completedMessages: 1000, processedScans: 100, estimatedPoses: 90 });
    expect(snapshot.mappingOptimization).toMatchObject({
      engine: "glim-full-async-v1", acceptedFrames: 70, completedFrames: 60, pendingFrames: 10,
      frameCapacity: 128, pendingImu: 20, imuCapacity: 8192, maximumWaitMs: 6000, observationAgeMs: 200,
    });
    vi.mocked(backend.state).mockRejectedValue(new Error("Synthetic disconnect"));
    expect((await mappingGuard(adapter, backend).getSnapshot()).mappingOptimization).toBeUndefined();
  });

  it("rejects a recorded receipt for a live session", async () => {
    state = { ...idle, inputMode: "live", phase: "running", mapping: true, session: identity };
    vi.mocked(backend.finish).mockImplementation(async (session, save, name) => {
      const fixture = await savedSlamFixture(maps, { session, inputMode: "recorded" });
      await writeFile(fixture.ready, JSON.stringify({ ...fixture.receipt, name }));
      return { session, saved: save, confirmed: true, mapId: `slam-${session}` };
    });
    const result = await app!.inject({
      method: "POST", url: "/api/v1/mapping/stop-save", headers: headers(),
      payload: { session: identity, name: "Wrong input" },
    });
    expect(result.statusCode).toBe(503);
    expect(result.json()).not.toHaveProperty("confirmed");
  });

  it("rejects a stale save before finalizing the current session", async () => {
    await backend.start();
    const result = await app!.inject({
      method: "POST", url: "/api/v1/mapping/stop-save", headers: headers(),
      payload: { session: "0".repeat(32), name: "Stale map" },
    });
    expect(result.statusCode).toBe(409);
    expect(backend.finish).not.toHaveBeenCalled();
  });

  it.each([
    { name: "No session" }, { session: identity, name: 7 }, { session: identity, name: "" },
    { session: "invalid", name: "Invalid session" },
    { session: identity, name: "Extra fields", command: "unaccepted" },
  ])("rejects malformed save requests before backend work: %j", async (payload) => {
    expect((await app!.inject({
      method: "POST", url: "/api/v1/mapping/stop-save", headers: headers(), payload,
    })).statusCode).toBe(400);
    expect(backend.finish).not.toHaveBeenCalled();
  });

  it("discards without creating a map and rejects a stale session", async () => {
    await backend.start();
    expect((await app!.inject({
      method: "POST", url: "/api/v1/mapping/stop-discard", headers: headers(),
      payload: { session: "0".repeat(32) },
    })).statusCode).toBe(409);
    expect((await app!.inject({
      method: "POST", url: "/api/v1/mapping/stop-discard", headers: headers(),
      payload: { session: identity },
    })).json()).toEqual({ confirmed: true, saved: false });
    expect((await app!.inject({ url: "/api/v1/slam/maps", headers: headers() })).json().maps).toEqual([]);
  });

  it("fails closed for unavailable mapping status", async () => {
    vi.mocked(backend.state).mockRejectedValue(new Error("Synthetic service unavailable"));
    const snapshot = await mappingGuard(adapter, backend).getSnapshot();
    expect(snapshot).toMatchObject({ mapping: true, mappingStatus: "failed", mappingBackend: "glim",
      mappingFailure: "mapping_service_unavailable", mappingCleanupComplete: false });
    expect(snapshot.checks.find((check) => check.id === "slam")?.ready).toBe(false);
    expect(snapshot.mappingProgress).toBeUndefined();
    const response = await app!.inject({ url: "/api/v1/state", headers: headers() });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({ mappingFailure: "mapping_service_unavailable",
      mappingCleanupComplete: false });
    expect(response.body).not.toContain("Synthetic service unavailable");
  });

  it("propagates failure and cleanup through the API, clearing the failure for a new session", async () => {
    state = { ...idle, phase: "failed", session: identity, mapping: true, cleanupComplete: false,
      failureCode: "process_startup_deadline_exceeded" };
    const snapshot = async () => (await app!.inject({ url: "/api/v1/state", headers: headers() })).json();
    expect(await snapshot()).toMatchObject({ mappingStatus: "failed",
      mappingFailure: "process_startup_deadline_exceeded", mappingCleanupComplete: false });
    state = { ...state, mapping: false, cleanupComplete: true };
    expect(await snapshot()).toMatchObject({ mappingStatus: "failed",
      mappingFailure: "process_startup_deadline_exceeded", mappingCleanupComplete: true });
    await backend.start();
    const running = await snapshot();
    expect(running).toMatchObject({ mappingStatus: "running", mappingCleanupComplete: false });
    expect(running).not.toHaveProperty("mappingFailure");
    expect(running.checks.find((check: { id: string }) => check.id === "navigation").ready).toBe(false);
  });

  it("uses a generic failure for older services and ignores a stale failure in healthy state", async () => {
    const guarded = mappingGuard(adapter, backend);
    state = { ...idle, phase: "failed" };
    expect((await guarded.getSnapshot()).mappingFailure).toBe("mapping_process_failed");
    state = { ...idle, failureCode: "child_reported_failure" };
    expect((await guarded.getSnapshot()).mappingFailure).toBeUndefined();
  });

  it("reports actual mapping counters without manufacturing missing progress", async () => {
    state = { ...idle, inputMode: "live", phase: "running", mapping: true, session: identity,
      completedMessages: 420, processedScans: 20, estimatedPoses: 8 };
    expect(await mappingGuard(adapter, backend).getSnapshot()).toMatchObject({
      mappingInputMode: "live", mappingProgress: {
        completedMessages: 420, processedScans: 20, estimatedPoses: 8,
      },
    });
    state = { ...idle, processedScans: 0 };
    expect((await mappingGuard(adapter, backend).getSnapshot()).mappingProgress).toBeUndefined();
  });

  it("allows a new attempt after a failed session has fully cleaned up", async () => {
    state = { ...idle, phase: "failed", session: identity };
    expect((await app!.inject({
      method: "POST", url: "/api/v1/mapping/start", headers: headers(),
    })).statusCode).toBe(202);
    expect(backend.start).toHaveBeenCalledOnce();
  });

  it("does not dispatch movement or resource playback in recorded mode, but can stop navigation", async () => {
    const originalControl = vi.spyOn(adapter, "controlNavigation").mockResolvedValue();
    const originalMotion = vi.spyOn(adapter, "startPresetMotion");
    const guarded = mappingGuard(adapter, backend);
    await expect(guarded.activateMap("demo")).rejects.toThrow();
    await expect(guarded.startNavigation({} as never)).rejects.toThrow();
    await expect(guarded.startPresetMotion("unknown")).rejects.toThrow();
    await expect(guarded.executeLinkcraftResource("unknown")).rejects.toThrow();
    await expect(guarded.controlNavigation("resume", "old-session")).rejects.toThrow();
    await expect(guarded.controlNavigation("pause", "old-session")).rejects.toThrow();
    await guarded.controlNavigation("stop", "old-session");
    expect(originalControl).toHaveBeenCalledExactlyOnceWith("stop", "old-session");
    expect(originalMotion).not.toHaveBeenCalled();
  });

  it("requires a saved-map directory whenever the independent backend is configured", async () => {
    await expect(buildServer({ demoMode: true, mappingBackend: backend })).rejects.toThrow(
      "independent_mapping_requires_saved_slam_directory");
  });
});
