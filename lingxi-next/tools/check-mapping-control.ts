/** Explicit recorded-data integration probe; never connects to the robot or starts ROS. */
import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { pairingCodeHash } from "../robot-api/src/auth";
import { UnixMappingBackend } from "../robot-api/src/mapping-backend";
import { buildServer } from "../robot-api/src/server";
import type { LidarEvent } from "../shared/contracts";

async function main() {
  const [socket, maps, output, countText, pointMode] = process.argv.slice(2);
  const count = Number(countText);
  assert(socket && maps && output && Number.isSafeInteger(count) && count > 0 &&
    (pointMode === undefined || pointMode === "points"));
  const pointsEnabled = pointMode === "points";
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
  let client: Awaited<ReturnType<typeof app.injectWS>> | undefined;
  let lastEvent: LidarEvent | undefined, streamFailure: unknown;
  let expectedSession: string | undefined, unavailable = 0;
  const pointRuns = new Map<string, { uniqueFrames: number; lastSequence: number; lastStamp: number; maximumPoints: number }>();
  const waitFor = async (condition: () => boolean, milliseconds = 3000) => {
    const deadline = performance.now() + milliseconds;
    while (!condition() && performance.now() < deadline) {
      if (streamFailure) throw streamFailure;
      await sleep(10);
    }
    if (streamFailure) throw streamFailure;
    assert(condition(), "bounded_point_observation_required");
  };
  try {
    assert.equal((await app.inject({
      method: "POST", url: "/api/v1/mapping/start",
    })).statusCode, 401);
    const paired = await app.inject({
      method: "POST", url: "/api/v1/session/pair", payload: { pairingCode: "recorded-control-probe-only" },
    });
    assert.equal(paired.statusCode, 201);
    const token: string = paired.json().token;
    const headers = { authorization: `Bearer ${token}` };
    const openPoints = async () => {
      const peer = await app.injectWS("/api/v1/lidar/events", {
        headers: { "sec-websocket-protocol": `lingxi, bearer.${token}` },
      });
      peer.on("message", (bytes) => {
        try {
          const event: LidarEvent = JSON.parse(bytes.toString());
          if (event.type === "lidar-unavailable") {
            assert(!("frame" in event));
            unavailable++;
          } else {
            assert.equal(event.type, "slam-preview", "recorded_points_must_never_be_live_or_demo");
            if (event.type !== "slam-preview") throw new Error("explicit_preview_required");
            assert.equal(event.inputMode, "recorded");
            assert.equal(event.coordinateUnits, "native-raw");
            const frame = event.frame;
            assert.equal(frame.epoch, expectedSession, "retired_session_must_not_reappear");
            assert.equal(frame.fixedFrame, true);
            assert.equal(frame.source, "point-cloud");
            assert.equal(frame.frameId, "lingxi_glim_odom");
            assert.equal(frame.robotPose, undefined);
            assert(frame.points.length > 0 && frame.points.length <= 8000 * 3 &&
              frame.points.length % 3 === 0 && frame.points.every(Number.isFinite));
            assert(Date.now() - frame.receivedAtMs >= -50 && Date.now() - frame.receivedAtMs <= 2100);
            const run = pointRuns.get(frame.epoch!) ?? {
              uniqueFrames: 0, lastSequence: 0, lastStamp: 0, maximumPoints: 0,
            };
            assert(frame.sequence >= run.lastSequence && frame.stampMs >= run.lastStamp);
            if (frame.sequence > run.lastSequence) run.uniqueFrames++;
            run.lastSequence = frame.sequence;
            run.lastStamp = frame.stampMs;
            run.maximumPoints = Math.max(run.maximumPoints, frame.points.length / 3);
            pointRuns.set(frame.epoch!, run);
          }
          lastEvent = event;
        } catch (error) { streamFailure = error; }
      });
      return peer;
    };
    if (pointsEnabled) {
      client = await openPoints();
      await waitFor(() => lastEvent?.type === "lidar-unavailable");
    }
    const initial = (await app.inject({ url: "/api/v1/state", headers })).json();
    const sessions: Record<string, unknown>[] = [];
    report.sessions = sessions;
    for (const save of [true, false]) {
      const started = await app.inject({ method: "POST", url: "/api/v1/mapping/start", headers });
      assert.equal(started.statusCode, 202, started.body);
      let state = await backend.state();
      const identity = state.session;
      assert(identity);
      expectedSession = identity;
      let browserReconnected = false;
      const deadline = performance.now() + 100_000;
      while (state.completedMessages !== count && performance.now() < deadline) {
        assert.notEqual(state.phase, "failed", JSON.stringify(state));
        assert((state.completedMessages ?? 0) < count);
        await sleep(10);
        state = await backend.state();
        if (pointsEnabled && !browserReconnected && (pointRuns.get(identity)?.uniqueFrames ?? 0) >= 2) {
          client!.terminate();
          client = await openPoints();
          browserReconnected = true;
        }
        if (streamFailure) throw streamFailure;
      }
      assert.equal(state.completedMessages, count);
      if (pointsEnabled) {
        assert(browserReconnected);
        assert((pointRuns.get(identity)?.uniqueFrames ?? 0) >= 3);
      }
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
      if (pointsEnabled) {
        const previousUnavailable = unavailable;
        await waitFor(() => unavailable >= previousUnavailable + 2 && lastEvent?.type === "lidar-unavailable");
        expectedSession = undefined;
      }
      const operation = { session: identity, save, completedMessages: state.completedMessages,
        processedScans: terminal.processedScans, estimatedPoses: terminal.estimatedPoses };
      if (pointsEnabled) Object.assign(operation, {
        points: pointRuns.get(identity), browserReconnected, unavailableAfterStop: true,
      });
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
    if (pointsEnabled) {
      assert.equal(pointRuns.size, 2);
      report.pointStream = {
        pairedWebSocket: true, singleServerAcrossSessions: true, browserReconnects: 2,
        recordedProvenance: true, freshBoundedPoints: true, noDemoFallback: true, clearsAfterStop: true,
      };
    }
    report.result = "recorded_native_service_api_save_discard_verified";
  } finally {
    for (const peer of app.websocketServer.clients) peer.terminate();
    client?.terminate();
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
