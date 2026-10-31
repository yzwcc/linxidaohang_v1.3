import { describe, expect, it } from "vitest";
import { emptySeedFields, planarLidarPose, planarSeedDraft, seedDraftReady, seedMatrix,
  withPlanarSeed, type SeedFields } from "../web/src/localization-view";
import { rigidMatrixSchema } from "../shared/localization";
import type { SavedSlamPointPreview } from "../shared/contracts";
import { localizationFixture } from "../robot-api/tests/fixtures/localization-state";

const zero: SeedFields = { x: "0", y: "0", z: "0", roll: "0", pitch: "0", yaw: "0" };
describe("explicit chest lidar seed convention", () => {
  it("places translation in the map frame and yaw about positive Z", () => {
    const matrix = seedMatrix({ ...zero, x: "2.5", y: "-3", z: "1", yaw: "90" });
    expect(matrix[0][0]).toBeCloseTo(0);
    expect(matrix[1][0]).toBeCloseTo(1);
    expect(matrix[0][1]).toBeCloseTo(-1);
    expect(matrix[2][3]).toBe(1);
    expect(planarLidarPose(matrix)).toEqual({ x: 2.5, y: -3, yaw: Math.PI / 2 });
  });
  it("applies roll then pitch then yaw, preserving a proper rigid matrix", () => {
    const matrix = seedMatrix({ ...zero, roll: "90", pitch: "90", yaw: "90" });
    expect(matrix[2][0]).toBeCloseTo(-1);
    expect(matrix[1][2]).toBeCloseTo(0);
    expect(matrix[0][2]).toBeCloseTo(1);
    expect(rigidMatrixSchema.safeParse(matrix).success).toBe(true);
  });
  it.each(["", " ", "NaN", "Infinity", "1e999"])("rejects invalid numeric entry %j", (text) => {
    expect(() => seedMatrix({ ...zero, x: text })).toThrow();
  });
});

describe("map-bound initial pose drafts", () => {
  const state = localizationFixture();
  const preview: SavedSlamPointPreview = { mapId: state.binding.map_id,
    receiptSha256: state.binding.receipt_sha256, pointsSha256: state.binding.points_sha256,
    inputMode: "recorded", coordinateUnits: "native-raw", totalPoints: 1, sampled: false, points: [0, 0, 0] };

  it("starts blank and requires all six explicitly entered pose values", () => {
    expect(Object.values(emptySeedFields())).toEqual(["", "", "", "", "", ""]);
    expect(planarSeedDraft(emptySeedFields())).toBeUndefined();
    expect(seedDraftReady(emptySeedFields(), state.binding, state, preview)).toBe(false);
    expect(seedDraftReady(zero, undefined, state, preview)).toBe(false);
    expect(seedDraftReady(zero, state.binding, state, preview)).toBe(true);
    for (const key of Object.keys(zero)) {
      expect(seedDraftReady({ ...zero, [key]: "" }, state.binding, state, preview)).toBe(false);
    }
  });

  it("picks XY and heading without assuming height, roll or pitch", () => {
    const fields = withPlanarSeed({ ...emptySeedFields(), z: "1.37", roll: "7.5", pitch: "-12" },
      { x: 2.75, y: -1.25, yaw: Math.PI / 2 });
    expect(fields).toEqual({ x: "2.75", y: "-1.25", yaw: "90", z: "1.37", roll: "7.5", pitch: "-12" });
    expect(planarSeedDraft(fields)).toEqual({ x: 2.75, y: -1.25, yaw: Math.PI / 2 });
    const partial = withPlanarSeed(emptySeedFields(), { x: 0, y: 0, yaw: 0 });
    expect(seedDraftReady(partial, state.binding, state, preview)).toBe(false);
    expect(() => withPlanarSeed(zero, { x: NaN, y: 0, yaw: 0 })).toThrow();
  });

  it("rejects drafts from another map, receipt, profile, points or native session", () => {
    for (const key of ["session", "map_id", "receipt_sha256", "points_sha256", "profile_sha256", "input_mode"] as const) {
      expect(seedDraftReady(zero, { ...state.binding, [key]: "changed" }, state, preview)).toBe(false);
    }
    for (const key of ["mapId", "receiptSha256", "pointsSha256", "inputMode"] as const) {
      expect(seedDraftReady(zero, state.binding, state, { ...preview, [key]: "changed" })).toBe(false);
    }
    expect(seedDraftReady(zero, state.binding, undefined, preview)).toBe(false);
    expect(seedDraftReady(zero, state.binding, state, undefined)).toBe(false);
  });

  it("does not submit against a pending, closed, stopped or unavailable worker", () => {
    for (const changed of [{ workerReady: false }, { workerClosed: true }, { requestPending: true },
      { referenceFrame: null }, { state: "stopped" as const }, { state: "failed" as const }]) {
      expect(seedDraftReady(zero, state.binding, { ...state, ...changed }, preview)).toBe(false);
    }
  });
});
