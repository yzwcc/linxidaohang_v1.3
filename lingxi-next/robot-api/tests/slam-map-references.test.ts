import { describe, expect, it } from "vitest";
import { mappingEndpointPoses, MAX_TRAJECTORY_BYTES } from "../src/slam-map-references";

describe("final GLIM LiDAR mapping references", () => {
  const first = "1793410000.000000001 1 -2 0.5 0 0 0 1";
  const last = "1793410000.000000002 3 -4 1.5 0 0 0.707107 0.707107";
  it("uses XYZW, map-from-lidar direction and normalized writer roundoff", () => {
    const value = mappingEndpointPoses(`${first}\n${last}\n`, 2);
    expect(value.first.frameIndex).toBe(1); expect(value.last.frameIndex).toBe(2);
    expect(value.first.mapFromSensor).toEqual([[1, 0, 0, 1], [0, 1, 0, -2], [0, 0, 1, .5], [0, 0, 0, 1]]);
    const matrix = value.last.mapFromSensor;
    expect(matrix[0][1]).toBeCloseTo(-1, 12); expect(matrix[1][0]).toBeCloseTo(1, 12);
    expect([matrix[0][3], matrix[1][3], matrix[2][3]]).toEqual([3, -4, 1.5]);
  });
  it("permits one saved frame and CRLF without guessing another reference", () => {
    const value = mappingEndpointPoses(`${first}\r\n`, 1);
    expect(value.first).toEqual(value.last);
  });
  it.each([
    ["", 1], [first, 2], [`${first}\n${last}`, 1], [`${last}\n${first}`, 2], [`${first}\n${first}`, 2],
    [`${first}\n\n${last}`, 3], [first.replace("1 -2", "NaN -2"), 1],
    [first.replace("1 -2", "Infinity -2"), 1], [first.replace("1 -2", "1e999 -2"), 1],
    [first.replace("0 0 0 1", "0 0 0 0"), 1], [first.replace("0 0 0 1", "0 0 0 1.001"), 1],
    [first + " 42", 1], [first.replace(".000000001", ".0000000001"), 1],
    [first.replace("1793410000", "-1"), 1], [first, 0], [first, 100_001], [first, 1.5],
  ] as const)("rejects incomplete or inconsistent trajectory %#", (text, count) => {
    expect(() => mappingEndpointPoses(text, count)).toThrow();
  });
  it("bounds input before parsing pose rows", () => {
    expect(() => mappingEndpointPoses(" ".repeat(MAX_TRAJECTORY_BYTES + 1), 1)).toThrow("bounded_mapping_trajectory");
  });
});
