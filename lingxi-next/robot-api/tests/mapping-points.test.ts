import { readFileSync } from "node:fs";
import path from "node:path";
import { PassThrough } from "node:stream";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MappingPointSource } from "../src/mapping-points";
import type { MappingBackend, MappingBackendState } from "../src/mapping-backend";
import type { PointInputMode } from "../src/slam-points";
import type { CorrectedMapPreview } from "../src/corrected-map-preview";

const fixture = JSON.parse(readFileSync("shared/fixtures/slam-point-v1.synthetic.json", "utf8"));
const first = fixture.session as string, second = "f".repeat(32);
const name = "a".repeat(32) + ".points.sock";
const directory = path.resolve("synthetic-private-mapping");
const running = (session = first, mode: PointInputMode = "recorded"): MappingBackendState => ({
  backend: "glim", inputMode: mode, phase: "running", session, mapping: true,
  cleanupComplete: false, pointStream: { socket: name },
});
function bytes(session = first, id = 1, mode: PointInputMode = "recorded") {
  const payload = Buffer.from(fixture.hex as string, "hex");
  payload[10] = mode === "live" ? 1 : 2;
  Buffer.from(session, "hex").copy(payload, 48);
  payload.writeBigUInt64LE(BigInt(id), 24);
  payload.writeDoubleLE(1000 + id, 32);
  return payload;
}

describe("persistent mapping point consumer with isolated protocol fixtures", () => {
  let source: MappingPointSource | undefined;
  afterEach(async () => { await source?.close(); vi.useRealTimers(); });

  async function start(connect = vi.fn(async () => new PassThrough()), mode: PointInputMode = "recorded",
    readMap?: () => Promise<CorrectedMapPreview | undefined>) {
    vi.useFakeTimers();
    let state = running(first, mode);
    let unavailable = false;
    const backend = { state: vi.fn(async () => {
      if (unavailable) throw new Error("synthetic unavailable backend");
      return state;
    }) } as unknown as MappingBackend;
    source = new MappingPointSource(backend, directory, connect, () => 10, readMap);
    source.start();
    await vi.advanceTimersByTimeAsync(0);
    return { connect, state: (next: MappingBackendState) => { state = next; },
      unavailable: () => { unavailable = true; } };
  }

  it("replaces provisional scans with complete revisions while requiring fresh sensor input", async () => {
    const pipe = new PassThrough();
    let corrected: CorrectedMapPreview | undefined;
    const context = await start(vi.fn(async () => pipe), "recorded", async () => corrected);
    pipe.write(bytes());
    expect(source!.getPointEvent().type).toBe("slam-preview");
    corrected = { schemaVersion: 1, session: first, inputMode: "recorded", revision: 2, submaps: 3,
      totalPoints: 20000, matchingFactors: 2, nonAdjacentMatchingFactors: 1,
      coordinateFrame: "lingxi_glim_map", coordinateUnits: "native-raw", updatedAtMs: 100000,
      points: [1, 2, 3, 4, 5, 6] };
    await vi.advanceTimersByTimeAsync(100);
    expect(source!.getPointEvent()).toMatchObject({ type: "slam-map", revision: 2, totalPoints: 20000,
      frame: { frameId: "lingxi_glim_map", sequence: 2, points: [1, 2, 3, 4, 5, 6] } });
    corrected = { ...corrected, revision: 3, points: [7, 8, 9] };
    await vi.advanceTimersByTimeAsync(100);
    expect(source!.getPointEvent()).toMatchObject({ revision: 3, frame: { points: [7, 8, 9] } });
    context.state({ ...running(), phase: "saving" });
    await vi.advanceTimersByTimeAsync(100);
    expect(() => source!.getPointEvent()).toThrow();
    expect(pipe.destroyed).toBe(false);
  });

  it("does not expose an old map after point input ends or provenance changes", async () => {
    const pipe = new PassThrough();
    const corrected = { schemaVersion: 1, session: first, inputMode: "live", revision: 1, submaps: 1,
      totalPoints: 1, matchingFactors: 0, nonAdjacentMatchingFactors: 0,
      coordinateFrame: "lingxi_glim_map", coordinateUnits: "native-raw", updatedAtMs: 100000,
      points: [1, 2, 3] } as CorrectedMapPreview;
    await start(vi.fn(async () => pipe), "live", async () => corrected);
    pipe.write(bytes(first, 1, "live"));
    await vi.advanceTimersByTimeAsync(100);
    expect(source!.getPointEvent().type).toBe("slam-map");
    pipe.emit("end");
    expect(() => source!.getPointEvent()).toThrow();
  });

  it("follows two sessions and drains hidden finalization without restarting the API", async () => {
    const pipes: PassThrough[] = [];
    const context = await start(vi.fn(async () => {
      const pipe = new PassThrough(); pipes.push(pipe); return pipe;
    }));
    expect(context.connect).toHaveBeenCalledWith(path.join(directory, name));
    pipes[0].write(bytes());
    expect(source!.getPointEvent()).toMatchObject({
      type: "slam-preview", inputMode: "recorded", coordinateUnits: "native-raw", frame: { epoch: first },
    });
    context.state({ ...running(), phase: "saving", pointStream: undefined });
    await vi.advanceTimersByTimeAsync(100);
    expect(() => source!.getPointEvent()).toThrow();
    expect(pipes[0].destroyed).toBe(false);
    pipes[0].write(bytes(first, 2));
    context.state({ ...running(), phase: "idle", mapping: false, cleanupComplete: true, pointStream: undefined });
    await vi.advanceTimersByTimeAsync(100);
    expect(pipes[0].destroyed).toBe(true);
    context.state(running(second));
    await vi.advanceTimersByTimeAsync(100);
    expect(() => source!.getPointEvent()).toThrow();
    pipes[1].write(bytes(second));
    expect(source!.getPointEvent()).toMatchObject({ frame: { epoch: second, sequence: 1 } });
    expect(context.connect).toHaveBeenCalledTimes(2);
  });

  it("keeps live provenance and raw units across recorded-to-live replacement sessions", async () => {
    const pipes: PassThrough[] = [];
    const context = await start(vi.fn(async () => {
      const pipe = new PassThrough(); pipes.push(pipe); return pipe;
    }));
    pipes[0].write(bytes());
    expect(source!.getPointEvent()).toMatchObject({ type: "slam-preview", inputMode: "recorded" });
    context.state(running(second, "live"));
    await vi.advanceTimersByTimeAsync(100);
    expect(pipes[0].destroyed).toBe(true);
    expect(() => source!.getPointEvent()).toThrow();
    pipes[1].write(bytes(second, 1, "live"));
    expect(source!.getPointEvent()).toMatchObject({
      type: "slam-preview", inputMode: "live", coordinateUnits: "native-raw", frame: { epoch: second },
    });
    context.state({ ...running(second, "live"), phase: "saving", pointStream: undefined });
    await vi.advanceTimersByTimeAsync(100);
    expect(() => source!.getPointEvent()).toThrow();
    expect(pipes[1].destroyed).toBe(false);
    pipes[1].write(bytes(second, 2, "live"));
    context.state({ ...running(second, "live"), phase: "idle", mapping: false,
      cleanupComplete: true, pointStream: undefined });
    await vi.advanceTimersByTimeAsync(100);
    expect(pipes[1].destroyed).toBe(true);
  });

  it("rejects recorded packets in a live-only session", async () => {
    const pipe = new PassThrough(), connect = vi.fn(async () => pipe);
    await start(connect, "live");
    pipe.write(bytes(first, 1, "live"));
    expect(source!.getPointEvent()).toMatchObject({ inputMode: "live" });
    pipe.write(bytes(first, 2, "recorded"));
    await vi.advanceTimersByTimeAsync(300);
    expect(() => source!.getPointEvent()).toThrow();
    expect(connect).toHaveBeenCalledOnce();
  });

  it.each(["end", "malformed", "old-session", "wrong-mode"])("does not reconnect or display after %s", async (kind) => {
    const pipe = new PassThrough(), connect = vi.fn(async () => pipe);
    await start(connect);
    pipe.write(bytes());
    expect(source!.getPointEvent().type).toBe("slam-preview");
    if (kind === "end") pipe.emit("end");
    if (kind === "malformed") pipe.write(Buffer.alloc(64));
    if (kind === "old-session") pipe.write(bytes(second, 2));
    if (kind === "wrong-mode") { const payload = bytes(first, 2); payload[10] = 1; pipe.write(payload); }
    await vi.advanceTimersByTimeAsync(500);
    expect(() => source!.getPointEvent()).toThrow();
    expect(connect).toHaveBeenCalledOnce();
  });

  it("rejects late connection data after the authoritative session has changed", async () => {
    let release!: (pipe: PassThrough) => void;
    const connect = vi.fn(() => new Promise<PassThrough>((resolve) => { release = resolve; }));
    const context = await start(connect);
    context.state(running(second));
    const old = new PassThrough();
    release(old);
    await vi.advanceTimersByTimeAsync(0);
    expect(old.destroyed).toBe(true);
    expect(() => source!.getPointEvent()).toThrow();
    // Supply the new connection so shutdown has no unresolved fixture operation.
    await vi.advanceTimersByTimeAsync(100);
    const current = new PassThrough();
    release(current);
    await vi.advanceTimersByTimeAsync(0);
    current.write(bytes(second));
    expect(source!.getPointEvent()).toMatchObject({ frame: { epoch: second } });
  });

  it("retries an endpoint that is not created yet, then closes on backend loss", async () => {
    const pipe = new PassThrough();
    const connect = vi.fn().mockRejectedValueOnce(new Error("ENOENT")).mockResolvedValue(pipe);
    const context = await start(connect);
    await vi.advanceTimersByTimeAsync(100);
    pipe.write(bytes());
    expect(source!.getPointEvent().type).toBe("slam-preview");
    context.unavailable();
    await vi.advanceTimersByTimeAsync(300);
    expect(pipe.destroyed).toBe(true);
    expect(() => source!.getPointEvent()).toThrow();
    expect(connect).toHaveBeenCalledTimes(2);
  });

  it("rejects unsafe endpoints and clears the active source on failure or shutdown", async () => {
    const pipe = new PassThrough();
    const context = await start(vi.fn(async () => pipe));
    pipe.write(bytes());
    context.state({ ...running(), pointStream: { socket: "../untrusted.sock" } });
    await vi.advanceTimersByTimeAsync(100);
    expect(pipe.destroyed).toBe(true);
    expect(() => source!.getPointEvent()).toThrow();
    expect(context.connect).toHaveBeenCalledOnce();
    await source!.close();
    await vi.advanceTimersByTimeAsync(1000);
    expect(context.connect).toHaveBeenCalledOnce();
  });
});
