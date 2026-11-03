import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
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
  it.each([null, true, 5, "", [], {}])("rejects invalid optional navigation selection %j", (navigationSocket) => {
    expect(() => readProductConfiguration(file, save({ navigationSocket }), [])).toThrow();
  });
  it.skipIf(process.platform === "linux")("does not select a physical Linux bridge on other platforms", () => {
    expect(() => readProductConfiguration(file, save({ navigationSocket: "/tmp/nav/bridge.sock" }), [])).toThrow();
  });
  it.skipIf(process.platform !== "linux")("selects only an explicit socket in a separate private parent", () => {
    const parent = path.join(root, "navigation"); mkdirSync(parent, { mode: 0o700 });
    const navigationSocket = path.join(parent, "bridge.sock");
    const digest = save({ navigationSocket });
    expect(readProductConfiguration(file, digest, []).navigationSocket).toBe(navigationSocket);
    for (const excluded of [root, parent, navigationSocket, path.join(parent, "native.so")]) {
      expect(() => readProductConfiguration(file, digest, [excluded])).toThrow("separate_product");
    }
    chmodSync(parent, 0o755);
    expect(() => readProductConfiguration(file, digest, [])).toThrow("private_product_path");
  });
  it.skipIf(process.platform !== "linux")("rejects overlapping, indirect and malformed socket parents", () => {
    const parent = path.join(root, "navigation"); mkdirSync(parent, { mode: 0o700 });
    const link = path.join(root, "linked"); symlinkSync(parent, link);
    for (const navigationSocket of [path.join(state, "bridge.sock"), path.join(root, "bridge.sock"),
      path.join(link, "bridge.sock"), path.join(parent, "missing", "bridge.sock"), "relative.sock",
      parent + "/./bridge.sock", parent + "/bridge.sock/", parent + "/bad\0name",
      path.join(parent, "z".repeat(100)), path.join(parent, "\u6d4b".repeat(34))]) {
      expect(() => readProductConfiguration(file, save({ navigationSocket }), [])).toThrow();
    }
  });
  it.skipIf(process.platform !== "linux")("requires an existing endpoint to be a private Unix socket", async () => {
    const parent = path.join(root, "navigation"); mkdirSync(parent, { mode: 0o700 });
    const navigationSocket = path.join(parent, "bridge.sock"), digest = save({ navigationSocket });
    const server = createServer();
    await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(navigationSocket, resolve); });
    try {
      chmodSync(navigationSocket, 0o600);
      expect(readProductConfiguration(file, digest, []).navigationSocket).toBe(navigationSocket);
      chmodSync(navigationSocket, 0o666);
      expect(() => readProductConfiguration(file, digest, [])).toThrow("private_navigation_socket");
    } finally { await new Promise<void>((resolve) => server.close(() => resolve())); }
    writeFileSync(navigationSocket, "not a socket", { mode: 0o600 });
    expect(() => readProductConfiguration(file, digest, [])).toThrow("private_navigation_socket");
    rmSync(navigationSocket); symlinkSync(path.join(parent, "missing"), navigationSocket);
    expect(() => readProductConfiguration(file, digest, [])).toThrow("private_navigation_socket");
  });
  it.skipIf(process.platform !== "linux")("requires private Linux owner permissions", () => {
    chmodSync(token, 0o644);
    expect(() => readProductConfiguration(file, save(), [])).toThrow("private_product_path");
  });
});
