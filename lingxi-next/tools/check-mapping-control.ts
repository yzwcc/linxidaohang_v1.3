/** Explicit recorded-data integration probe; never connects to the robot or starts ROS. */
import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { pairingCodeHash } from "../robot-api/src/auth";
import { UnixMappingBackend } from "../robot-api/src/mapping-backend";
import { buildServer } from "../robot-api/src/server";

async function main() {
  const [socket, maps, output, countText] = process.argv.slice(2);
  const count = Number(countText);
  assert(socket && maps && output && Number.isSafeInteger(count) && count > 0);
  const backend = new UnixMappingBackend(socket);
  const app = await buildServer({
    demoMode: true, mappingBackend: backend, savedSlamDirectory: maps,
    stateDirectory: path.join(output, "api-state"), staticDirectory: path.join(output, "no-static"),
    pairingCodeHash: pairingCodeHash("recorded-control-probe-only"),
  });
  const report: Record<string, unknown> = {
    inputMode: "recorded", robotContacted: false, motionCommandsSent: false,
    liveSensorsSubscribed: false, navigationReady: false, localizationReady: false,
    result: "failed",
  };
  try {
    assert.equal((await app.inject({
      method: "POST", url: "/api/v1/mapping/start",
    })).statusCode, 401);
    const paired = await app.inject({
      method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "recorded-control-probe-only" },
    });
    assert.equal(paired.statusCode, 201);
    const headers = { authorization: `Bearer ${paired.json().token}` };
    const initial = (await app.inject({ url: "/api/v1/state", headers })).json();
    const sessions: Record<string, unknown>[] = [];
    report.sessions = sessions;
    for (const save of [true, false]) {
      const started = await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers });
      assert.equal(started.statusCode, 202, started.body);
      let state = await backend.state();
      const identity = state.session;
      assert(identity);
      const deadline = performance.now() + 100_000;
      while (state.completedMessages !== count && performance.now() < deadline) {
        assert.notEqual(state.phase, "failed", JSON.stringify(state));
        assert((state.completedMessages ?? 0) < count);
        await sleep(10);
        state = await backend.state();
      }
      assert.equal(state.completedMessages, count);
      const snapshot = (await app.inject({ url: "/api/v1/state", headers })).json();
      assert.equal(snapshot.mappingSession, identity);
      assert.equal(snapshot.mappingInputMode, "recorded");
      const name = "Recorded service map";
      const response = await app.inject({
        method: "POST", url: `/api/v1/mapping/${save ? "stop-save" : "stop-discard"}`, headers,
        payload: save ? { session: identity, name } : { session: identity },
      });
      assert.equal(response.statusCode, 200, response.body);
      assert.equal(response.json().confirmed, true);
      const terminal = await backend.state();
      assert.equal(terminal.mapping, false);
      assert.equal(terminal.cleanupComplete, true);
      assert.equal(terminal.phase, "idle");
      const operation = { session: identity, save, completedMessages: state.completedMessages,
        processedScans: terminal.processedScans, estimatedPoses: terminal.estimatedPoses };
      sessions.push(operation);
      if (save) {
        const map = response.json().savedSlamMap;
        assert.equal(map.id, `slam-${identity}`);
        assert.equal(map.name, name);
        assert.equal(map.inputMode, "recorded");
        assert.equal(map.navigationReady, false);
        const preview = await app.inject({ url: `/api/v1/slam/maps/${map.id}/points`, headers });
        assert.equal(preview.statusCode, 200, preview.body);
        assert.equal(preview.json().points.length / 3, map.counts.points);
        Object.assign(operation, { counts: map.counts, previewPoints: preview.json().points.length / 3 });
        // Same-session retries confirm the existing result without running a second save.
        const retried = await app.inject({
          method: "POST", url: "/api/v1/mapping/stop-save", headers, payload: { session: identity, name },
        });
        assert.equal(retried.statusCode, 200, retried.body);
      } else {
        const wrong = await app.inject({
          method: "POST", url: "/api/v1/mapping/stop-discard", headers,
          payload: { session: sessions[0].session },
        });
        assert.equal(wrong.statusCode, 409, wrong.body);
      }
      const after = (await app.inject({ url: "/api/v1/state", headers })).json();
      assert.deepEqual(after.maps, initial.maps);
      assert.equal(after.activeMapId, initial.activeMapId);
      assert.equal(after.savedSlamMaps.length, 1);
      assert.equal(after.checks.find((check: { id: string }) => check.id === "navigation").ready, false);
    }
    assert.notEqual(sessions[0].session, sessions[1].session);
    report.result = "recorded_native_service_api_save_discard_verified";
  } finally {
    await app.close();
    await writeFile(path.join(output, "private-api-report.json"), JSON.stringify(report, null, 2),
      { flag: "wx", mode: 0o600 });
  }
  console.log(JSON.stringify({ result: report.result }));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
