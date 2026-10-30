/** Synthetic protocol/UI fixture only. No native localization or robot connection. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash, X509Certificate } from "node:crypto";
import { chmod, mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pairingCodeHash } from "../../robot-api/src/auth";
import type { LocalizationBackend } from "../../robot-api/src/localization-backend";
import { buildServer } from "../../robot-api/src/server";
import { localizationFixture } from "../../robot-api/tests/fixtures/localization-state";
import { savedSlamFixture } from "../../robot-api/tests/fixtures/saved-slam-map";
import { sameLocalizationBinding, type RigidMatrix } from "../../shared/localization";

async function main() {
  assert.equal(process.platform, "linux");
  assert.notEqual(process.geteuid!(), 0);
  assert.equal(process.argv.length, 5);
  const root = path.resolve(process.argv[2]), web = await realpath(process.argv[3]);
  const seconds = Number(process.argv[4]);
  assert(Number.isInteger(seconds) && seconds >= 30 && seconds <= 900);
  assert.equal(path.dirname(root), await realpath(path.join(os.homedir(), ".cache/lingxi-next")));
  await mkdir(root, { mode: 0o700 });
  const key = path.join(root, "synthetic-key.pem"), certificate = path.join(root, "synthetic-cert.pem");
  const generated = spawnSync("/usr/bin/openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
    "-keyout", key, "-out", certificate, "-days", "1", "-subj", "/CN=Lingxi synthetic localization UI",
    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], { stdio: "ignore", timeout: 15_000 });
  assert.ifError(generated.error);
  assert.equal(generated.status, 0);
  await Promise.all([chmod(key, 0o600), chmod(certificate, 0o600)]);
  const tlsKey = await readFile(key), tlsCert = await readFile(certificate);
  const spki = createHash("sha256").update(new X509Certificate(tlsCert).publicKey.export({
    type: "spki", format: "der",
  })).digest("base64");
  const maps = path.join(root, "maps");
  await mkdir(maps, { mode: 0o700 });
  const map = await savedSlamFixture(maps, { session: "b".repeat(32), inputMode: "recorded" });
  await writeFile(map.ready, JSON.stringify({ ...map.receipt, name: "合成测试地图 / Synthetic" }));
  let state = localizationFixture(), outage = false, conflictNext = false;
  const receiptSha256 = createHash("sha256").update(await readFile(map.ready)).digest("hex");
  state.binding = { ...state.binding, receipt_sha256: receiptSha256,
    points_sha256: map.receipt.savedPointsSha256 };
  let acceptedPose: RigidMatrix | undefined;
  const commands: string[] = [];
  let rejected = 0, reads = 0, seed: RigidMatrix | undefined;
  const conflict = () => Object.assign(new Error("Synthetic generation conflict"), { statusCode: 409 });
  const backend: LocalizationBackend = {
    state: async () => {
      reads++;
      if (outage) throw Object.assign(new Error("Synthetic offline"), { statusCode: 503 });
      return structuredClone(state);
    },
    command: async (value) => {
      commands.push(value.action);
      if (conflictNext) { conflictNext = false; state.generation++; rejected++; throw conflict(); }
      if (outage || value.expectedGeneration !== state.generation ||
          !sameLocalizationBinding(value.binding, state.binding) || state.state === "stopped") throw conflict();
      if (value.action === "seed") seed = acceptedPose = structuredClone(value.mapFromSensor);
      state = { ...state, generation: state.generation + 1, requestPending: true,
        mapFromSensor: null, consecutiveMatches: 0,
        state: { seed: "seed_requested" as const, pause: "paused" as const, stop: "stopped" as const }[value.action],
        reason: value.action === "stop" ? "explicit_stop" : "worker_seed_confirmation_required" };
      return structuredClone(state);
    },
  };
  const app = await buildServer({
    demoMode: false, localizationConsole: true, localizationBackend: backend, savedSlamDirectory: maps,
    stateDirectory: path.join(root, "state"), staticDirectory: web,
    pairingCodeHash: pairingCodeHash("synthetic-localization-ui"), tlsKey, tlsCert,
  });
  app.get("/__synthetic/index", async (_request, reply) => {
    const html = await readFile(path.join(web, "index.html"), "utf8");
    return reply.type("text/html").send(html.replace("<body>", '<body><div class="synthetic-fixture-banner" ' +
      'style="padding:6px 12px;background:#fff1d8;color:#704c15;font-size:12px">' +
      "合成界面测试 · 未运行原生定位 · 未连接机器人</div>"));
  });
  // Test-only loopback controls stay outside the console's production write allowlist.
  app.get<{ Params: { command: string } }>("/__synthetic/:command", async (request, reply) => {
    const action = request.params.command;
    if (action === "outage") outage = true;
    else if (action === "recover") outage = false;
    else if (action === "map-mismatch") state.binding = { ...state.binding, receipt_sha256: "f".repeat(64) };
    else if (action === "map-restore") state.binding = { ...state.binding, receipt_sha256: receiptSha256 };
    else if (action === "conflict-next") conflictNext = true;
    else if (action === "track") {
      if (!acceptedPose || !["seed_requested", "acquiring"].includes(state.state)) return reply.code(409).send();
      state = { ...state, state: "tracking", requestPending: false, consecutiveMatches: 3,
        mapFromSensor: acceptedPose, reason: "matching_confirmed" };
    } else if (action === "ack") state = { ...state, requestPending: false };
    else if (action !== "status") return reply.code(404).send();
    return { classification: "isolated_synthetic_ui_fixture", commands, rejected, reads, seed,
      state: state.state, generation: state.generation };
  });
  const url = await app.listen({ host: "127.0.0.1", port: 0 });
  console.log(JSON.stringify({ classification: "isolated_synthetic_ui_fixture",
    url: url + "/__synthetic/index", baseUrl: url, certificateSpkiSha256: spki,
    pairingCode: "synthetic-localization-ui", robotContacted: false, nativeSlamExecuted: false,
    motionCommandsSent: false }));
  let closing = false;
  const close = async () => {
    if (closing) return;
    closing = true; clearTimeout(expiry); await app.close();
  };
  const expiry = setTimeout(() => void close(), seconds * 1000);
  process.once("SIGINT", () => void close());
  process.once("SIGTERM", () => void close());
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
