import { describe, expect, it } from "vitest";
import { identityMatrix, localizationFixture } from "../robot-api/tests/fixtures/localization-state";
import { advanceRoute, observeRoute, routeMetrics, routeSample, type RouteCheck } from "../web/src/route-check";
import type { LocalizationState, RigidMatrix } from "../shared/localization";

function live(frame = 12): LocalizationState {
  const state = localizationFixture();
  return { ...state, binding: { ...state.binding, input_mode: "live" }, state: "tracking",
    reason: "continuous_matches", consecutiveMatches: 3, lastFrame: frame,
    mapFromSensor: structuredClone(identityMatrix) };
}
const sample = (state = live()) => routeSample(state, 1000, 1000, new Date("2026-11-09T00:00:00Z"))!;
const start = (): RouteCheck => ({ runtimeKey: "owned-run", status: "recording", origin: sample() });
function pose(x: number, y: number, yaw = 0): RigidMatrix {
  const a = yaw * Math.PI / 180;
  return [[Math.cos(a), -Math.sin(a), 0, x], [Math.sin(a), Math.cos(a), 0, y], [0, 0, 1, 0], [0, 0, 0, 1]];
}

describe("live route checkpoints", () => {
  it("accepts only fresh live tracking and copies the sensor matrix", () => {
    const state = live(), captured = sample(state);
    expect(captured.frame).toBe(12);
    state.mapFromSensor![0][3] = 5;
    expect(captured.lidar[0][3]).toBe(0);
    for (const time of [999, 1901, NaN, Infinity]) expect(routeSample(state, 1000, time)).toBeUndefined();
    expect(routeSample(state, NaN, 1000)).toBeUndefined();
    expect(routeSample(localizationFixture(), 1000, 1000)).toBeUndefined();
    for (const change of [{ state: "lost" }, { lastFrame: 0 }, { mapFromSensor: null },
      { workerClosed: true }, { requestPending: true }, { consecutiveMatches: 2 },
      { binding: { ...state.binding, input_mode: "recorded" } }]) {
      expect(routeSample({ ...state, ...change } as LocalizationState, 1000, 1000)).toBeUndefined();
    }
  });
  it("requires a new frame at each checkpoint and preserves completed evidence", () => {
    const a = start();
    expect(() => advanceRoute(a, sample(), "owned-run")).toThrow(/新的定位帧/);
    const b = advanceRoute(a, sample(live(13)), "owned-run");
    expect(b.status).toBe("recording");
    const c = advanceRoute(b, sample(live(14)), "owned-run");
    expect(c.status).toBe("complete");
    expect(observeRoute(c, undefined, "next-run")).toBe(c);
  });
  it("invalidates map/session, generation, service run and stream regression", () => {
    const a = start();
    for (const [candidate, key] of [[undefined, "owned-run"], [sample(), "next-run"],
      [{ ...sample(), generation: 1 }, "owned-run"],
      [{ ...sample(), binding: { ...sample().binding, receipt_sha256: "f".repeat(64) } }, "owned-run"],
      [{ ...sample(), binding: { ...sample().binding, session: "f".repeat(32) } }, "owned-run"]] as const) {
      const stopped = observeRoute(a, candidate, key);
      expect(stopped.status).toBe("interrupted");
      expect(observeRoute(stopped, sample(live(20)), "owned-run")).toBe(stopped);
      expect(() => advanceRoute(stopped, sample(live(20)), "owned-run")).toThrow();
    }
    const observed = observeRoute(a, sample(live(30)), "owned-run");
    expect(observeRoute(observed, sample(live(29)), "owned-run").status).toBe("interrupted");
  });
  it("reports displacement and closure without applying physical calibration", () => {
    const a = start(); a.origin.lidar = pose(0, 0, 179);
    const b = { ...sample(live(13)), lidar: pose(3, 0) };
    const c = { ...sample(live(14)), lidar: pose(.03, .04, -179) };
    const completed = advanceRoute(advanceRoute(a, b, "owned-run"), c, "owned-run");
    const measured = routeMetrics(completed, 2.8);
    expect(measured.outwardRaw).toBe(3);
    expect(measured.closureRaw).toBeCloseTo(.05);
    expect(measured.rotationDegrees).toBeCloseTo(2);
    expect(measured.measuredMetresPerRawUnit).toBeCloseTo(2.8 / 3);
    expect(measured.outwardBaseMetres).toBeUndefined();
    expect(measured.physicalScaleValidated).toBe(false);
    expect(measured.navigationReady).toBe(false);
    for (const invalid of [undefined, 0, 51, NaN, Infinity]) {
      expect(routeMetrics(completed, invalid).measuredMetresPerRawUnit).toBeUndefined();
    }
  });
  it("compares diagnostic base poses only in the same declared coordinate frame", () => {
    const a = start(), b = sample(live(13)), c = sample(live(14));
    for (const [s, p] of [[a.origin, pose(0, 0, 179)], [b, pose(3, 0)], [c, pose(.03, .04, -179)]] as const) {
      s.base = { status: "available", mapFromBase: p, lastFrame: s.frame, scanHeaderNs: "1", effectiveScanNs: "1",
        metresPerMapUnit: 1, coordinateFrame: "lingxi_saved_map", bodyFrame: "base_link",
        physicalScaleValidated: false, navigationReady: false };
    }
    const completed = advanceRoute(advanceRoute(a, b, "owned-run"), c, "owned-run");
    expect(routeMetrics(completed).outwardBaseMetres).toBe(3);
    expect(routeMetrics(completed).closureBaseMetres).toBeCloseTo(.05);
    expect(routeMetrics(completed).headingBaseDegrees).toBeCloseTo(2);
    c.base!.metresPerMapUnit = .9;
    expect(routeMetrics(completed).outwardBaseMetres).toBeUndefined();
  });
});
