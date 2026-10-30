import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

const bundle = process.env.LINGXI_LOCALIZATION_TEST_BUNDLE;
const skip = process.platform !== "linux" || !bundle || process.getuid() === 0
  ? "Explicit ordinary-user Linux compiled localization API required" : false;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function bounded(value) {
  let timer;
  try {
    return await Promise.race([value, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("localization API lifecycle deadline")), 8000);
    })]);
  } finally { clearTimeout(timer); }
}
async function start(t, patch = {}) {
  const directory = await mkdtemp(path.join(tmpdir(), "lx-la-"));
  await mkdir(path.join(directory, "maps"));
  const child = spawn(process.execPath, [path.resolve(bundle)], {
    cwd: directory, env: {
      PATH: "/usr/bin:/bin", LANG: "C.UTF-8", LINGXI_DEMO_MODE: "1",
      LINGXI_PORT: "0", LINGXI_LOCALIZATION_PARENT_STDIN: "1",
      LINGXI_LOCALIZATION_SOCKET: path.join(directory, "absent.sock"),
      LINGXI_SAVED_SLAM_DIR: path.join(directory, "maps"),
      LINGXI_DATA_DIR: path.join(directory, "state"),
      LINGXI_PAIRING_CODE_SHA256: createHash("sha256").update("synthetic-owned-localization").digest("hex"),
      ...patch,
    }, stdio: ["pipe", "pipe", "pipe"],
  });
  let output = "", stderr = "";
  child.stdout.on("data", (data) => { output += data; });
  child.stderr.on("data", (data) => { stderr += data; });
  const exited = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  t.after(async () => {
    try {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
      try { await bounded(exited); } catch (error) { child.kill("SIGKILL"); await bounded(exited); throw error; }
    } finally { await rm(directory, { recursive: true, force: true }); }
  });
  return { child, exited, stderr: () => stderr, async ready() {
    for (let count = 0; count < 320; count++) {
      const line = output.split("\n").find((item) => item.startsWith("{"));
      if (line) return JSON.parse(line);
      if (child.exitCode !== null) assert.fail("localization API exited before listen: " + stderr);
      await delay(25);
    }
    assert.fail("localization API startup deadline");
  } };
}
for (const shutdown of ["eof", "SIGTERM"]) {
  test(`parent ${shutdown} closes isolated API and paired WebSocket`, { skip }, async (t) => {
    const runtime = await start(t), status = await runtime.ready();
    const base = `http://127.0.0.1:${status.port}`;
    const pair = await fetch(base + "/api/v1/session/pair", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ pairingCode: "synthetic-owned-localization" }) });
    assert.equal(pair.status, 201);
    const { token } = await pair.json();
    const headers = { authorization: `Bearer ${token}` };
    const state = await (await fetch(base + "/api/v1/state", { headers })).json();
    assert.equal(state.connected, false);
    assert.equal(state.demoMode, false);
    assert.deepEqual(state.slamLocalization, { status: "unavailable" });
    assert.deepEqual(state.maps, []);
    const socket = new WebSocket(base.replace("http:", "ws:") + "/api/v1/events", ["lingxi-v1", `bearer.${token}`]);
    t.after(() => socket.close());
    await bounded(new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    }));
    const closed = new Promise((resolve) => socket.addEventListener("close", resolve, { once: true }));
    if (shutdown === "eof") runtime.child.stdin.end(); else runtime.child.kill(shutdown);
    assert.deepEqual(await bounded(runtime.exited), { code: 0, signal: null });
    await bounded(closed);
    await assert.rejects(fetch(base + "/health"));
    assert.equal(runtime.stderr(), "");
  });
}
for (const [scenario, patch] of [
  ["missing owner", { LINGXI_LOCALIZATION_PARENT_STDIN: "" }],
  ["production without TLS", { LINGXI_DEMO_MODE: "0" }],
  ["mixed mapping endpoint", { LINGXI_MAPPING_SOCKET: "/tmp/never-open-mapping.sock" }],
  ["relative socket", { LINGXI_LOCALIZATION_SOCKET: "relative.sock" }],
  ["invalid port", { LINGXI_PORT: "65536" }],
]) {
  test(`rejects ${scenario} before listening`, { skip }, async (t) => {
    const runtime = await start(t, patch);
    runtime.child.stdin.end();
    assert.deepEqual(await bounded(runtime.exited), { code: 1, signal: null });
    assert.equal(runtime.stderr(), "Lingxi Next localization API startup failed.\n");
  });
}
