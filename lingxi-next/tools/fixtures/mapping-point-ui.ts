/** Bounded Linux UI fixture. Generated packets exercise recorded-mode presentation only. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash, randomUUID, X509Certificate } from "node:crypto";
import { chmod, mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import { createServer, type Server, type Socket } from "node:net";
import os from "node:os";
import path from "node:path";
import { pairingCodeHash } from "../../robot-api/src/auth";
import { type MappingBackend, type MappingBackendState } from "../../robot-api/src/mapping-backend";
import { buildServer } from "../../robot-api/src/server";
import { savedSlamFixture } from "../../robot-api/tests/fixtures/saved-slam-map";
// @ts-expect-error The existing JS fixture has no declaration file.
import { syntheticPacket } from "./synthetic-point-packet.mjs";

async function main() {
  assert.equal(process.platform, "linux");
  assert.notEqual(process.geteuid!(), 0);
  assert.equal(process.argv.length, 5);
  const root = path.resolve(process.argv[2]);
  const web = await realpath(process.argv[3]);
  const seconds = Number(process.argv[4]);
  assert(Number.isInteger(seconds) && seconds >= 30 && seconds <= 900);
  const cache = await realpath(path.join(os.homedir(), ".cache/lingxi-next"));
  assert.equal(path.dirname(root), cache);
  await mkdir(root, { mode: 0o700 });
  const key = path.join(root, "synthetic-key.pem"), certificate = path.join(root, "synthetic-cert.pem");
  const generated = spawnSync("/usr/bin/openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes",
    "-keyout", key, "-out", certificate, "-days", "1", "-subj", "/CN=Lingxi synthetic mapping UI",
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
  let state: MappingBackendState = {
    backend: "glim", inputMode: "recorded", session: null,
    phase: "idle", mapping: false, cleanupComplete: true,
  };
  let server: Server | undefined, client: Socket | undefined;
  let sequence = 0, paused = false, ended = false;
  let timer: ReturnType<typeof setInterval> | undefined;
  const closePoints = async () => {
    clearInterval(timer);
    client?.destroy();
    client = undefined;
    if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
    server = undefined;
  };
  const backend: MappingBackend = {
    state: async () => state,
    start: async () => {
      assert.equal(state.mapping, false);
      await closePoints();
      const session = randomUUID().replaceAll("-", "");
      const endpoint = path.join(maps, session + ".points.sock");
      assert(Buffer.byteLength(endpoint) < 100);
      sequence = 0; paused = false; ended = false;
      server = createServer((socket) => {
        if (client) { socket.destroy(); return; }
        client = socket;
        socket.on("error", () => { ended = true; });
      });
      await new Promise<void>((resolve, reject) => {
        server!.once("error", reject);
        server!.listen(endpoint, resolve);
      });
      await chmod(endpoint, 0o600);
      state = { ...state, session, phase: "running", mapping: true, cleanupComplete: false,
        pointStream: { socket: path.basename(endpoint) } };
      timer = setInterval(() => {
        if (!client || client.destroyed || client.writableNeedDrain || paused || ended) return;
        const packet: Buffer = syntheticPacket(++sequence);
        packet[10] = 2;
        Buffer.from(session, "hex").copy(packet, 48);
        client.write(packet);
      }, 200);
      return state;
    },
    finish: async (session, save, name) => {
      assert.equal(state.session, session);
      assert.equal(state.mapping, true);
      state = { ...state, phase: save ? "saving" : "stopping" };
      await closePoints();
      if (save) {
        const fixture = await savedSlamFixture(maps, { directory: session, session, inputMode: "recorded" });
        await writeFile(fixture.ready, JSON.stringify({ ...fixture.receipt, name }));
      }
      state = { ...state, pointStream: undefined, phase: "idle", mapping: false, cleanupComplete: true };
      return { session, saved: save, confirmed: true, ...(save ? { mapId: `slam-${session}` } : {}) };
    },
  };
  const app = await buildServer({
    demoMode: true, mappingBackend: backend, savedSlamDirectory: maps,
    stateDirectory: path.join(root, "state"), staticDirectory: web,
    pairingCodeHash: pairingCodeHash("synthetic-mapping-point-ui"),
    tlsKey, tlsCert,
  });
  app.post<{ Params: { command: string } }>("/__synthetic/:command", async (request, reply) => {
    if (request.params.command === "pause") paused = true;
    else if (request.params.command === "resume" && !ended) paused = false;
    else if (request.params.command === "eof") { ended = true; client?.end(); }
    else if (request.params.command !== "status") return reply.code(404).send();
    return { classification: "isolated_synthetic_ui_fixture", sequence, paused, ended };
  });
  const url = await app.listen({ host: "127.0.0.1", port: 0 });
  console.log(JSON.stringify({
    classification: "isolated_synthetic_ui_fixture", url, certificateSpkiSha256: spki,
    pairingCode: "synthetic-mapping-point-ui",
    robotContacted: false, nativeSlamExecuted: false, motionCommandsSent: false,
  }));
  let closing = false;
  const close = async () => {
    if (closing) return;
    closing = true;
    clearTimeout(expiry);
    await app.close();
    await closePoints();
  };
  const expiry = setTimeout(() => void close(), seconds * 1000);
  process.once("SIGINT", () => void close());
  process.once("SIGTERM", () => void close());
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
