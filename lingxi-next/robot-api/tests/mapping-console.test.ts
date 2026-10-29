import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import type { FastifyInstance } from "fastify";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import type { MappingBackend, MappingBackendState } from "../src/mapping-backend";
import { mappingConsoleAdapter, mappingGuard } from "../src/mapping-backend";

describe("dedicated managed-mapping console", () => {
  let root: string, app: FastifyInstance, token: string;
  let state: MappingBackendState, backend: MappingBackend;

  beforeEach(async () => {
    root = await mkdtemp(path.join(tmpdir(), "lingxi-console-"));
    await mkdir(path.join(root, "maps"));
    state = { backend: "glim", inputMode: "live", phase: "idle", session: null,
      mapping: false, cleanupComplete: true };
    backend = {
      state: vi.fn(async () => state),
      start: vi.fn(async () => {
        state = { ...state, phase: "requested", mapping: true, cleanupComplete: false, session: "b".repeat(32) };
        return state;
      }),
      finish: vi.fn(),
    };
    app = await buildServer({
      demoMode: true, mappingConsole: true, mappingBackend: backend,
      pairingCodeHash: pairingCodeHash("isolated-console-test"),
      savedSlamDirectory: path.join(root, "maps"), stateDirectory: path.join(root, "data"),
      staticDirectory: path.join(root, "absent"),
    });
    token = (await app.inject({ method: "POST", url: "/api/v1/session/pair",
      payload: { pairingCode: "isolated-console-test" } })).json().token;
  });
  afterEach(async () => { await app?.close(); await rm(root, { recursive: true, force: true }); });

  it("exposes separate start readiness without vendor, cloud or demo data", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Unexpected external call"));
    try {
      const result = (await app.inject({ url: "/api/v1/state",
        headers: { authorization: `Bearer ${token}` } })).json();
      expect(result).toMatchObject({ demoMode: false, mappingControlReady: true, mappingInputMode: "live",
        maps: [], points: [], tours: [], savedSlamMaps: [], motionPresets: [], expressions: [] });
      for (const id of ["aimdk", "lidar", "odom", "navigation", "linksoul"]) {
        expect(result.checks.find((item: { id: string }) => item.id === id).ready).toBe(false);
      }
      expect(fetch).not.toHaveBeenCalled();
    } finally { fetch.mockRestore(); }
  });

  it("derives console readiness and session from one authoritative snapshot", async () => {
    const stateReader = vi.fn(async () => state);
    const guarded = mappingGuard(mappingConsoleAdapter(), { ...backend, state: stateReader }, true);
    expect(await guarded.getSnapshot()).toMatchObject({
      mappingControlReady: true, mapping: false, mappingStatus: "idle",
    });
    expect(stateReader).toHaveBeenCalledOnce();
  });

  it("requires pairing then accepts start independently of vendor SLAM", async () => {
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start" })).statusCode).toBe(401);
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start",
      headers: { authorization: `Bearer ${token}` } })).statusCode).toBe(202);
    expect(backend.start).toHaveBeenCalledOnce();
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start",
      headers: { authorization: `Bearer ${token}` } })).statusCode).toBe(409);
  });

  it.each(["requested", "running", "saving", "stopping", "failed"] as const)(
    "rejects start before %s cleanup is confirmed", async (phase) => {
      state = { ...state, phase, cleanupComplete: false };
      expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start",
        headers: { authorization: `Bearer ${token}` } })).statusCode).toBe(409);
      expect(backend.start).not.toHaveBeenCalled();
    });

  it("keeps start unavailable when the service disappears", async () => {
    vi.mocked(backend.state).mockRejectedValue(new Error("Synthetic disconnect"));
    const result = (await app.inject({ url: "/api/v1/state",
      headers: { authorization: `Bearer ${token}` } })).json();
    expect(result).toMatchObject({ mapping: true, mappingControlReady: false, mappingStatus: "failed" });
    expect((await app.inject({ method: "POST", url: "/api/v1/mapping/start",
      headers: { authorization: `Bearer ${token}` } })).statusCode).toBe(409);
  });

  it("blocks business writes and the agent callback before any side effect", async () => {
    for (const [method, url] of [
      ["POST", "/api/v1/navigation/point"], ["POST", "/api/v1/navigation/control"],
      ["POST", "/api/v1/agent/events"], ["POST", "/api/v1/actions/confirm-idle"],
      ["POST", "/api/v1/maps/unknown/activate"], ["POST", "/api/v1/points"],
      ["PUT", "/api/v1/settings/ai"], ["PUT", "/api/v1/settings/credentials"],
      ["DELETE", "/api/v1/tours/unknown"],
    ] as const) {
      expect((await app.inject({ method, url, headers: { authorization: `Bearer ${token}` },
        payload: {} })).statusCode, url).toBe(403);
    }
    expect(backend.start).not.toHaveBeenCalled();
  });
});
