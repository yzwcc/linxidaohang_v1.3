import { describe, expect, it } from "vitest";
import { emptySeedFields, planarLidarPose, planarSeedDraft, seedDraftReady, seedMatrix, seedFieldsFromMatrix, seedReferenceFields,
  withPlanarSeed, type SeedFields } from "../web/src/localization-view";
import { rigidMatrixSchema } from "../shared/localization";
import type { SavedSlamPointPreview } from "../shared/contracts";
import { localizationFixture } from "../robot-api/tests/fixtures/localization-state";
import type { SavedSlamReferences } from "../shared/slam-reference";

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
  it.each([[7, -12, 93], [90, 90, 45], [20, -90, -70], [-110, 89.999, 135], [30, 110, -60], [0, 0, 0]])(
    "roundtrips full 3D orientation including gimbal cases (%i, %i, %i)", (roll, pitch, yaw) => {
      const matrix = seedMatrix({ ...zero, x: "1.5", y: "-2", z: ".8", roll: String(roll), pitch: String(pitch), yaw: String(yaw) });
      const actual = seedMatrix(seedFieldsFromMatrix(matrix));
      for (let row = 0; row < 4; row++) for (let column = 0; column < 4; column++) {
        expect(actual[row][column]).toBeCloseTo(matrix[row][column], 10);
      }
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

  it("loads full reference poses only from matching preview, profile and source metadata", () => {
    const first = seedMatrix({ ...zero, z: "1.3", roll: "12", pitch: "-7", yaw: "90" });
    const references: SavedSlamReferences = { source: "glim-final-lidar-trajectory", sourceFrame: "lidar_chest_front",
      coordinateUnits: "native-raw", trajectorySha256: "a".repeat(64), profileSha256: state.binding.profile_sha256,
      frames: 2, first: { frameIndex: 1, mapFromSensor: first }, last: { frameIndex: 2, mapFromSensor: seedMatrix(zero) } };
    const value = { ...preview, seedReferences: references };
    expect(seedReferenceFields(value, state.binding, "first")).toEqual(seedFieldsFromMatrix(first));
    expect(seedReferenceFields(value, state.binding, "last")).toEqual(zero);
    for (const key of ["mapId", "receiptSha256", "pointsSha256", "inputMode"] as const) {
      expect(seedReferenceFields({ ...value, [key]: "changed" }, state.binding, "first")).toBeUndefined();
    }
    for (const change of [{ profileSha256: "b".repeat(64) }, { sourceFrame: "base_link" },
      { frames: 3 }, { source: "odom_lidar" }]) {
      expect(seedReferenceFields({ ...value, seedReferences: { ...references, ...change } } as typeof value,
        state.binding, "first")).toBeUndefined();
    }
    expect(seedReferenceFields(preview, state.binding, "first")).toBeUndefined();
    expect(seedReferenceFields(value, undefined, "first")).toBeUndefined();
  });
});
