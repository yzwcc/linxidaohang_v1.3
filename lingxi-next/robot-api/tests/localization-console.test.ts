import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import path from "node:path";
import { tmpdir } from "node:os";
import type { FastifyInstance } from "fastify";
import { buildServer } from "../src/server";
import { pairingCodeHash } from "../src/auth";
import type { LocalizationBackend } from "../src/localization-backend";
import type { LocalizationState } from "../../shared/localization";
import { identityMatrix, localizationFixture } from "./fixtures/localization-state";

describe("isolated paired recorded localization console", () => {
  let root: string, app: FastifyInstance, token: string, backend: LocalizationBackend, state: LocalizationState;
  const headers = () => ({ authorization: `Bearer ${token}` });
  const payload = () => ({ binding: state.binding, expectedGeneration: state.generation });
  const seed = () => ({ ...payload(), referenceFrame: state.referenceFrame, mapFromSensor: identityMatrix });
  const options = () => ({
    demoMode: true, localizationConsole: true, localizationBackend: backend,
    stateDirectory: path.join(root, "data"), savedSlamDirectory: path.join(root, "maps"),
    staticDirectory: path.join(root, "absent"), pairingCodeHash: pairingCodeHash("isolated-localization-test"),
  });
  beforeEach(async () => {
    root = await mkdtemp(path.join(tmpdir(), "lingxi-localization-api-"));
    await mkdir(path.join(root, "maps"));
    state = localizationFixture();
    backend = {
      state: vi.fn(async () => structuredClone(state)),
      command: vi.fn(async (command) => {
        if (JSON.stringify(command.binding) !== JSON.stringify(state.binding) ||
            command.expectedGeneration !== state.generation || state.state === "stopped") {
          throw Object.assign(new Error("Session changed"), { statusCode: 409 });
        }
        state = { ...state, generation: state.generation + 1, requestPending: true,
          mapFromSensor: null, consecutiveMatches: 0,
          state: { seed: "seed_requested" as const, pause: "paused" as const, stop: "stopped" as const }[command.action] };
        return structuredClone(state);
      }),
    };
    app = await buildServer(options());
    token = (await app.inject({ method: "POST", url: "/api/v1/session/pair",
      payload: { pairingCode: "isolated-localization-test" } })).json().token;
  });
  afterEach(async () => { await app?.close(); await rm(root, { recursive: true, force: true }); });

  it("exposes no robot readiness, fake data or external calls", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Unexpected external call"));
    try {
      const result = (await app.inject({ url: "/api/v1/state", headers: headers() })).json();
      expect(result).toMatchObject({ connected: false, demoMode: false, maps: [], points: [], tours: [],
        navigation: { status: "idle" }, slamLocalization: { status: "available", snapshot: state } });
      expect(result.checks.every((item: { ready: boolean }) => !item.ready)).toBe(true);
      expect(result.pose).toBeUndefined();
      expect(fetch).not.toHaveBeenCalled();
    } finally { fetch.mockRestore(); }
  });

  it("requires pairing for reads and every control", async () => {
    expect((await app.inject({ url: "/api/v1/slam/localization" })).statusCode).toBe(401);
    for (const action of ["seed", "pause", "stop"]) {
      expect((await app.inject({ method: "POST", url: `/api/v1/slam/localization/${action}`,
        payload: action === "seed" ? seed() : payload() })).statusCode).toBe(401);
    }
    expect(backend.command).not.toHaveBeenCalled();
  });

  it("accepts a seed as pending, with no successful pose or navigation readiness", async () => {
    const body = seed();
    const result = await app.inject({ method: "POST", url: "/api/v1/slam/localization/seed",
      headers: headers(), payload: body });
    expect(result.statusCode).toBe(202);
    expect(result.json()).toMatchObject({ state: "seed_requested", requestPending: true, generation: 1,
      mapFromSensor: null, localizationReady: false, navigationReady: false });
    expect(backend.command).toHaveBeenCalledExactlyOnceWith({ action: "seed", ...body });
  });

  it.each(["pause", "stop"] as const)("revokes a tracked pose on %s acceptance", async (action) => {
    state = { ...state, state: "tracking", consecutiveMatches: 3, mapFromSensor: identityMatrix };
    const result = await app.inject({ method: "POST", url: `/api/v1/slam/localization/${action}`,
      headers: headers(), payload: payload() });
    expect(result.statusCode).toBe(202);
    expect(result.json()).toMatchObject({ state: action === "stop" ? "stopped" : "paused",
      generation: 1, requestPending: true, mapFromSensor: null });
  });

  it("passes stale conflicts once, without retry or reporting success", async () => {
    const old = seed();
    state.generation++;
    const result = await app.inject({ method: "POST", url: "/api/v1/slam/localization/seed",
      headers: headers(), payload: old });
    expect(result.statusCode).toBe(409);
    expect(result.json()).not.toHaveProperty("accepted");
    expect(backend.command).toHaveBeenCalledOnce();
    expect(state.state).toBe("unseeded");
  });

  it.each([
    { referenceFrame: 0 }, { expectedGeneration: -1 }, { referenceFrame: null },
    { binding: {} }, { extra: true }, { mapFromSensor: [[1]] },
    { mapFromSensor: [[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]] },
    { mapFromSensor: [[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]] },
    { mapFromSensor: [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 0]] },
  ])("rejects malformed seed before dispatch: %j", async (patch) => {
    expect((await app.inject({ method: "POST", url: "/api/v1/slam/localization/seed",
      headers: headers(), payload: { ...seed(), ...patch } })).statusCode).toBe(400);
    expect(backend.command).not.toHaveBeenCalled();
  });

  it.each(["generation", "binding", "pending", "state"] as const)("rejects mismatched %s acknowledgement", async (field) => {
    vi.mocked(backend.command).mockImplementation(async () => ({
      ...state, generation: field === "generation" ? 9 : 1,
      binding: { ...state.binding, session: field === "binding" ? "f".repeat(32) : state.binding.session },
      state: field === "state" ? "unseeded" : "seed_requested",
      requestPending: field !== "pending",
    }));
    expect((await app.inject({ method: "POST", url: "/api/v1/slam/localization/seed",
      headers: headers(), payload: seed() })).statusCode).toBe(503);
    expect(backend.command).toHaveBeenCalledOnce();
  });

  it.each(["outage", "invalid", "live"] as const)("clears the current pose on %s", async (mode) => {
    state = { ...state, state: "tracking", consecutiveMatches: 3, mapFromSensor: identityMatrix };
    expect((await app.inject({ url: "/api/v1/state", headers: headers() })).json()
      .slamLocalization.snapshot.mapFromSensor).toEqual(identityMatrix);
    if (mode === "outage") vi.mocked(backend.state).mockRejectedValue(new Error("Disconnected"));
    if (mode === "invalid") state.consecutiveMatches = 0;
    if (mode === "live") state.binding.input_mode = "live";
    expect((await app.inject({ url: "/api/v1/state", headers: headers() })).json()
      .slamLocalization).toEqual({ status: "unavailable" });
    expect((await app.inject({ url: "/api/v1/slam/localization", headers: headers() })).statusCode)
      .toBe(mode === "outage" ? 500 : 503);
  });

  it("rejects business writes, mapping and agent callbacks", async () => {
    for (const [method, url] of [
      ["POST", "/api/v1/navigation/point"], ["POST", "/api/v1/navigation/control"],
      ["POST", "/api/v1/agent/events"], ["POST", "/api/v1/mapping/start"],
      ["POST", "/api/v1/mapping/stop-save"], ["POST", "/api/v1/actions/confirm-idle"],
      ["POST", "/api/v1/points"], ["PUT", "/api/v1/settings/credentials"],
      ["DELETE", "/api/v1/tours/unknown"],
    ] as const) {
      expect((await app.inject({ method, url, headers: headers(), payload: {} })).statusCode, url).toBe(403);
    }
    expect(backend.command).not.toHaveBeenCalled();
  });

  it("rejects insecure controls even with forged forwarding headers", async () => {
    const prod = await buildServer({ ...options(), demoMode: false });
    try {
      // A development session remains valid, but does not authorize non-loopback HTTP.
      expect((await app.inject({ method: "POST", url: "/api/v1/slam/localization/seed",
        remoteAddress: "192.0.2.1", headers: { ...headers(), "x-forwarded-proto": "https" },
        payload: seed() })).statusCode).toBe(426);
      expect((await prod.inject({ method: "POST", url: "/api/v1/session/pair",
        payload: { pairingCode: "isolated-localization-test" },
        headers: { "x-forwarded-proto": "https" } })).statusCode).toBe(426);
      expect(backend.command).not.toHaveBeenCalled();
    } finally { await prod.close(); }
  });

  it("requires explicit isolation and a saved map catalog", async () => {
    await expect(buildServer({ ...options(), localizationConsole: false })).rejects.toThrow("isolated_localization");
    await expect(buildServer({ ...options(), savedSlamDirectory: undefined })).rejects.toThrow("saved_slam_directory");
    await expect(buildServer({ ...options(), agentSpeaker: { speak: async () => ({ firstAudioMs: 1 }) } }))
      .rejects.toThrow("isolated_localization");
  });
});
