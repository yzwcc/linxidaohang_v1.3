import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { request as httpsRequest } from "node:https";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const bundle = process.env.LINGXI_LOCALIZATION_TEST_BUNDLE;
const python = process.env.LINGXI_LOCALIZATION_OWNER_PYTHON;
const skip = process.platform !== "linux" || !bundle || !python || process.getuid() === 0
  ? "Explicit ordinary-user Linux Python and compiled localization API required" : false;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function bounded(value) {
  let timer;
  try {
    return await Promise.race([value, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("localization owner test deadline")), 12_000);
    })]);
  } finally { clearTimeout(timer); }
}
async function retired(pids) {
  for (let count = 0; count < 200; count++) {
    const alive = await Promise.all(pids.map(async (pid) => {
      try {
        return !["Z", "X"].includes((await readFile(`/proc/${pid}/stat`, "utf8")).split(") ").at(-1).split(" ")[0]);
      } catch (error) {
        if (["ENOENT", "ESRCH"].includes(error.code)) return false;
        throw error;
      }
    }));
    if (alive.every((value) => !value)) return;
    await delay(25);
  }
  assert.fail("localization child still executing");
}
async function start(t, scenario = "healthy") {
  const directory = await mkdtemp(path.join(tmpdir(), "lx-lo-")), output = path.join(directory, "run");
  const key = path.join(directory, "key.pem"), cert = path.join(directory, "cert.pem");
  const tls = spawnSync("/usr/bin/openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
    "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=Isolated localization test",
    "-addext", "subjectAltName=IP:127.0.0.1"], { stdio: "ignore", timeout: 15_000 });
  assert.ifError(tls.error);
  assert.equal(tls.status, 0);
  const ca = await readFile(cert);
  const child = spawn(python, [path.join(root, "tools/tests/fixtures/managed_localization_child.py"),
    "owner", "--output", output, "--scenario", scenario, "--node", process.execPath,
    "--bundle", path.resolve(bundle), "--tls-key", key, "--tls-cert", cert], {
    cwd: root, env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8", PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let status, stdout = "", stderr = "";
  child.stdout.on("data", (data) => {
    stdout += data;
    for (const line of stdout.split("\n")) {
      try { status = JSON.parse(line); } catch { /* Await a complete status. */ }
    }
  });
  child.stderr.on("data", (data) => { stderr += data; });
  const exited = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  t.after(async () => {
    try {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
      try { await bounded(exited); } catch (error) { child.kill("SIGKILL"); await bounded(exited); throw error; }
      let nativePid = status?.nativePid;
      try { nativePid ??= Number(await readFile(path.join(output, "native/child.pid"), "utf8")); }
      catch (error) { if (error.code !== "ENOENT") throw error; }
      await retired([nativePid, status?.apiPid].filter(Boolean));
    } finally { await rm(directory, { recursive: true, force: true }); }
  });
  return { child, exited, output, report: async () => JSON.parse(
    await readFile(path.join(output, "owner-report.json"), "utf8")),
  async ready() {
    for (let count = 0; count < 1000; count++) {
      if (status?.event === "listening") return status;
      if (child.exitCode !== null) {
        const diagnostics = await Promise.all(["owner-report.json", "native/private-child.log",
          "private-api.log"].map(async (name) => {
          try { return `${name}: ${(await readFile(path.join(output, name), "utf8")).slice(-4096)}`; }
          catch (error) { if (error.code === "ENOENT") return `${name}: absent`; throw error; }
        }));
        assert.fail(`localization owner exited: ${stderr}\n${diagnostics.join("\n")}`);
      }
      await delay(10);
    }
    assert.fail("localization owner startup deadline");
  },
  send(port, route, method = "GET", body, token) {
    return new Promise((resolve, reject) => {
      const request = httpsRequest(`https://127.0.0.1:${port}/api/v1/${route}`, {
        method, ca, headers: { ...(body ? { "content-type": "application/json" } : {}),
          ...(token ? { authorization: `Bearer ${token}` } : {}) },
      }, (response) => {
        let data = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => { data += chunk; });
        response.on("error", reject);
        response.on("end", () => resolve({ status: response.statusCode, value: JSON.parse(data) }));
      });
      request.on("error", reject);
      request.setTimeout(3000, () => request.destroy(new Error("HTTPS localization test deadline")));
      request.end(body ? JSON.stringify(body) : undefined);
    });
  } };
}
function safe(report, inputsRechecked = true) {
  const live = report.inputMode === "live";
  assert.equal(report.inputComplete, !live && report.result === "completed");
  assert.equal(report.intervalComplete, live && report.result === "completed");
  assert.equal(report.inputsRechecked, inputsRechecked);
  assert.equal(report.failurePhase === null, report.failure === null);
  assert.equal(report.robotContacted, live); // Declared input mode; fixture performs no sensor I/O.
  for (const flag of ["motionCommandsSent", "localizationReady",
    "navigationReady", "mapProfileAssociationVerified"]) assert.equal(report[flag], false);
  assert.equal(report.nativeProcess.processAlive, false);
  assert.equal(report.nativeProcess.monitorAlive, false);
  assert.equal(report.nativeProcess.cleanupComplete, true);
  if (report.apiProcess) {
    assert.equal(report.apiProcess.processAlive, false);
    assert.equal(report.apiProcess.groupAlive, false);
  }
}

for (const interrupted of [false, true]) {
  test(`live interval ${interrupted ? "operator stop" : "completion"} verifies terminal cleanup`, { skip }, async (t) => {
    const runtime = await start(t, "live"), status = await runtime.ready();
    const pair = await runtime.send(status.port, "session/pair", "POST", { pairingCode: "owned-localization-test" });
    const token = pair.value.token;
    const state = await runtime.send(status.port, "slam/localization", "GET", undefined, token);
    assert.equal(state.status, 200);
    assert.equal(state.value.binding.input_mode, "live");
    const pause = await runtime.send(status.port, "slam/localization/pause", "POST", {
      binding: state.value.binding, expectedGeneration: state.value.generation,
    }, token);
    assert.equal(pause.status, 202);
    if (interrupted) runtime.child.kill("SIGTERM");
    assert.deepEqual(await bounded(runtime.exited), { code: interrupted ? 2 : 0, signal: null });
    const report = await runtime.report();
    safe(report);
    assert.equal(report.reason, interrupted ? "operator_stop" : "live_interval_complete");
    assert.equal(report.failure, null);
    assert.equal(report.nativeProcess.exitCode, 0);
    assert.equal(report.apiProcess.exitCode, 0);
    await assert.rejects(runtime.send(status.port, "slam/localization", "GET", undefined, token));
  });
}
for (const scenario of ["live-bad-report", "live-missing-report"]) {
  for (const interrupted of [false, true]) {
    test(`${scenario} rejects ${interrupted ? "operator stop" : "completion"}`, { skip }, async (t) => {
      const runtime = await start(t, scenario);
      await runtime.ready();
      if (interrupted) runtime.child.kill("SIGTERM");
      assert.deepEqual(await bounded(runtime.exited), { code: 1, signal: null });
      const report = await runtime.report();
      safe(report);
      assert.equal(report.failurePhase, "completion_verification");
      assert.equal(report.reason, interrupted ? "operator_stop" : "live_interval_complete");
    });
  }
}

test("native readiness precedes paired HTTPS; complete replay and both children close", { skip }, async (t) => {
  const runtime = await start(t), status = await runtime.ready();
  const paired = await runtime.send(status.port, "session/pair", "POST", { pairingCode: "owned-localization-test" });
  assert.equal(paired.status, 201);
  const token = paired.value.token;
  const state = await runtime.send(status.port, "slam/localization", "GET", undefined, token);
  assert.equal(state.status, 200);
  assert.equal(state.value.workerReady, true);
  assert.equal(state.value.binding.input_mode, "recorded");
  const pause = await runtime.send(status.port, "slam/localization/pause", "POST", {
    binding: state.value.binding, expectedGeneration: state.value.generation,
  }, token);
  assert.equal(pause.status, 202);
  assert.equal(pause.value.state, "paused");
  assert.equal((await runtime.send(status.port, "navigation/control", "POST", { action: "resume" }, token)).status, 403);
  assert.deepEqual(await bounded(runtime.exited), { code: 0, signal: null });
  const report = await runtime.report();
  safe(report);
  assert.equal(report.reason, "recorded_input_complete");
  assert.equal(report.failure, null);
  assert.equal(report.nativeProcess.completedMessages, 20);
  assert.equal(report.apiProcess.forcedTermination, false);
  assert.equal((await stat(runtime.output)).mode & 0o777, 0o700);
  assert.equal((await stat(path.join(runtime.output, "owner-report.json"))).mode & 0o777, 0o600);
  await assert.rejects(runtime.send(status.port, "state"));
});

for (const peer of ["apiPid", "nativePid"]) {
  test(`${peer} crash retires the other owned process`, { skip }, async (t) => {
    const runtime = await start(t), status = await runtime.ready();
    process.kill(status[peer], "SIGKILL");
    assert.deepEqual(await bounded(runtime.exited), { code: 1, signal: null });
    const report = await runtime.report();
    safe(report);
    assert.equal(report.result, "failed");
    assert.notEqual(report.failure, null);
    await retired([status.apiPid, status.nativePid]);
  });
}
test("owner SIGKILL independently closes both private input pipes", { skip }, async (t) => {
  const runtime = await start(t), status = await runtime.ready();
  runtime.child.kill("SIGKILL");
  assert.deepEqual(await bounded(runtime.exited), { code: null, signal: "SIGKILL" });
  await retired([status.apiPid, status.nativePid]);
});
for (const scenario of ["healthy", "partial-failure"]) {
  test(`SIGTERM with ${scenario} never claims complete input`, { skip }, async (t) => {
    const runtime = await start(t, scenario);
    await runtime.ready();
    runtime.child.kill("SIGTERM");
    assert.deepEqual(await bounded(runtime.exited), { code: scenario === "healthy" ? 2 : 1, signal: null });
    const report = await runtime.report();
    safe(report);
    assert.equal(report.result, scenario === "healthy" ? "interrupted" : "failed");
    assert.equal(report.reason, "operator_stop");
    assert.equal(report.inputComplete, false);
  });
}
for (const [scenario, phase, failure, apiStarted] of [
  ["wrong-binding", "native_ready", "ValueError", false],
  ["startup", "native_ready", ["RuntimeError", "TimeoutError"], false],
  ["api-startup", "api_start", "RuntimeError", true],
  ["missing-tls", "api_start", "RuntimeError", true],
  ["duration", "recorded_input", "TimeoutError", true],
  ["bad-completion", "completion_verification", "ValueError", true],
  ["changed-input", "input_verification", "ValueError", true],
]) {
  test(`${scenario} refuses completion and cleans up`, { skip }, async (t) => {
    const runtime = await start(t, scenario);
    assert.deepEqual(await bounded(runtime.exited), { code: 1, signal: null });
    const report = await runtime.report();
    safe(report, scenario !== "changed-input");
    assert.equal(report.result, "failed");
    assert.equal(report.inputComplete, false);
    assert.equal(report.failurePhase, phase);
    if (Array.isArray(failure)) assert.ok(failure.includes(report.failure));
    else assert.equal(report.failure, failure);
    assert.equal(report.apiProcess !== null, apiStarted);
    if (["bad-completion", "changed-input"].includes(scenario)) {
      assert.equal(report.reason, "recorded_input_complete");
      assert.equal(report.nativeProcess.completedMessages, 20);
      assert.equal(report.nativeProcess.exitCode, 0);
      assert.equal(report.apiProcess.exitCode, 0);
    } else if (scenario === "startup") {
      assert.equal(report.nativeProcess.completedMessages, 0);
      assert.equal(report.nativeProcess.healthEvents, 0);
    }
  });
}
