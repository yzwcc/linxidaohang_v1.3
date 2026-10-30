import { describe, expect, it } from "vitest";
import { basePoseStateSchema, localizationStateSchema } from "../../shared/localization";
import { identityMatrix, localizationFixture } from "./fixtures/localization-state";

const base = {
  status: "available", reason: "diagnostic_quality_only", mapFromBase: identityMatrix, lastFrame: 12,
  scanHeaderNs: "1700000000000000001", effectiveScanNs: "1700000000000000001", metresPerMapUnit: 1,
  coordinateUnits: "metres-at-declared-scale", coordinateFrame: "lingxi_saved_map", bodyFrame: "base_link",
  physicalScaleValidated: false, navigationReady: false,
};

describe("optional base diagnostic state", () => {
  it("preserves the old state contract when the option is absent", () => {
    expect(localizationStateSchema.safeParse(localizationFixture()).success).toBe(true);
  });
  it("keeps original integer timestamps as strings and requires current tracking frame", () => {
    const state = { ...localizationFixture(), state: "tracking", consecutiveMatches: 3,
      mapFromSensor: identityMatrix, basePose: base };
    expect(localizationStateSchema.parse(state).basePose?.effectiveScanNs).toBe("1700000000000000001");
    for (const changes of [{ state: "paused", mapFromSensor: null }, { lastFrame: 13 }]) {
      expect(localizationStateSchema.safeParse({ ...state, ...changes }).success).toBe(false);
    }
  });
  it("cannot retain a matrix while unavailable or promote declared scale to readiness", () => {
    for (const changes of [{ status: "unavailable" }, { physicalScaleValidated: true },
      { navigationReady: true }, { metresPerMapUnit: 0 }, { scanHeaderNs: 1700000000000000001 }]) {
      expect(basePoseStateSchema.safeParse({ ...base, ...changes }).success).toBe(false);
    }
    expect(basePoseStateSchema.safeParse({ ...base, status: "unavailable", reason: "input_closed",
      mapFromBase: null, lastFrame: 0, scanHeaderNs: null, effectiveScanNs: null }).success).toBe(true);
  });
});
