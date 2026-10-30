/** Bounded passive target check of the real mapping owner, HTTPS and WSS. No motion. */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { closeSync, openSync } from "node:fs";
import { mkdir, readFile, readdir, realpath, writeFile } from "node:fs/promises";
import { request as httpsRequest } from "node:https";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const root = fileURLToPath(new URL("../", import.meta.url));
const WebSocket = createRequire(import.meta.url)("ws");
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const { values } = parseArgs({ options: Object.fromEntries(
  ["config", "python", "api", "web", "output", "seconds"].map((name) => [name, { type: "string" }]),
) });
assert.equal(process.platform, "linux");
assert.equal(process.arch, "arm64");
assert.notEqual(process.getuid(), 0);
for (const name of ["config", "python", "api", "web", "output"]) assert(path.isAbsolute(values[name] ?? ""));
const seconds = Number(values.seconds ?? "12");
assert(Number.isInteger(seconds) && seconds >= 10 && seconds <= 30);
const config = JSON.parse(await readFile(values.config));
assert.equal(config.platform, "arm64-humble");
assert.equal(config.inputMode, "live");
assert(config.maxSeconds >= seconds + 15 && config.pointAdapter);
assert.equal(await realpath(path.dirname(values.output)), path.dirname(values.output));
assert(Buffer.byteLength(path.join(values.output, "run/maps", "a".repeat(32) + ".points.sock")) < 100);
await mkdir(values.output, { mode: 0o700 });
const key = path.join(values.output, "key.pem"), certificate = path.join(values.output, "cert.pem");
const tls = spawnSync("/usr/bin/openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
  "-keyout", key, "-out", certificate, "-days", "1", "-subj", "/CN=Lingxi passive mapping check",
  "-addext", "subjectAltName=IP:127.0.0.1"], { stdio: "ignore", timeout: 15_000 });
assert.ifError(tls.error);
assert.equal(tls.status, 0);
const ca = await readFile(certificate), code = randomBytes(24).toString("hex");
const report = { result: "failed", inputMode: "live", machine: process.arch,
  robotContacted: true, motionCommandsSent: false, navigationReady: false, localizationReady: false,
  geometryValidated: false, sessions: [], httpsVerified: false, webAssetsVerified: false,
  apiBundleSha256: hash(await readFile(values.api)), nodeSha256: hash(await readFile(process.execPath)),
  sourceSha256: {}, cleanupConfirmed: false };
for (const name of ["tools/check-live-mapping.mjs", "robot-runtime/python/managed_mapping.py",
  "robot-runtime/python/mapping_owner.py", "robot-runtime/python/mapping_service.py",
  "robot-runtime/python/controlled_mapping.py", "robot-runtime/python/mapping_platform.py",
  "robot-slam/lingxi_slam/passive_ros.py", "robot-slam/lingxi_slam/mapping_worker.py",
  "robot-slam/lingxi_slam/mapping_session.py", "robot-api/src/mapping-backend.ts",
  "robot-api/src/server.ts", "robot-api/src/mapping-points.ts",
  "robot-runtime/src/mapping-api.ts", "robot-runtime/src/mapping-main.ts",
  "robot-api/src/domain.ts", "shared/contracts.ts", "web/src/App.tsx"]) {
  report.sourceSha256[name] = hash(await readFile(path.join(root, name)));
}
const descriptor = openSync(path.join(values.output, "private-launcher.log"), "wx", 0o600);
let child;
try {
  child = spawn(values.python, ["-B", path.join(root, "robot-runtime/python/managed_mapping.py"),
    "--config", values.config, "--output", path.join(values.output, "run"),
    "--node", process.execPath, "--node-sha256", report.nodeSha256,
    "--api-bundle", values.api, "--api-sha256", report.apiBundleSha256, "--seconds", "180"], {
    env: { PATH: "/usr/bin:/bin", LANG: "C.UTF-8", PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1",
      LINGXI_HOST: "127.0.0.1", LINGXI_PORT: "0", LINGXI_TLS_KEY: key, LINGXI_TLS_CERT: certificate,
      LINGXI_PAIRING_CODE_SHA256: hash(code), LINGXI_WEB_ROOT: values.web },
    stdio: ["ignore", "pipe", descriptor], detached: true,
  });
} finally { closeSync(descriptor); }
let port, startup = "", processError, exitResult;
child.on("error", (error) => { processError = error; });
child.stdout.on("data", (bytes) => {
  startup += bytes;
  if (startup.length > 4096) { processError = new Error("bounded_mapping_startup_required"); return; }
  if (!startup.endsWith("\n")) return;
  try {
    const status = JSON.parse(startup);
    assert.equal(status.service, "lingxi-next");
    assert.equal(status.event, "listening");
    assert(Number.isInteger(status.port) && status.port > 0 && status.port <= 65535);
    port = status.port;
  } catch (error) { processError = error; }
});
const exited = new Promise((resolve) => child.once("close", (code, signal) => {
  exitResult = { code, signal }; resolve(exitResult);
}));
let token, peer, streamError, lastEvent, expectedSession;
const frames = new Map();
async function waitFor(condition, milliseconds = 15_000) {
  const deadline = Date.now() + milliseconds;
  while (!condition() && Date.now() < deadline) {
    if (processError || streamError) throw processError ?? streamError;
    assert(!exitResult, "mapping_owner_exited_before_check_finished");
    await sleep(25);
  }
  if (processError || streamError) throw processError ?? streamError;
  assert(condition(), "mapping_observation_deadline");
}
function request(route, method = "GET", body, expected = 200) {
  return new Promise((resolve, reject) => {
    const headers = { ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(body !== undefined ? { "content-type": "application/json" } : {}) };
    const req = httpsRequest({ hostname: "127.0.0.1", port, path: route, method, ca, headers,
      timeout: method === "POST" ? 130_000 : 4000 }, (res) => {
      const parts = []; let length = 0;
      res.on("data", (bytes) => {
        length += bytes.length;
        if (length > 8 * 1024 * 1024) res.destroy(new Error("bounded_mapping_response_required"));
        else parts.push(bytes);
      });
      res.on("error", reject);
      res.on("end", () => {
        try { assert.equal(res.statusCode, expected, `mapping_http_status:${route}`); resolve(Buffer.concat(parts)); }
        catch (error) { reject(error); }
      });
    });
    req.once("timeout", () => req.destroy(new Error("mapping_https_deadline")));
    req.once("error", reject);
    req.end(body === undefined ? undefined : JSON.stringify(body));
  });
}
const api = async (route, method, body, expected) => JSON.parse(
  await request("/api/v1/" + route, method, body, expected));
function openPoints() {
  peer = new WebSocket(`wss://127.0.0.1:${port}/api/v1/lidar/events`, ["lingxi", `bearer.${token}`], { ca });
  peer.on("error", (error) => { streamError = error; });
  peer.on("message", (bytes) => {
    try {
      const event = JSON.parse(bytes);
      if (event.type !== "lidar-unavailable") {
        assert.equal(event.type, "slam-preview");
        assert.equal(event.inputMode, "live");
        assert.equal(event.coordinateUnits, "native-raw");
        assert.equal(event.frame.epoch, expectedSession);
        assert.equal(event.frame.fixedFrame, true);
        assert.equal(event.frame.robotPose, undefined);
        const points = event.frame.points;
        assert(points.length > 0 && points.length <= 24_000 && points.length % 3 === 0 && points.every(Number.isFinite));
        assert(Date.now() - event.frame.receivedAtMs >= -50 && Date.now() - event.frame.receivedAtMs < 2200);
        const seen = frames.get(expectedSession) ?? { count: 0, last: 0, maximumPoints: 0 };
        assert(event.frame.sequence >= seen.last);
        if (event.frame.sequence > seen.last) seen.count++;
        seen.last = event.frame.sequence;
        seen.maximumPoints = Math.max(seen.maximumPoints, points.length / 3);
        frames.set(expectedSession, seen);
      }
      lastEvent = event;
    } catch (error) { streamError = error; }
  });
}
try {
  await waitFor(() => port !== undefined, 45_000);
  const paired = await api("session/pair", "POST", { pairingCode: code }, 201);
  assert.equal(typeof paired.token, "string"); token = paired.token;
  report.httpsVerified = true;
  const html = (await request("/")).toString("utf8");
  const script = html.match(/src="(\/assets\/[^" ]+\.js)"/);
  assert(script); assert((await request(script[1])).length > 1000);
  report.webAssetsVerified = true;
  const initial = await api("state");
  assert.equal(initial.demoMode, false); assert.equal(initial.mappingInputMode, "live");
  assert.equal(initial.checks.find((check) => check.id === "lidar")?.ready, false);
  for (const save of [true, false]) {
    await api("mapping/start", "POST", undefined, 202);
    const started = await api("state");
    expectedSession = started.mappingSession;
    assert.match(expectedSession, /^[a-f0-9]{32}$/);
    openPoints();
    await waitFor(() => (frames.get(expectedSession)?.count ?? 0) >= 3, 30_000);
    const beforeReconnect = frames.get(expectedSession).last;
    peer.terminate(); openPoints();
    await waitFor(() => frames.get(expectedSession).last > beforeReconnect);
    await sleep(seconds * 1000);
    if (streamError) throw streamError;
    const state = await api("state");
    assert.equal(state.mappingStatus, "running");
    assert(state.mappingProgress?.completedMessages > 0 && state.mappingProgress.processedScans >= 3 &&
      state.mappingProgress.estimatedPoses >= 3);
    assert.equal(state.checks.find((check) => check.id === "lidar")?.ready, true);
    assert.equal(state.checks.find((check) => check.id === "navigation")?.ready, false);
    const result = await api(`mapping/stop-${save ? "save" : "discard"}`, "POST",
      { session: expectedSession, ...(save ? { name: "Passive live mapping check" } : {}) });
    assert.equal(result.confirmed, true);
    await waitFor(() => lastEvent?.type === "lidar-unavailable");
    const summary = { save, ...state.mappingProgress, frames: frames.get(expectedSession), reconnectConfirmed: true };
    if (save) {
      const map = result.savedSlamMap;
      assert.equal(map.inputMode, "live"); assert.equal(map.navigationReady, false);
      assert.equal(map.localizationReady, false);
      const preview = await api(`slam/maps/${map.id}/points`);
      assert.equal(preview.points.length / 3, map.counts.points);
      summary.counts = map.counts;
      summary.previewPoints = preview.points.length / 3;
    }
    const stopped = await api("state");
    assert.equal(stopped.mapping, false); assert.equal(stopped.mappingControlReady, true);
    assert.equal(stopped.checks.find((check) => check.id === "lidar")?.ready, false);
    assert.deepEqual(stopped.maps, initial.maps);
    assert.equal(stopped.savedSlamMaps.length, 1);
    report.sessions.push({ ...summary, livePointHealthConfirmed: true, stoppedPointHealthCleared: true });
    peer.terminate(); peer = undefined; expectedSession = undefined;
  }
  child.kill("SIGTERM");
  assert.deepEqual(await exited, { code: 0, signal: null });
  const run = path.join(values.output, "run");
  const owner = JSON.parse(await readFile(path.join(run, "owner-report.json")));
  assert.equal(owner.result, "stopped"); assert.equal(owner.remainingChildren, 0);
  for (const entry of [owner.serviceProcess, owner.apiProcess]) {
    assert.equal(entry.exitCode, 0); assert.equal(entry.forcedTermination, false);
    assert.equal(entry.processAlive, false); assert.equal(entry.groupAlive, false);
  }
  report.owner = owner;
  report.children = [];
  for (const name of (await readdir(path.join(run, "maps"))).filter((name) => name.endsWith(".private-report.json"))) {
    const native = JSON.parse(await readFile(path.join(run, "maps", name)));
    assert.equal(native.inputMode, "live"); assert(["saved", "discarded"].includes(native.result));
    assert.equal(native.nativeOwnerIsSingleNonMainThread, true);
    assert.equal(native.nativeCalls.create, 1); assert.equal(native.nativeCalls.close, 1);
    assert.equal(native.worker.threadAlive, false); assert.equal(native.controlThreadAlive, false);
    assert.equal(native.worker.failure, null);
    assert(native.points.every((point) => !point.failed && !point.threadAlive));
    report.children.push({ result: native.result, counts: native.worker.counts,
      nativeCalls: native.nativeCalls, points: native.points, passive: native.passive });
  }
  assert.equal(report.children.length, 2);
  report.cleanupConfirmed = true;
  report.result = "arm64_passive_live_mapping_https_save_discard_verified";
} catch (error) {
  report.error = { name: error.name, message: error.message };
  throw error;
} finally {
  peer?.terminate();
  if (!exitResult) {
    child.kill("SIGTERM");
    let timer;
    try {
      await Promise.race([exited, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("mapping_owner_cleanup_deadline")), 140_000);
      })]);
    } finally { clearTimeout(timer); }
  }
  report.ownerExit = exitResult;
  await writeFile(path.join(values.output, "check-report.json"), JSON.stringify(report, null, 2) + "\n", { flag: "wx", mode: 0o600 });
}
console.log(JSON.stringify({ result: report.result, sessions: report.sessions.length, cleanupConfirmed: report.cleanupConfirmed }));
