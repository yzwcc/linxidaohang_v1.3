/** Local, synthetic UI fixture. It does not run GLIM, access sensors or contact a robot. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pairingCodeHash } from "../../robot-api/src/auth";
import { type MappingBackend, type MappingBackendState } from "../../robot-api/src/mapping-backend";
import { buildServer } from "../../robot-api/src/server";
import { savedSlamFixture } from "../../robot-api/tests/fixtures/saved-slam-map";

async function main() {
  const root = path.resolve(process.argv[2] ?? "");
  const seconds = Number(process.argv[3]);
  const allowed = path.resolve(".local");
  assert(root.startsWith(allowed + path.sep) && !path.relative(allowed, root).startsWith(".."));
  assert(Number.isInteger(seconds) && seconds >= 30 && seconds <= 900);
  const inputMode = process.argv[4] ?? "recorded";
  assert(inputMode === "recorded" || inputMode === "live");
  const captureLimitSeconds = process.argv[5] === undefined ? undefined : Number(process.argv[5]);
  assert(inputMode === "live" ? Number.isFinite(captureLimitSeconds) && captureLimitSeconds! >= 5 && captureLimitSeconds! <= 300
    : captureLimitSeconds === undefined);
  await mkdir(root, { mode: 0o700 });
  const maps = path.join(root, "maps");
  await mkdir(maps);
  let state: MappingBackendState = {
    backend: "glim", inputMode, session: null, captureLimitSeconds,
    phase: "idle", mapping: false, cleanupComplete: true,
  };
  const backend: MappingBackend = {
    state: async () => state,
    start: async () => {
      assert.equal(state.mapping, false);
      state = { ...state, session: randomUUID().replaceAll("-", ""), phase: "running",
        mapping: true, cleanupComplete: false,
        mapper: { engine: "glim-full-async-v1", acceptedFrames: 20, completedFrames: 16, frameCapacity: 128,
          acceptedImu: 200, completedImu: 180, imuCapacity: 8192, maximumWaitMs: 1200,
          maximumTaskMs: 1400, ageSeconds: .1 } };
      return state;
    },
    finish: async (session, save, name) => {
      assert.equal(state.session, session);
      assert.equal(state.mapping, true);
      if (save) {
        const fixture = await savedSlamFixture(maps, { directory: session, session, inputMode });
        await writeFile(fixture.ready, JSON.stringify({ ...fixture.receipt, name }));
      }
      state = { ...state, phase: "idle", mapping: false, cleanupComplete: true };
      return { session, saved: save, confirmed: true, ...(save ? { mapId: `slam-${session}` } : {}) };
    },
  };
  const app = await buildServer({
    demoMode: true, mappingBackend: backend, savedSlamDirectory: maps,
    stateDirectory: path.join(root, "state"), staticDirectory: path.join(root, "no-static"),
    pairingCodeHash: pairingCodeHash("synthetic-ui-only"),
  });
  const url = await app.listen({ host: "127.0.0.1", port: 0 });
  console.log(JSON.stringify({
    classification: "isolated_synthetic_ui_fixture", url, pairingCode: "synthetic-ui-only",
    robotContacted: false, nativeSlamExecuted: false, motionCommandsSent: false,
  }));
  const timer = setTimeout(() => void app.close(), seconds * 1000);
  const close = async () => { clearTimeout(timer); await app.close(); };
  process.once("SIGINT", () => void close());
  process.once("SIGTERM", () => void close());
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
