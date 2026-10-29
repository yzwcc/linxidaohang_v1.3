import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { open, mkdtemp, readFile, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const require = createRequire(import.meta.url);
const WebSocket = require("ws");
const root = fileURLToPath(new URL("../", import.meta.url));
const fixture = JSON.parse(await readFile(path.join(root, "shared/fixtures/slam-point-v1.synthetic.json"), "utf8"));
const bundle = process.env.LINGXI_POINT_API_BUNDLE;
const skip = process.platform !== "linux" || !bundle
  ? "Explicit Linux Node and LINGXI_POINT_API_BUNDLE required" : false;

async function deadline(promise, label, milliseconds = 5_000) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label)), milliseconds); }),
    ]);
  } finally { clearTimeout(timer); }
}

function packet(mode, id = 1) {
  // Every payload here is an isolated synthetic fixture, including live-mode protocol tests.
  const bytes = Buffer.from(fixture.hex, "hex");
  bytes[10] = { live: 1, recorded: 2, synthetic: 3 }[mode];
  bytes.writeBigUInt64LE(BigInt(id), 24);
  bytes.writeDoubleLE(bytes.readDoubleLE(32) + (id - 1) * 0.1, 32);
  bytes.writeDoubleLE(Number(process.hrtime.bigint()) / 1e9, 40);
  return bytes;
}

function message(socket, predicate) {
  let accept, reject;
  const result = new Promise((resolve, fail) => { accept = resolve; reject = fail; });
  const receive = (bytes) => {
    try {
      const value = JSON.parse(bytes.toString());
      if (predicate(value)) accept(value);
    } catch (error) { reject(error); }
  };
  socket.on("message", receive);
  return deadline(result, "expected point API message").finally(() => socket.off("message", receive));
}

async function start(t, overrides = {}, regularInput = false) {
  const directory = await mkdtemp(path.join(tmpdir(), "lingxi-point-runtime-synthetic-"));
  let input;
  if (regularInput) input = await open(path.join(root, "shared/fixtures/slam-point-v1.synthetic.json"), "r");
  const child = spawn(process.execPath, [path.resolve(bundle)], {
    cwd: root,
    env: {
      PATH: process.env.PATH, LANG: "C.UTF-8",
      LINGXI_DEMO_MODE: "1", LINGXI_PORT: "0",
      LINGXI_DATA_DIR: directory, LINGXI_WEB_ROOT: path.join(directory, "no-static"),
      LINGXI_DEMO_PAIRING_CODE: "isolated-synthetic-point-runtime",
      LINGXI_SLAM_POINT_FD: "3", LINGXI_SLAM_SESSION: fixture.session,
      LINGXI_SLAM_INPUT_MODE: "live", ...overrides,
    },
    stdio: ["ignore", "pipe", "pipe", regularInput ? input.fd : "pipe"],
  });
  let output = "", errors = "";
  child.stdout.on("data", (bytes) => { output = (output + bytes).slice(-16_384); });
  child.stderr.on("data", (bytes) => { errors = (errors + bytes).slice(-16_384); });
  const exit = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  t.after(async () => {
    try {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
      await deadline(exit, "point API cleanup deadline");
    } catch (error) {
      child.kill("SIGKILL");
      await exit;
      throw error;
    } finally {
      await input?.close();
      await rm(directory, { recursive: true, force: true });
    }
  });
  return {
    child, exit, errors: () => errors,
    async listening() {
      let inspect;
      const ready = new Promise((resolve, reject) => {
        inspect = () => {
          for (const line of output.split("\n")) {
            try {
              const event = JSON.parse(line);
              if (event.event === "listening" && Number.isInteger(event.port) && event.port > 0) {
                resolve(`http://127.0.0.1:${event.port}`);
              }
            } catch { /* Non-JSON diagnostics are not point bytes. */ }
          }
        };
        child.stdout.on("data", inspect);
        void exit.then(() => reject(new Error(`API exited before listening: ${errors}`)), reject);
        inspect();
      });
      return deadline(ready, "point API startup deadline", 15_000)
        .finally(() => child.stdout.off("data", inspect));
    },
  };
}

async function paired(runtime) {
  const url = await runtime.listening();
  assert.equal((await fetch(`${url}/api/v1/state`)).status, 401);
  const pair = await fetch(`${url}/api/v1/session/pair`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ pairingCode: "isolated-synthetic-point-runtime" }),
  });
  assert.equal(pair.status, 201);
  const { token } = await pair.json();
  assert.equal(typeof token, "string");
  const headers = { authorization: `Bearer ${token}` };
  const state = async () => {
    const response = await fetch(`${url}/api/v1/state`, { headers });
    assert.equal(response.status, 200);
    return response.json();
  };
  const socket = new WebSocket(`${url.replace("http:", "ws:")}/api/v1/lidar/events`, ["lingxi", `bearer.${token}`]);
  await deadline(once(socket, "open"), "paired WebSocket startup");
  return { socket, state };
}

test("real inherited socket serves paired points, rejects stale/EOF data and shuts down naturally", { skip }, async (t) => {
  const runtime = await start(t);
  const { socket, state } = await paired(runtime);
  t.after(() => socket.terminate());
  const before = await state();
  const frame = message(socket, (value) => value.type === "lidar");
  runtime.child.stdio[3].write(packet("live"));
  const delivered = (await frame).frame;
  assert.deepEqual(delivered.points, fixture.points);
  assert.equal(delivered.source, "point-cloud");
  assert.equal(delivered.robotPose, undefined);
  assert.equal(delivered.cells, undefined);
  const stale = await message(socket, (value) => value.type === "lidar-unavailable");
  assert.equal(stale.type, "lidar-unavailable");
  const after = await state();
  assert.deepEqual(after.checks, before.checks);
  assert.deepEqual(after.navigation, before.navigation);
  assert.equal(after.mapping, before.mapping);
  const freshAgain = message(socket, (value) => value.type === "lidar" && value.frame.sequence === 2);
  runtime.child.stdio[3].write(packet("live", 2));
  await freshAgain;
  const unavailable = message(socket, (value) => value.type === "lidar-unavailable");
  runtime.child.stdio[3].end();
  await unavailable;
  runtime.child.kill("SIGTERM");
  assert.deepEqual(await deadline(runtime.exit, "natural SIGTERM exit"), { code: 0, signal: null });
});

for (const mode of ["recorded", "synthetic"]) {
  test(`${mode} input is not exposed as live by the actual entrypoint`, { skip }, async (t) => {
    const runtime = await start(t, { LINGXI_SLAM_INPUT_MODE: mode });
    const { socket } = await paired(runtime);
    t.after(() => socket.terminate());
    let liveFrames = 0;
    socket.on("message", (bytes) => { if (JSON.parse(bytes.toString()).type === "lidar") liveFrames += 1; });
    const unavailable = message(socket, (value) => value.type === "lidar-unavailable");
    runtime.child.stdio[3].end(packet(mode));
    await unavailable;
    assert.equal(liveFrames, 0);
    runtime.child.kill("SIGINT");
    assert.deepEqual(await deadline(runtime.exit, "natural SIGINT exit"), { code: 0, signal: null });
  });
}

test("a malformed point stream stays unavailable without falling back to demo lidar", { skip }, async (t) => {
  const runtime = await start(t);
  const { socket } = await paired(runtime);
  t.after(() => socket.terminate());
  const invalid = packet("live");
  invalid[0] = 0;
  const unavailable = message(socket, (value) => value.type === "lidar-unavailable");
  runtime.child.stdio[3].write(invalid);
  await unavailable;
  assert.equal(runtime.child.exitCode, null);
});

for (const [name, environment, regularInput] of [
  ["missing session", { LINGXI_SLAM_SESSION: "" }, false],
  ["non-socket inherited file", {}, true],
  ["invalid listen port", { LINGXI_PORT: "-1" }, false],
  ["production without TLS", { LINGXI_DEMO_MODE: "0" }, false],
]) {
  test(`${name} fails closed and exits naturally`, { skip }, async (t) => {
    const runtime = await start(t, environment, regularInput);
    assert.deepEqual(await deadline(runtime.exit, "natural invalid-startup exit", 15_000), { code: 1, signal: null });
    assert.equal(runtime.errors().trim(), "Lingxi Next inherited-point API startup failed.");
  });
}
