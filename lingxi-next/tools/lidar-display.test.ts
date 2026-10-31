import { describe, expect, it } from "vitest";
import type { LidarFrame } from "../shared/contracts";
import { lidarBounds, parseLidarZSlice, sliceLidarFrames } from "../web/src/lidar-view";

const frame = (points: number[]): LidarFrame => ({ sequence: 1, epoch: "synthetic", source: "point-cloud",
  frameId: "synthetic", sensorFrame: "synthetic", fixedFrame: true, stampMs: 100, receivedAtMs: 100, points });

describe("point-cloud display-only slicing and viewport", () => {
  it.each([["", "1"], ["0", ""], [" ", "2"], ["1", "1"], ["2", "1"], ["NaN", "1"], ["0", "Infinity"]])(
    "does not guess an absent or invalid Z band (%s, %s)", (min, max) => expect(parseLidarZSlice(min, max)).toBeUndefined());

  it("accepts explicit fractional or negative raw coordinates without treating them as metres", () => {
    expect(parseLidarZSlice(" -.25 ", "1.5")).toEqual({ min: -.25, max: 1.5 });
  });

  it("includes both Z boundaries and preserves the complete original cloud and metadata", () => {
    const input = frame([1, 2, -.5, 3, 4, 0, 5, 6, .5, 7, 8, 1]);
    Object.freeze(input.points); Object.freeze(input);
    const result = sliceLidarFrames([input], { min: 0, max: .5 });
    expect(result).toEqual([{ ...input, points: [3, 4, 0, 5, 6, .5] }]);
    expect(input.points).toEqual([1, 2, -.5, 3, 4, 0, 5, 6, .5, 7, 8, 1]);
    expect(result[0]).not.toBe(input);
    expect(sliceLidarFrames([input], { min: 10, max: 11 })[0].points).toEqual([]);
  });

  it("rejects invalid numeric bands instead of passing all points through", () => {
    for (const slice of [{ min: NaN, max: 1 }, { min: 1, max: Infinity }, { min: 1, max: 1 }, { min: 2, max: 1 }]) {
      expect(() => sliceLidarFrames([frame([1, 2, 3])], slice)).toThrow();
    }
  });

  it("changes only main-view bounds, retaining distant points in the source", () => {
    const points = Array.from({ length: 1000 }, (_, i) => [i % 10, Math.floor(i / 10) % 10, 0]).flat();
    points.push(1000, -1000, 1);
    const input = frame(points), all = lidarBounds([input]), main = lidarBounds([input], "main");
    expect(all.maxX).toBeGreaterThan(1000); expect(all.minY).toBeLessThan(-1000);
    expect(main.maxX).toBeLessThan(11); expect(main.minY).toBeGreaterThan(-2);
    expect(input.points.at(-3)).toBe(1000);
    expect(lidarBounds([input], "all")).toEqual(all);
  });

  it("keeps a valid extent for empty, small and coincident clouds", () => {
    for (const input of [frame([]), frame([0, 0, 0]), frame(Array.from({ length: 110 }, () => [1, 2, 3]).flat())]) {
      for (const mode of ["all", "main"] as const) {
        const bounds = lidarBounds([input], mode);
        expect(bounds.maxX).toBeGreaterThan(bounds.minX); expect(bounds.maxY).toBeGreaterThan(bounds.minY);
      }
    }
  });
});
