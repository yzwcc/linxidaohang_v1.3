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
          const fixture = await savedSlamFixture(maps, { session, inputMode: "recorded" });
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
    expect(snapshot).toMatchObject({ mapping: true, mappingStatus: "failed", mappingBackend: "glim" });
    expect(snapshot.checks.find((check) => check.id === "slam")?.ready).toBe(false);
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
