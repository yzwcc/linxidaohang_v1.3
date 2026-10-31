import { describe, expect, it } from "vitest";
import { lidarMeasureGesture, lidarToScreen, lidarViewport } from "../web/src/lidar-view";

const bounds = { minX: -12, maxX: 20, minY: -8, maxY: 16 };
describe("raw XY point-cloud inspection", () => {
  it.each([[1100, 620, 1, 0, 0], [410, 220, 2.5, 33, -19], [800, 400, .5, -70, 28]])(
    "keeps raw distance independent of aspect, zoom and pan (%i x %i)", (width, height, zoom, x, y) => {
      const view = lidarViewport(bounds, width, height, zoom, { x, y });
      const start = { x: 0, y: 0 }, end = { x: 3, y: 4 };
      const measurement = lidarMeasureGesture(view, lidarToScreen(view, start), lidarToScreen(view, end));
      expect(measurement?.distance).toBeCloseTo(5, 10);
      expect(measurement?.start.x).toBeCloseTo(start.x, 10);
      expect(measurement?.start.y).toBeCloseTo(start.y, 10);
      expect(measurement?.end.x).toBeCloseTo(end.x, 10);
      expect(measurement?.end.y).toBeCloseTo(end.y, 10);
    });

  it("is symmetric and preserves fractional native coordinates without converting to metres", () => {
    const view = lidarViewport(bounds, 840, 390, 1.6, { x: 37, y: -21 });
    const start = { x: 300.25, y: 200.75 }, end = { x: 360.75, y: 160.25 };
    const forward = lidarMeasureGesture(view, start, end)!, backward = lidarMeasureGesture(view, end, start)!;
    expect(forward.distance).toBeCloseTo(Math.hypot(60.5, 40.5) / view.scale, 10);
    expect(backward).toEqual({ start: forward.end, end: forward.start, distance: forward.distance });
  });

  it("does not produce a measurement for a click, tiny gesture or release outside the cloud", () => {
    const view = lidarViewport(bounds, 640, 360, 1, { x: 0, y: 0 });
    const start = { x: 100, y: 100 };
    for (const end of [start, { x: 108, y: 108 }, { x: -1, y: 100 }, { x: 641, y: 100 },
      { x: 100, y: 361 }, { x: NaN, y: 100 }, { x: Infinity, y: 100 }]) {
      expect(lidarMeasureGesture(view, start, end)).toBeUndefined();
    }
    expect(lidarMeasureGesture(view, { x: -1, y: 100 }, start)).toBeUndefined();
  });
});
