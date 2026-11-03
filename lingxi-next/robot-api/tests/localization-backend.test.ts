import { afterEach, describe, expect, it, vi } from "vitest";
import { createServer, type Server, type Socket } from "node:net";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { UnixLocalizationServiceBackend, validatedLocalizationState, localizationAllowsFaceExpression,
  type LocalizationBackend } from "../src/localization-backend";
import { localizationStateSchema, type LocalizationState, type LocalizationRuntime } from "../../shared/localization";
import { identityMatrix, localizationFixture, localizationRuntimeFixture } from "./fixtures/localization-state";

describe("localization state validation", () => {
  it("accepts raw chest lidar tracking without claiming production readiness", () => {
    expect(validatedLocalizationState({ ...localizationFixture(), state: "tracking",
      consecutiveMatches: 3, mapFromSensor: identityMatrix }).navigationReady).toBe(false);
  });
  it("accepts live diagnostic input but rejects synthetic input", () => {
    const state = localizationFixture();
    expect(validatedLocalizationState({ ...state, binding: { ...state.binding, input_mode: "live" } })
      .binding.input_mode).toBe("live");
    expect(() => validatedLocalizationState({ ...state,
      binding: { ...state.binding, input_mode: "synthetic" } })).toThrow();
  });
  it.each([
    { state: "paused", mapFromSensor: identityMatrix }, { cleanupComplete: true },
    { workerClosed: true }, { state: "tracking", consecutiveMatches: 3 },
    { localizationReady: true }, { navigationReady: true }, { sourceFrame: "base_link" },
    { coordinateUnits: "metres" }, { sourceFrame: "IMU" }, { sourceFrame: undefined },
  ])("rejects inconsistent state %j", (patch) => {
    expect(localizationStateSchema.safeParse({ ...localizationFixture(), ...patch }).success).toBe(false);
  });
});

describe("localization face-display admission", () => {
  const running = (): LocalizationRuntime => ({ ...localizationRuntimeFixture(), phase: "running", generation: 3,
    runId: "2".repeat(32), selectedMap: { mapId: localizationFixture().binding.map_id,
      receiptSha256: localizationFixture().binding.receipt_sha256 }, cleanupComplete: false });
  const tracking = (): LocalizationState => ({ ...localizationFixture(), state: "tracking", generation: 7,
    consecutiveMatches: 3, mapFromSensor: identityMatrix });
  function backend(): LocalizationBackend {
    return { state: vi.fn(async () => tracking()), runtime: vi.fn(async () => running()), command: vi.fn() };
  }
  it("allows validated tracking without conflating worker reset and process generations", async () => {
    const service = backend();
    expect(await localizationAllowsFaceExpression(service)).toBe(true);
    expect(service.runtime).toHaveBeenCalledTimes(2);
    expect(service.command).not.toHaveBeenCalled();
  });
  it.each(["unseeded", "seed_requested", "acquiring", "lost", "paused", "stopped", "failed"] as const)(
    "refuses worker %s without any writes", async (state) => {
      const service = backend();
      vi.mocked(service.state).mockResolvedValue({ ...tracking(), state, mapFromSensor: null });
      expect(await localizationAllowsFaceExpression(service)).toBe(false);
      expect(service.command).not.toHaveBeenCalled();
    });
  it.each([
    { requestPending: true }, { workerClosed: true }, { workerReady: false }, { cleanupComplete: true },
    { consecutiveMatches: 2 }, { mapFromSensor: null },
  ])("rejects inconsistent tracking %j", async (patch) => {
    const service = backend();
    vi.mocked(service.state).mockResolvedValue({ ...tracking(), ...patch });
    expect(await localizationAllowsFaceExpression(service)).toBe(false);
  });
  it.each([
    { map_id: `slam-${"9".repeat(32)}` }, { receipt_sha256: "9".repeat(64) }, { input_mode: "live" as const },
  ])("rejects a different selected map identity %j", async (patch) => {
    const service = backend(), value = tracking();
    vi.mocked(service.state).mockResolvedValue({ ...value, binding: { ...value.binding, ...patch } });
    expect(await localizationAllowsFaceExpression(service)).toBe(false);
  });
  it.each([
    { generation: 4 }, { runId: "3".repeat(32) }, { serviceId: "3".repeat(32) }, { closed: true },
    { phase: "stopping" as const }, { phase: "starting" as const },
  ])("rejects a runtime change while reading tracker state %j", async (patch) => {
    const service = backend();
    vi.mocked(service.runtime!).mockResolvedValueOnce(running()).mockResolvedValue({ ...running(), ...patch });
    expect(await localizationAllowsFaceExpression(service)).toBe(false);
  });
  it.each(["before", "state", "after", "missing"])("fails closed for an unavailable %s observation", async (stage) => {
    const service = backend();
    const error = new Error("synthetic-unavailable");
    if (stage === "missing") service.runtime = undefined;
    else if (stage === "state") vi.mocked(service.state).mockRejectedValue(error);
    else if (stage === "before") vi.mocked(service.runtime!).mockRejectedValue(error);
    else vi.mocked(service.runtime!).mockResolvedValueOnce(running()).mockRejectedValue(error);
    expect(await localizationAllowsFaceExpression(service)).toBe(false);
  });
});

describe.skipIf(process.platform !== "linux")("bounded Unix localization transport", () => {
  let server: Server | undefined, root: string | undefined;
  const clients = new Set<Socket>();
  afterEach(async () => {
    for (const socket of clients) socket.destroy();
    clients.clear();
    if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
    server = undefined;
    if (root) await rm(root, { recursive: true, force: true });
    root = undefined;
  });
  async function fixture(respond: (socket: Socket, line: string) => void) {
    root = await mkdtemp(path.join(tmpdir(), "lx-loc-"));
    const endpoint = path.join(root, "s");
    server = createServer((socket) => {
      clients.add(socket); socket.on("close", () => clients.delete(socket));
      socket.once("data", (bytes) => respond(socket, bytes.toString("utf8")));
    });
    await new Promise<void>((resolve) => server!.listen(endpoint, resolve));
    return new UnixLocalizationServiceBackend(endpoint);
  }
  it("reads service runtime independently of the child state", async () => {
    const value = localizationRuntimeFixture();
    const backend = await fixture((socket, line) => {
      expect(JSON.parse(line)).toEqual({ action: "runtime" });
      socket.end(JSON.stringify({ ok: true, value }) + "\n");
    });
    expect(await backend.runtime()).toEqual(value);
  });
  it.each(["start", "shutdown"] as const)("forwards one exact %s request with service identity", async (action) => {
    const selection = { mapId: `slam-${"b".repeat(32)}`, receiptSha256: "c".repeat(64) };
    const identity = { serviceId: "1".repeat(32), expectedGeneration: 0 };
    const command = action === "start" ? { action, ...identity, ...selection } :
      { action, ...identity, runId: "2".repeat(32) };
    let calls = 0;
    const value = { ...localizationRuntimeFixture(), generation: 1, runId: "2".repeat(32),
      selectedMap: selection, cleanupComplete: false, phase: action === "start" ? "starting" : "stopping" };
    const backend = await fixture((socket, line) => {
      calls++; expect(JSON.parse(line)).toEqual(command);
      socket.end(JSON.stringify({ ok: true, value }) + "\n");
    });
    expect(await backend.lifecycle(command)).toEqual(value);
    expect(calls).toBe(1);
  });
  it("does not retry a lifecycle request without acknowledgement", async () => {
    let calls = 0;
    const backend = await fixture((socket) => { calls++; socket.end(); });
    await expect(backend.lifecycle({ action: "shutdown", serviceId: "1".repeat(32),
      expectedGeneration: 1, runId: "2".repeat(32) })).rejects.toMatchObject({ statusCode: 503 });
    expect(calls).toBe(1);
  });
  it("sends a single exact state query", async () => {
    let count = 0;
    const backend = await fixture((socket, line) => {
      count++; expect(line).toBe('{"action":"state"}\n');
      socket.end(JSON.stringify({ ok: true, value: localizationFixture() }) + "\n");
    });
    expect(await backend.state()).toEqual(localizationFixture());
    expect(count).toBe(1);
  });
  it("forwards exact bound generation and validates the seed acknowledgement", async () => {
    const state = localizationFixture();
    const command = { action: "seed" as const, binding: state.binding, expectedGeneration: 0,
      sourceFrame: state.sourceFrame, referenceFrame: 12, mapFromSensor: identityMatrix };
    const backend = await fixture((socket, line) => {
      expect(JSON.parse(line)).toEqual(command);
      socket.end(JSON.stringify({ ok: true, value: { ...state, generation: 1,
        state: "seed_requested", requestPending: true } }) + "\n");
    });
    expect((await backend.command(command)).state).toBe("seed_requested");
  });
  it("does not retry a lost acknowledgement", async () => {
    let count = 0;
    const backend = await fixture((socket) => { count++; socket.end(); });
    const state = localizationFixture();
    await expect(backend.command({ action: "stop", binding: state.binding, expectedGeneration: 0 }))
      .rejects.toMatchObject({ statusCode: 503 });
    expect(count).toBe(1);
  });
  it.each(["malformed", "oversized", "extra", "partial", "hang", "conflict"] as const)(
    "handles %s response without exposing internal data", async (mode) => {
      const backend = await fixture((socket) => {
        if (mode === "hang") return;
        socket.end(mode === "malformed" ? "secret-not-json\n" : mode === "oversized" ? "x".repeat(9000) :
          mode === "partial" ? '{"ok":true' : mode === "conflict"
            ? JSON.stringify({ ok: false, code: "localization_generation_changed", status: 409 }) + "\n"
            : JSON.stringify({ ok: true, value: localizationFixture() }) + "\n{}\n");
      });
      await expect(backend.state()).rejects.toMatchObject({ statusCode: mode === "conflict" ? 409 : 503 });
    });
});
