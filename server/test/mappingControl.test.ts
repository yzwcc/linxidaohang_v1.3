import { describe, expect, it } from "vitest";
import { mapSaveScript, mappingCommandScript } from "../src/mappingControl.js";

describe("AIMDK mapping adapter", () => {
  it("uses the SDK service and protects navigation map output", () => {
    const script = mapSaveScript();
    expect(script).toContain("/aimdk_5Fmsgs/srv/GetStoredMapByName");
    expect(script).toContain("GetStoredMapByName.Request()");
    expect(script).toContain("width * height != len(result.data)");
    expect(script).toContain("带旋转的地图原点");
    expect(script).toContain("os.rename(tmp, target)");
    expect(script).toContain("reused_existing_map");
    expect(script).toContain("grid_map_info.txt");
  });

  it("publishes the SDK command with transient-local reliable QoS and waits for a subscriber", () => {
    const script = mappingCommandScript();
    expect(script).toContain("QoSDurabilityPolicy.TRANSIENT_LOCAL");
    expect(script).toContain("QoSReliabilityPolicy.RELIABLE");
    expect(script).toContain("publisher.get_subscription_count()");
    expect(script).toContain("/integrated_command");
  });
});
