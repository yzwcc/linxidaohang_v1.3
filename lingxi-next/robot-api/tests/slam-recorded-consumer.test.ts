import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import { describe, expect, it } from "vitest";
import { RecordedPointAudit, verifyRecordedApi } from "../../tools/slam-point-api-consumer";

// Explicitly isolated synthetic fixture exercising the recorded, not live, route.
const fixture = JSON.parse(readFileSync("shared/fixtures/slam-point-v1.synthetic.json", "utf8"));
const record = (id = 1) => {
  const bytes = Buffer.from(fixture.hex as string, "hex");
  bytes[10] = 2;
  bytes.writeBigUInt64LE(BigInt(id), 24);
  bytes.writeDoubleLE(1000 + id, 32);
  return bytes;
};
describe("recorded native point audit and API guard", () => {
  it("uses the native packet-content digest without publishing point geometry", async () => {
    const pipe = new PassThrough();
    const source = new RecordedPointAudit(pipe, fixture.session, () => 10);
    const bytes = record();
    pipe.write(bytes.subarray(0, 67)); pipe.write(bytes.subarray(67));
    await expect(source.getLidarFrame()).rejects.toThrow();
    const metadata = Buffer.alloc(24);
    metadata.writeBigUInt64LE(1n, 0); metadata.writeDoubleLE(1001, 8); metadata.writeBigUInt64LE(3n, 16);
    const digest = createHash("sha256").update(metadata).update(bytes.subarray(192))
      .update(bytes.subarray(64, 192)).digest("hex");
    expect(source.report()).toEqual({
      records: 1, copiedPoints: 2, retired: 0, firstNativeFrameId: 1, lastNativeFrameId: 1,
      allPacketBytesSha256: digest, inputMode: "recorded",
    });
    source.close();
  });
  it("invalidates incomplete sequences rather than calling them complete", async () => {
    const pipe = new PassThrough();
    const source = new RecordedPointAudit(pipe, fixture.session, () => 10);
    pipe.write(record(2));
    expect(pipe.destroyed).toBe(true);
    expect(source.records).toBe(0);
    expect(() => source.offer(record(1))).toThrow();
    source.close();
  });
  it("exercises paired API rejection during replay and after EOF with no readiness change", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "lingxi-recorded-api-"));
    const pipe = new PassThrough();
    const source = new RecordedPointAudit(pipe, fixture.session, () => 10);
    let end!: (ok: boolean) => void;
    const ended = new Promise<boolean>((resolve) => { end = resolve; });
    pipe.write(record());
    const timer = setTimeout(() => { pipe.end(); end(true); }, 500);
    try {
      expect(await verifyRecordedApi(source, directory, ended)).toEqual({
        pairedWebSocketChecked: true, recordedInputRejectedAsLive: true,
        beforeAndAfterEofUnavailable: true, readinessUnchanged: true,
        robotCommandsSent: false, tcpListenerStarted: false,
      });
      expect(source.records).toBe(1);
    } finally {
      clearTimeout(timer);
      source.close();
      await rm(directory, { recursive: true, force: true });
    }
  });
  it("treats pipe failure as failure, not a normal replay completion", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "lingxi-recorded-api-failed-"));
    const pipe = new PassThrough();
    const source = new RecordedPointAudit(pipe, fixture.session, () => 10);
    pipe.write(record());
    try {
      await expect(verifyRecordedApi(source, directory, Promise.resolve(false))).rejects
        .toThrow("complete_recorded_point_pipe_eof_required");
    } finally {
      source.close();
      await rm(directory, { recursive: true, force: true });
    }
  });
});
