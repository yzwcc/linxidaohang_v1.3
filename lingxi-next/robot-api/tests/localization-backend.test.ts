import { afterEach, describe, expect, it } from "vitest";
import { createServer, type Server, type Socket } from "node:net";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { UnixLocalizationBackend, validatedLocalizationState } from "../src/localization-backend";
import { localizationStateSchema } from "../../shared/localization";
import { identityMatrix, localizationFixture } from "./fixtures/localization-state";

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
    return new UnixLocalizationBackend(endpoint);
  }
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
