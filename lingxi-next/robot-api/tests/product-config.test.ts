import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";
import { readProductConfiguration } from "../../robot-runtime/src/product-config";

describe("explicit private product configuration", () => {
  let root: string, file: string, state: string, token: string;
  const save = (changes = {}) => {
    writeFileSync(file, JSON.stringify({ schemaVersion: 1, stateDirectory: state,
      agentUrl: "http://127.0.0.1:18180", agentTokenFile: token, ...changes }), { mode: 0o600 });
    return createHash("sha256").update(readFileSync(file)).digest("hex");
  };
  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), "lx-product-config-"));
    file = path.join(root, "config.json"); state = path.join(root, "state"); token = path.join(root, "agent-token");
    mkdirSync(state, { mode: 0o700 });
    writeFileSync(token, "synthetic-agent-token-not-a-real-secret\n", { mode: 0o600 });
  });
  afterEach(() => rmSync(root, { recursive: true, force: true }));
  it("reads a hash-bound configuration with separate persistent state and private token", () => {
    expect(readProductConfiguration(file, save(), [])).toEqual({ stateDirectory: state,
      agentUrl: "http://127.0.0.1:18180", internalAgentToken: "synthetic-agent-token-not-a-real-secret" });
  });
  it("rejects changed bytes and unexpected fields", () => {
    const hash = save(); save({ agentUrl: "http://127.0.0.1:18181" });
    expect(() => readProductConfiguration(file, hash, [])).toThrow("unchanged_product");
    expect(() => readProductConfiguration(file, save({ apiKey: "not-allowed" }), [])).toThrow();
  });
  it.each(["https://example.invalid", "http://localhost:18180", "http://127.0.0.1:65536",
    "http://127.0.0.1:018180", "http://127.0.0.1:18180/path", "http://user@127.0.0.1:18180"])(
    "rejects an unreviewed Agent endpoint %s", (agentUrl) => {
      expect(() => readProductConfiguration(file, save({ agentUrl }), [])).toThrow();
    });
  it("rejects overlap with runtime, catalog and secret paths", () => {
    for (const excluded of [root, state, path.join(state, "nested"), token, file]) {
      expect(() => readProductConfiguration(file, save(), [excluded])).toThrow("separate_product");
    }
  });
  it("rejects invalid or oversized tokens", () => {
    for (const value of ["short", "a".repeat(4100), "a".repeat(32) + "\nextra", "a".repeat(31) + " "]) {
      writeFileSync(token, value);
      expect(() => readProductConfiguration(file, save(), [])).toThrow();
    }
  });
  it.skipIf(process.platform !== "linux")("requires private Linux owner permissions", () => {
    chmodSync(token, 0o644);
    expect(() => readProductConfiguration(file, save(), [])).toThrow("private_product_path");
  });
});
