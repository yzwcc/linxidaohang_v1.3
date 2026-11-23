import { describe, expect, it } from "vitest";
import { lidarBounds, lidarScaleBar, lidarSeedGesture, lidarToScreen, lidarViewport, surveyTrailBounds } from "../web/src/lidar-view";

describe("cumulative map viewport", () => {
  it("uses route XY but not elapsed times and does not change the full cloud", () => {
    const points = Object.freeze([-20, 12, 1, 19, -24, 2, 1.2, 0.4, 0.5]);
    const trail = Object.freeze([0, 0, 0, 1.2, 0.4, 65, 2, 1.3, 66]);
    const route = surveyTrailBounds(trail)!;
    expect(route).toEqual({ minX: -4, maxX: 6, minY: -4, maxY: 5.3 });
    const full = lidarBounds([{ points: [...points] }]);
    expect(full.minX).toBeLessThan(-20); expect(full.maxX).toBeGreaterThan(19);
    expect(full.minY).toBeLessThan(-24); expect(full.maxY).toBeGreaterThan(12);
    expect(lidarViewport(route, 365, 550, 1, { x: 0, y: 0 }).scale)
      .toBeGreaterThan(4 * lidarViewport(full, 365, 550, 1, { x: 0, y: 0 }).scale);
  });

  it("keeps nearby features in view for stationary captures and detects a missing trail", () => {
    expect(surveyTrailBounds([10, 20, 2])).toEqual({ minX: 6, maxX: 14, minY: 16, maxY: 24 });
    expect(surveyTrailBounds([])).toBeUndefined();
    expect(surveyTrailBounds([NaN, 20, 1, 10, 20])).toBeUndefined();
  });

  it("picks the same map point and heading in both views", () => {
    const pose = { x: 1.2, y: .4 }, full = lidarBounds([{ points: [-20, 12, 1, 19, -24, 2] }]);
    for (const bounds of [full, surveyTrailBounds([0, 0, 0, pose.x, pose.y, 66])!]) {
      const view = lidarViewport(bounds, 365, 550, 1, { x: 0, y: 0 });
      const start = lidarToScreen(view, pose);
      const draft = lidarSeedGesture(view, start, { ...start, y: start.y - 30 })!;
      expect(draft.x).toBeCloseTo(pose.x); expect(draft.y).toBeCloseTo(pose.y);
      expect(draft.yaw).toBeCloseTo(Math.PI / 2);
    }
  });

  it("computes readable metric scale bars from the actual view zoom", () => {
    expect(lidarScaleBar(8, 96)).toEqual({ pixels: 80, label: "10 米" });
    expect(lidarScaleBar(40, 96)).toEqual({ pixels: 80, label: "2 米" });
    expect(lidarScaleBar(160, 96)).toEqual({ pixels: 80, label: "50 厘米" });
    expect(lidarScaleBar(0, 96)).toBeUndefined();
  });
});
