import { describe, expect, it } from "vitest";
import { planarLidarPose, seedMatrix, type SeedFields } from "../web/src/localization-view";
import { rigidMatrixSchema } from "../shared/localization";

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
