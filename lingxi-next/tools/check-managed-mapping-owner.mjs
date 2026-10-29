import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { request as httpsRequest } from "node:https";
import test from "node:test";
import { fileURLToPath } from "node:url";

const WebSocket = createRequire(import.meta.url)("ws");
const root = fileURLToPath(new URL("../", import.meta.url));
const bundle = process.env.LINGXI_MAPPING_API_BUNDLE;
const python = process.env.LINGXI_MAPPING_OWNER_PYTHON;
const skip = process.platform !== "linux" || !bundle || !python || process.getuid() === 0
  ? "Explicit ordinary-user Linux Python and compiled managed API required" : false;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function bounded(promise, milliseconds = 12_000) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("managed mapping test deadline")), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}
async function retired(pids) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const alive = await Promise.all(pids.map(async (pid) => {
      try {
        return !["Z", "X"].includes((await readFile(`/proc/${pid}/stat`, "utf8")).split(") ").at(-1).split(" ")[0]);
      } catch (error) {
        if (["ENOENT", "ESRCH"].includes(error.code)) return false;
        throw error;
      }
    }));
    if (alive.every((value) => !value)) return;
    await sleep(25);
  }
  assert.fail("owned mapping process still executing");
}
async function start(t, scenario = "healthy") {
  const directory = await mkdtemp(path.join(tmpdir(), "lx-mm-"));
  const output = path.join(directory, "run");
  const tls = scenario === "production-tls";
  const key = path.join(directory, "synthetic-key.pem"), certificate = path.join(directory, "synthetic-cert.pem");
  let ca;
  if (tls) {
    const result = spawnSync("/usr/bin/openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
      "-keyout", key, "-out", certificate, "-days", "1", "-subj", "/CN=Lingxi isolated test",
      "-addext", "subjectAltName=IP:127.0.0.1"], { stdio: "ignore", timeout: 15_000 });
    assert.ifError(result.error);
    assert.equal(result.status, 0);
    ca = await readFile(certificate);
  }
  const child = spawn(python, [path.join(root, "tools/tests/fixtures/managed_mapping_child.py"),
    "owner", "--output", output, "--scenario", scenario, "--node", process.execPath, "--bundle", path.resolve(bundle),
    ...(tls ? ["--tls-key", key, "--tls-cert", certificate] : [])], {
    cwd: root, env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8", PYTHONDONTWRITEBYTECODE: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let status, stdout = "", stderr = "";
  const pids = [];
  child.stdout.on("data", (bytes) => {
    stdout = (stdout + bytes).slice(-8192);
    for (const line of stdout.split("\n")) {
      try { status = JSON.parse(line); } catch { /* Await the next complete line. */ }
    }
  });
  child.stderr.on("data", (bytes) => { stderr = (stderr + bytes).slice(-8192); });
  const exited = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  t.after(async () => {
    try {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
      try { await bounded(exited); }
      catch (error) { child.kill("SIGKILL"); await bounded(exited); throw error; }
      if (status) await retired([status.apiPid, status.servicePid, ...pids]);
    } finally { await rm(directory, { recursive: true, force: true }); }
  });
  return {
    child, exited, output, tls, ca,
    report: async () => JSON.parse(await readFile(path.join(output, "owner-report.json"), "utf8")),
    async ready() {
      for (let attempt = 0; attempt < 400; attempt += 1) {
        if (status?.event === "listening") return status;
        if (child.exitCode !== null) assert.fail(`owner exited before listening: ${stderr}`);
        await sleep(25);
      }
      assert.fail("managed mapping startup deadline");
    },
    async nativePid() {
      for (let attempt = 0; attempt < 200; attempt += 1) {
        try {
          const pid = Number(await readFile(path.join(output, "maps/synthetic-native.pid"), "utf8"));
          assert.ok(Number.isInteger(pid) && pid > 1);
          if (!pids.includes(pid)) {
            pids.push(pid);
            return pid;
          }
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        }
        await sleep(25);
      }
      assert.fail("synthetic native start deadline");
    },
  };
}
async function client(runtime, t) {
  const status = await runtime.ready();
  const base = `${runtime.tls ? "https" : "http"}://127.0.0.1:${status.port}`;
  const send = (url, options) => runtime.tls ? new Promise((resolve, reject) => {
    const connection = httpsRequest(url, { ...options, ca: runtime.ca }, (response) => {
      let data = "";
      response.setEncoding("utf8");
      response.on("data", (bytes) => { data += bytes; });
      response.on("end", () => resolve({ status: response.statusCode, json: async () => JSON.parse(data) }));
      response.on("error", reject);
    });
    connection.on("error", reject);
    connection.setTimeout(10_000, () => connection.destroy(new Error("HTTPS test deadline")));
    connection.end(options.body);
  }) : fetch(url, options);
  const paired = await send(`${base}/api/v1/session/pair`, { method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ pairingCode: "isolated-managed-mapping-test" }) });
  assert.equal(paired.status, 201);
  const { token } = await paired.json();
  const request = async (route, method = "GET", body) => send(`${base}/api/v1/${route}`, {
    method, headers: { authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "content-type": "application/json" }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const startMapping = async () => {
    assert.equal((await request("mapping/start", "POST")).status, 202);
    const nativePid = await runtime.nativePid();
    const socket = new WebSocket(`${runtime.tls ? "wss" : "ws"}://127.0.0.1:${status.port}/api/v1/lidar/events`,
      ["lingxi", `bearer.${token}`], runtime.tls ? { ca: runtime.ca } : {});
    t.after(() => socket.terminate());
    const delivered = new Promise((resolve) => {
      socket.on("message", (bytes) => {
        const event = JSON.parse(bytes);
        if (event.type === "slam-preview") resolve(event);
      });
    });
    await bounded(once(socket, "open"));
    const points = await bounded(delivered);
    assert.equal(points.coordinateUnits, "native-raw");
    assert.equal(points.inputMode, "recorded");
    assert.ok(points.frame.points.length > 0);
    const state = await (await request("state")).json();
    assert.equal(state.mappingBackend, "glim");
    assert.equal(state.mappingControlReady, false);
    return { nativePid, session: state.mappingSession, socket };
  };
  return { status, request, startMapping };
}

test("dedicated API supports paired point streaming, save, replacement and discard under one owner", { skip }, async (t) => {
  const runtime = await start(t), api = await client(runtime, t);
  const idle = await (await api.request("state")).json();
  assert.equal(idle.mappingControlReady, true);
  assert.deepEqual(idle.maps, []);
  assert.deepEqual(idle.tours, []);
  assert.equal((await api.request("navigation/control", "POST", { action: "resume" })).status, 403);
  const first = await api.startMapping();
  const saved = await api.request("mapping/stop-save", "POST", { session: first.session, name: "Synthetic owner map" });
  assert.equal(saved.status, 200);
  const result = await saved.json();
  assert.equal(result.savedSlamMap.navigationReady, false);
  assert.equal(result.savedSlamMap.coordinateUnits, "native-raw");
  await retired([first.nativePid]);
  const second = await api.startMapping();
  assert.notEqual(second.session, first.session);
  assert.equal((await api.request("mapping/stop-discard", "POST", { session: first.session })).status, 409);
  assert.equal((await api.request("mapping/stop-discard", "POST", { session: second.session })).status, 200);
  await retired([second.nativePid]);
  assert.equal((await (await api.request("slam/maps")).json()).maps.length, 1);
  runtime.child.kill("SIGTERM");
  assert.deepEqual(await bounded(runtime.exited), { code: 0, signal: null });
  const report = await runtime.report();
  assert.equal(report.result, "stopped");
  assert.equal(report.remainingChildren, 0);
  assert.equal(report.serviceProcess.forcedTermination, false);
  assert.equal(report.apiProcess.forcedTermination, false);
  assert.equal(report.mappingReady, false);
});

for (const peer of ["apiPid", "servicePid"]) {
  test(`${peer} crash closes the other peer and the active synthetic native child`, { skip }, async (t) => {
    const runtime = await start(t), api = await client(runtime, t);
    const active = await api.startMapping();
    process.kill(api.status[peer], "SIGKILL");
    assert.deepEqual(await bounded(runtime.exited), { code: 1, signal: null });
    await retired([active.nativePid, api.status.apiPid, api.status.servicePid]);
    const report = await runtime.report();
    assert.equal(report.result, "failed");
    assert.equal(report.remainingChildren, 0);
    if (peer === "servicePid") assert.ok(report.adoptedChildren.count >= 1);
    assert.equal((await readdir(path.join(runtime.output, "maps"))).filter((name) => name.endsWith("save-request.json")).length, 0);
  });
}

test("owner SIGKILL cascades private EOF through service, API and active native child", { skip }, async (t) => {
  const runtime = await start(t), api = await client(runtime, t);
  const active = await api.startMapping();
  runtime.child.kill("SIGKILL");
  await bounded(runtime.exited);
  await retired([active.nativePid, api.status.servicePid, api.status.apiPid]);
});

test("adopted native child ignoring EOF is killed within the cleanup budget and reported as failure", { skip }, async (t) => {
  const runtime = await start(t, "stubborn-native"), api = await client(runtime, t);
  const active = await api.startMapping();
  process.kill(api.status.servicePid, "SIGKILL");
  assert.deepEqual(await bounded(runtime.exited), { code: 1, signal: null });
  await retired([active.nativePid]);
  const report = await runtime.report();
  assert.equal(report.adoptedChildren.forcedTermination, true);
  assert.equal(report.adoptedChildren.cleanupComplete, true);
  assert.equal(report.remainingChildren, 0);
});

test("production HTTPS console accepts paired mapping without vendor or demo dependencies", { skip }, async (t) => {
  const runtime = await start(t, "production-tls"), api = await client(runtime, t);
  const state = await (await api.request("state")).json();
  assert.equal(state.demoMode, false);
  assert.equal(state.mappingControlReady, true);
  assert.deepEqual(state.maps, []);
  const active = await api.startMapping();
  assert.equal((await api.request("mapping/stop-discard", "POST", { session: active.session })).status, 200);
  await retired([active.nativePid]);
  assert.equal((await api.request("settings/ai", "PUT", {})).status, 403);
  runtime.child.kill("SIGTERM");
  assert.deepEqual(await bounded(runtime.exited), { code: 0, signal: null });
  assert.equal((await runtime.report()).remainingChildren, 0);
});

for (const scenario of ["service-startup", "api-startup", "production-no-tls", "duration"]) {
  test(`${scenario} preserves bounded startup and complete cleanup`, { skip }, async (t) => {
    const runtime = await start(t, scenario);
    assert.deepEqual(await bounded(runtime.exited), { code: scenario === "duration" ? 0 : 1, signal: null });
    const report = await runtime.report();
    assert.equal(report.result, scenario === "duration" ? "stopped" : "failed");
    assert.equal(report.remainingChildren, 0);
    assert.equal(report.adoptedChildren.cleanupComplete, true);
  });
}
