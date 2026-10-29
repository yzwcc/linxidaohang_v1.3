import { readFileSync } from "node:fs";
import { PassThrough } from "node:stream";
import { describe, expect, it } from "vitest";
import { decodePointRecord, LatestSlamPointSource, SlamPointPipeSource } from "../src/slam-points";

// Isolated synthetic protocol fixture, never a robot sensor or acceptance result.
const fixture = JSON.parse(readFileSync("shared/fixtures/slam-point-v1.synthetic.json", "utf8"));
const session: string = fixture.session;
const golden = () => Buffer.from(fixture.hex as string, "hex");
const live = () => { const bytes = golden(); bytes[10] = 1; return bytes; };
const ordered = (id: number, stamp = 1000 + id, emitted = 10) => {
  const bytes = live();
  bytes.writeBigUInt64LE(BigInt(id), 24); bytes.writeDoubleLE(stamp, 32); bytes.writeDoubleLE(emitted, 40);
  return bytes;
};

describe("isolated corrected-point binary contract", () => {
  it("decodes the shared Python golden bytes with explicit non-base pose semantics", () => {
    expect(decodePointRecord(golden(), session, "synthetic", 10)).toEqual({
      session, inputMode: "synthetic", nativeFrameId: 1, algorithmStamp: fixture.algorithmStamp,
      emittedAtSeconds: 10, nativePointCount: 3, points: fixture.points, worldLidarPose: fixture.worldLidarPose,
    });
  });
  it("rejects malformed headers, oversized records and high-bit magic aliases", () => {
    for (const mutate of [
      (b: Buffer) => { b[0] |= 128; }, (b: Buffer) => b.writeUInt16LE(2, 8),
      (b: Buffer) => { b[10] = 1; }, (b: Buffer) => { b[11] = 1; },
      (b: Buffer) => b.writeUInt32LE(0, 12), (b: Buffer) => b.writeUInt32LE(8001, 12),
      (b: Buffer) => b.writeUInt32LE(1, 16), (b: Buffer) => b.writeUInt32LE(2_000_001, 16),
      (b: Buffer) => b.writeUInt32LE(1, 20), (b: Buffer) => b.writeBigUInt64LE(0n, 24),
      (b: Buffer) => b.writeBigUInt64LE(2n ** 53n, 24), (b: Buffer) => { b[48] = 255; },
      (b: Buffer) => b.writeDoubleLE(0, 32), (b: Buffer) => b.writeDoubleLE(1e308, 32),
      (b: Buffer) => b.writeDoubleLE(NaN, 32), (b: Buffer) => b.writeDoubleLE(Infinity, 40),
      (b: Buffer) => b.writeDoubleLE(-1, 40), (b: Buffer) => b.writeDoubleLE(NaN, 192),
    ]) {
      const bytes = golden(); mutate(bytes);
      expect(() => decodePointRecord(bytes, session, "synthetic", 10)).toThrow();
    }
    for (const bytes of [Buffer.alloc(0), golden().subarray(0, 63), golden().subarray(1),
      Buffer.concat([golden(), Buffer.alloc(1)]), Buffer.alloc(192_193)]) {
      expect(() => decodePointRecord(bytes, session, "synthetic", 10)).toThrow();
    }
    expect(() => decodePointRecord(golden(), session.toUpperCase(), "synthetic", 10)).toThrow();
    expect(() => decodePointRecord(golden(), "0".repeat(32), "synthetic", 10)).toThrow();
    expect(() => new LatestSlamPointSource(session, "unknown" as "live")).toThrow();
  });
  it("uses the same rigid rotation and bottom-row thresholds as Python", () => {
    for (const value of [1e-7, 1.001e-7]) {
      const bytes = golden(); bytes.writeDoubleLE(value, 64 + 12 * 8);
      const decode = () => decodePointRecord(bytes, session, "synthetic", 10);
      if (value === 1e-7) expect(decode).not.toThrow(); else expect(decode).toThrow();
    }
    const shear = golden();
    for (const index of [1, 2, 4, 6, 8, 9]) shear.writeDoubleLE(4e-6, 64 + index * 8);
    expect(() => decodePointRecord(shear, session, "synthetic", 10)).toThrow();
    const reflected = golden(); reflected.writeDoubleLE(-1, 64);
    expect(() => decodePointRecord(reflected, session, "synthetic", 10)).toThrow();
    const rotated = golden();
    for (const [index, value] of [[0, Math.cos(.37)], [1, -Math.sin(.37)],
      [4, Math.sin(.37)], [5, Math.cos(.37)]]) rotated.writeDoubleLE(value, 64 + index * 8);
    expect(() => decodePointRecord(rotated, session, "synthetic", 10)).not.toThrow();
  });
  it("enforces monotonic freshness rather than poll time or algorithm time", () => {
    for (const now of [9.94, 12.001, -1, Infinity, NaN]) {
      expect(() => decodePointRecord(golden(), session, "synthetic", now)).toThrow();
    }
    for (const now of [9.951, 10, 12]) {
      expect(() => decodePointRecord(golden(), session, "synthetic", now)).not.toThrow();
    }
  });
  it("accepts exactly the bounded maximum", () => {
    const bytes = Buffer.alloc(192_192);
    golden().subarray(0, 192).copy(bytes);
    bytes.writeUInt32LE(8000, 12); bytes.writeUInt32LE(8000, 16);
    expect(decodePointRecord(bytes, session, "synthetic", 10).points).toHaveLength(24_000);
  });
});

describe("latest-only visual point source", () => {
  it("retains emission age, does not expose robotPose and returns owned point arrays", async () => {
    let now = 10;
    const source = new LatestSlamPointSource(session, "live", () => now, () => 100_000);
    source.offer(live());
    now = 11;
    const frame = await source.getLidarFrame();
    expect(frame).toMatchObject({
      epoch: session, sequence: 1, frameId: "lingxi_glim_odom", fixedFrame: true, receivedAtMs: 99_000,
    });
    expect(frame.robotPose).toBeUndefined();
    frame.points[0] = 999;
    expect((await source.getLidarFrame()).points[0]).toBe(1.25);
    now = 12.001;
    await expect(source.getLidarFrame()).rejects.toThrow();
    source.close();
    await expect(source.getLidarFrame()).rejects.toThrow();
    expect(() => source.offer(ordered(2))).toThrow();
  });
  it("never serves recorded or synthetic packets as live", async () => {
    for (const [mode, id] of [["recorded", 2], ["synthetic", 3]] as const) {
      const source = new LatestSlamPointSource(session, mode, () => 10);
      const bytes = golden(); bytes[10] = id;
      expect(source.offer(bytes)).toBe(true);
      await expect(source.getLidarFrame()).rejects.toThrow();
    }
  });
  it("rejects duplicates and out-of-order fields without restoring old snapshots", async () => {
    const source = new LatestSlamPointSource(session, "live", () => 10);
    expect(source.offer(ordered(1))).toBe(true);
    expect(source.offer(ordered(3))).toBe(true); // Dropped visual frames are permitted.
    for (const bytes of [ordered(3), ordered(2), ordered(4, 1002), ordered(4, 1004, 9.99)]) {
      expect(source.offer(bytes)).toBe(false);
    }
    expect((await source.getLidarFrame()).sequence).toBe(3);
    expect(source.offer(ordered(5))).toBe(true);
  });
  it("latches an invalid record or disconnect until a new source is constructed", async () => {
    const source = new LatestSlamPointSource(session, "live", () => 10);
    source.offer(live());
    expect(() => source.offer(golden())).toThrow();
    await expect(source.getLidarFrame()).rejects.toThrow();
    expect(() => source.offer(ordered(2))).toThrow();
    source.close(); source.close();
  });
});

describe("dedicated local byte-pipe receiver", () => {
  it("decodes fragmented and concatenated records while retaining only the newest", async () => {
    const pipe = new PassThrough();
    const source = new SlamPointPipeSource(pipe, session, "live", () => 10);
    const bytes = Buffer.concat([ordered(1), ordered(2), ordered(3)]);
    for (let offset = 0; offset < bytes.length; offset += 7) pipe.write(bytes.subarray(offset, offset + 7));
    expect((await source.getLidarFrame()).sequence).toBe(3);
    pipe.write(Buffer.concat([ordered(5), ordered(4)]));
    expect((await source.getLidarFrame()).sequence).toBe(5);
    source.close();
    expect(pipe.destroyed).toBe(true);
    await expect(source.getLidarFrame()).rejects.toThrow();
  });
  it("clears the last frame on EOF, truncated EOF, invalid framing or stream errors", async () => {
    for (const terminate of [
      (pipe: PassThrough) => pipe.emit("end"),
      (pipe: PassThrough) => { pipe.write(live().subarray(0, 70)); pipe.emit("end"); },
      (pipe: PassThrough) => pipe.write(Buffer.alloc(64)),
      (pipe: PassThrough) => pipe.emit("error", new Error("isolated synthetic stream failure")),
      (pipe: PassThrough) => pipe.destroy(),
    ]) {
      const pipe = new PassThrough();
      const source = new SlamPointPipeSource(pipe, session, "live", () => 10);
      pipe.write(live());
      expect((await source.getLidarFrame()).sequence).toBe(1);
      terminate(pipe);
      await new Promise<void>((resolve) => setImmediate(resolve));
      await expect(source.getLidarFrame()).rejects.toThrow();
      expect(() => source.offer(ordered(2))).toThrow();
      source.close();
    }
  });
  it("never accepts a pipe with a previous session or a different input mode", async () => {
    for (const bytes of [golden(), (() => { const b = live(); b[48] = 255; return b; })()]) {
      const pipe = new PassThrough();
      const source = new SlamPointPipeSource(pipe, session, "live", () => 10);
      pipe.write(bytes);
      await expect(source.getLidarFrame()).rejects.toThrow();
      expect(pipe.destroyed).toBe(true);
      source.close();
    }
  });
  it("does not revive an already closed input pipe", async () => {
    const pipe = new PassThrough(); pipe.destroy();
    const source = new SlamPointPipeSource(pipe, session, "live", () => 10);
    expect(() => source.offer(live())).toThrow();
    source.close();
  });
});
