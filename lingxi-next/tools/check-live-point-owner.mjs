import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const WebSocket = createRequire(import.meta.url)("ws");
const root = fileURLToPath(new URL("../", import.meta.url));
const bundle = process.env.LINGXI_POINT_API_BUNDLE;
const python = process.env.LINGXI_POINT_OWNER_PYTHON;
const skip = process.platform !== "linux" || !bundle || !python
  ? "Explicit Linux Node, Python and compiled point API required" : false;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function bounded(promise, milliseconds = 6_000) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("isolated owner deadline")), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

async function executing(pid) {
  try {
    const fields = (await readFile(`/proc/${pid}/stat`, "utf8")).split(") ").at(-1).split(" ");
    return !["Z", "X"].includes(fields[0]);
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

async function retired(pids) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if ((await Promise.all(pids.map(executing))).every((value) => !value)) return;
    await sleep(50);
  }
  assert.fail("owned child still executing after parent exit");
}

async function start(t, scenario = "healthy") {
  const directory = await mkdtemp(path.join(tmpdir(), "lingxi-owner-synthetic-"));
  const output = path.join(directory, "run");
  const child = spawn(python, [path.join(root, "tools/tests/fixtures/live_point_child.py"), "owner",
    "--scenario", scenario, "--output", output, "--node", process.execPath, "--bundle", path.resolve(bundle)], {
    cwd: root, env: { PATH: process.env.PATH, LANG: "C.UTF-8", PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let status, stderr = "", stdout = "";
  child.stderr.on("data", (bytes) => { stderr = (stderr + bytes).slice(-8_192); });
  child.stdout.on("data", (bytes) => {
    stdout = (stdout + bytes).slice(-8_192);
    for (const line of stdout.split("\n")) {
      try { status = JSON.parse(line); } catch { /* Await a complete bounded status line. */ }
    }
  });
  const exited = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  t.after(async () => {
    try {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
      try { await bounded(exited); }
      catch (error) {
        child.kill("SIGKILL");
        await bounded(exited);
        throw error;
      }
      if (status) await retired([status.sensorPid, status.apiPid]);
    } finally { await rm(directory, { recursive: true, force: true }); }
  });
  return {
    child, exited,
    report: async () => JSON.parse(await readFile(path.join(output, "owner-report.json"), "utf8")),
    async ready() {
      for (let attempt = 0; attempt < 300; attempt += 1) {
        if (status?.event === "listening") return status;
        if (child.exitCode !== null) assert.fail(`synthetic owner exited before ready: ${stderr}`);
        await sleep(50);
      }
      assert.fail("synthetic owner startup deadline");
    },
  };
}

async function point(runtime, t) {
  const status = await runtime.ready();
  const response = await fetch(`http://127.0.0.1:${status.port}/api/v1/session/pair`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ pairingCode: "isolated-point-owner-fixture" }),
  });
  assert.equal(response.status, 201);
  const { token } = await response.json();
  const socket = new WebSocket(`ws://127.0.0.1:${status.port}/api/v1/lidar/events`, ["lingxi", `bearer.${token}`]);
  t.after(() => socket.terminate());
  await bounded(once(socket, "open"));
  let receive;
  const frame = new Promise((resolve) => {
    receive = (bytes) => {
      const value = JSON.parse(bytes);
      if (value.type === "slam-preview") resolve(value);
    };
    socket.on("message", receive);
  });
  const delivered = await bounded(frame).finally(() => socket.off("message", receive));
  assert.equal(delivered.type, "slam-preview");
  assert.equal(delivered.inputMode, "live");
  assert.equal(delivered.coordinateUnits, "native-raw");
  assert.equal(delivered.frame.source, "point-cloud");
  assert.equal(delivered.frame.robotPose, undefined);
  return status;
}

test("owned synthetic sensor to real inherited API supports paired points and natural stop", { skip }, async (t) => {
  const runtime = await start(t);
  await point(runtime, t);
  runtime.child.kill("SIGTERM");
  assert.deepEqual(await bounded(runtime.exited), { code: 0, signal: null });
  const report = await runtime.report();
  assert.equal(report.result, "stopped");
  assert.equal(report.sensorProcess.cleanupComplete, true);
  assert.equal(report.sensorProcess.forcedTermination, false);
  assert.deepEqual(report.apiProcess, { exitCode: 0, forcedTermination: false, processAlive: false, groupAlive: false });
  assert.equal(report.mappingReady, false);
  assert.equal(report.navigationReady, false);
});

for (const peer of ["apiPid", "sensorPid"]) {
  test(`${peer} crash retires the other owned process without restart`, { skip }, async (t) => {
    const runtime = await start(t);
    const status = await point(runtime, t);
    process.kill(status[peer], "SIGKILL");
    assert.deepEqual(await bounded(runtime.exited), { code: 1, signal: null });
    await retired([status.apiPid, status.sensorPid]);
    assert.equal((await runtime.report()).result, "failed");
  });
}

test("abrupt parent death closes both owned children through private EOF", { skip }, async (t) => {
  const runtime = await start(t);
  const status = await point(runtime, t);
  runtime.child.kill("SIGKILL");
  await bounded(runtime.exited);
  await retired([status.apiPid, status.sensorPid]);
});

for (const scenario of ["before-connect", "no-connect", "api-startup"]) {
  test(`${scenario} failure cleans up without a listening success or automatic retry`, { skip }, async (t) => {
    const runtime = await start(t, scenario);
    assert.deepEqual(await bounded(runtime.exited, 15_000), { code: 1, signal: null });
    const report = await runtime.report();
    assert.equal(report.result, "failed");
    assert.equal(report.sensorProcess.processAlive, false);
    assert.equal(report.sensorProcess.cleanupComplete, true);
    if (scenario !== "api-startup") assert.equal(report.apiProcess, null);
  });
}

test("bounded duration stops both owned processes naturally without a signal", { skip }, async (t) => {
  const runtime = await start(t, "duration");
  const status = await runtime.ready();
  assert.deepEqual(await bounded(runtime.exited), { code: 0, signal: null });
  await retired([status.sensorPid, status.apiPid]);
  const report = await runtime.report();
  assert.equal(report.result, "stopped");
  assert.equal(report.sensorProcess.forcedTermination, false);
  assert.equal(report.apiProcess.forcedTermination, false);
});
