import { describe, expect, it } from "vitest";
import { lidarFromScreen, lidarSeedGesture, lidarToScreen, lidarViewport } from "../web/src/lidar-view";

const bounds = { minX: -12, maxX: 20, minY: -8, maxY: 16 };
describe("point-cloud position and heading picking", () => {
  it.each([[1100, 620, 1, 0, 0], [410, 220, 2.5, 33, -19], [800, 400, .5, -70, 28]])(
    "inverts the actual aspect, zoom and pan transform (%i x %i)", (width, height, zoom, x, y) => {
      const view = lidarViewport(bounds, width, height, zoom, { x, y });
      for (const point of [{ x: -4, y: 6 }, { x: 0, y: 0 }, { x: 17, y: -3 }]) {
        const result = lidarFromScreen(view, lidarToScreen(view, point));
        expect(result.x).toBeCloseTo(point.x, 10); expect(result.y).toBeCloseTo(point.y, 10);
      }
    });

  it.each([[40, 0, 0], [0, -40, Math.PI / 2], [-40, 0, Math.PI], [0, 40, -Math.PI / 2]])(
    "keeps positive map Y upward for heading (%i, %i)", (dx, dy, yaw) => {
      const view = lidarViewport(bounds, 840, 390, 1.6, { x: 37, y: -21 });
      const start = { x: 300, y: 200 }, expected = lidarFromScreen(view, start);
      expect(lidarSeedGesture(view, start, { x: start.x + dx, y: start.y + dy })).toEqual({ ...expected, yaw });
    });

  it("does not turn a click, cancelled-length drag or release outside the canvas into a seed", () => {
    const view = lidarViewport(bounds, 640, 360, 1, { x: 0, y: 0 });
    const start = { x: 100, y: 100 };
    for (const end of [start, { x: 108, y: 108 }, { x: -1, y: 100 }, { x: 641, y: 100 },
      { x: 100, y: 361 }, { x: NaN, y: 100 }]) expect(lidarSeedGesture(view, start, end)).toBeUndefined();
    expect(lidarSeedGesture(view, { x: -1, y: 100 }, start)).toBeUndefined();
  });

  it("rejects invalid viewports before deriving a pose", () => {
    for (const width of [0, 48, Infinity, NaN]) {
      expect(() => lidarViewport(bounds, width, 360, 1, { x: 0, y: 0 })).toThrow();
    }
    expect(() => lidarViewport({ ...bounds, maxX: bounds.minX }, 640, 360, 1, { x: 0, y: 0 })).toThrow();
    expect(() => lidarViewport(bounds, 640, 360, 0, { x: 0, y: 0 })).toThrow();
  });
});
