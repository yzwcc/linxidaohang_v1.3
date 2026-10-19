import { describe, expect, it } from "vitest";
import { parseTunnelIdentity } from "../src/tunnelTarget.js";

describe("tunnel target identity", () => {
  it("parses the hostname and all robot addresses", () => {
    expect(parseTunnelIdentity([
      "hostname=agi",
      "addresses=10.0.1.41 10.115.72.203 240a:42c6:c01::1"
    ].join("\n"))).toEqual({
      hostname: "agi",
      addresses: ["10.0.1.41", "10.115.72.203", "240a:42c6:c01::1"]
    });
  });

  it("returns empty identity fields for malformed output", () => {
    expect(parseTunnelIdentity("unexpected output")).toEqual({ hostname: "", addresses: [] });
  });
});
