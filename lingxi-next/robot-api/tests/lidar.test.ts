import { describe, expect, it } from "vitest";
import type { LidarFrame } from "../../shared/contracts";
import { validatedLidarFrame } from "../src/lidar";
import { appendScan, lidarBounds } from "../../web/src/lidar-view";

const frame = (sequence = 1, change: Partial<LidarFrame> = {}): LidarFrame => ({
  sequence, epoch: "test", source: "point-cloud", frameId: "map", sensorFrame: "lidar",
  fixedFrame: true, stampMs: Date.now() + sequence, receivedAtMs: Date.now(),
  points: [1, 2, 0, 3, 4, 0], ...change,
});
describe("live lidar transport and rendering data", () => {
  it("rejects stale, non-finite, incomplete or oversized point payloads", () => {
    expect(validatedLidarFrame(frame()).points).toEqual([1, 2, 0, 3, 4, 0]);
    for (const change of [
      { receivedAtMs: Date.now() - 3000 }, { points: [1, Number.NaN, 0] },
      { points: [1, 2] }, { points: Array(24_003).fill(0) },
    ]) expect(() => validatedLidarFrame(frame(1, change))).toThrow();
  });
  it("only accumulates frames in one fixed coordinate system", () => {
    const first = appendScan({ key: "", frames: [] }, frame(), true);
    expect(appendScan(first, frame(2), true).frames).toHaveLength(2);
    expect(appendScan(first, frame(2, { fixedFrame: false }), true).frames).toHaveLength(1);
    expect(appendScan(first, frame(2, { epoch: "new-map" }), true).frames).toHaveLength(1);
    expect(appendScan(first, frame(2, { frameId: "odom" }), true).frames).toHaveLength(1);
    expect(appendScan(first, frame(2), false).frames).toHaveLength(1);
  });
  it("drops duplicate/late callbacks and expires history within 15 seconds", () => {
    const first = appendScan({ key: "", frames: [] }, frame(2, { stampMs: 20_000 }), true);
    expect(appendScan(first, frame(1), true)).toBe(first);
    expect(appendScan(first, frame(3, { stampMs: 10_000 }), true)).toBe(first);
    expect(appendScan(first, frame(3, { stampMs: 40_000 }), true).frames).toHaveLength(1);
  });
  it("replaces globally corrected map revisions instead of stacking old geometry", () => {
    const first = appendScan({ key: "", frames: [] }, frame(1, { frameId: "lingxi_glim_map" }), true);
    const corrected = frame(2, { frameId: "lingxi_glim_map", points: [8, 9, 0] });
    expect(appendScan(first, corrected, true).frames).toEqual([corrected]);
  });
  it("bounds retained point count and computes metres from actual coordinates", () => {
    let history = { key: "", frames: [] } as ReturnType<typeof appendScan>;
    for (let index = 0; index < 100; index += 1) history = appendScan(history, frame(index, { points: Array(24_000).fill(1) }), true);
    expect(history.frames.reduce((count, item) => count + item.points.length / 3, 0)).toBeLessThanOrEqual(50_000);
    const bounds = lidarBounds([frame()]);
    expect(bounds.minX).toBeLessThan(1); expect(bounds.maxY).toBeGreaterThan(4);
  });
});
