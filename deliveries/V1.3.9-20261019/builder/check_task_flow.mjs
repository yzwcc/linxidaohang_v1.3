// Real console routes + RobotClient + Python HTTP handlers + JSON disk storage.
import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [consoleDir, webPort, bridgePort, mapRoot] = process.argv.slice(2);
const { buildApp } = await import(pathToFileURL(path.join(consoleDir, "dist/app.js")).href);
const { createRobotClient } = await import(pathToFileURL(path.join(consoleDir, "dist/robotClient.js")).href);
const config = {
  deploymentMode: "robot", host: "127.0.0.1", port: 0, publicBaseUrl: "http://127.0.0.1",
  robot: { id: "default", host: "127.0.0.1", connectionMode: "direct",
    webPort: Number(webPort), bridgePort: Number(bridgePort), mapRoot },
  auth: { devBypass: true, tokenSecret: "isolated-fixture", tokenTtlSec: 3600 }
};
const app = buildApp(config, createRobotClient(config));
let requests = 0;
async function call(method, route, payload) {
  const response = await app.inject({ method, url: "/api/robots/default" + route, payload });
  requests++;
  assert.equal(response.statusCode, 200, response.body);
  return response.json();
}
try {
  for (const id of ["p1", "p2"]) {
    await call("POST", "/maps/123/points", { id, name: id, map_id: 123,
      pose: { position: { x: id === "p1" ? 1 : 2, y: 0, z: 0 },
        orientation: { x: 0, y: 0, z: 0, w: 1 } } });
  }
  await call("GET", "/maps/123"); // Populate the real client cache before mutation.
  const task = {
    id: "persisted", name: "Persistent functional tour", command_topic: "开始参观",
    start_phrases: ["带我参观"], loop_count: 3, enabled: true,
    steps: [
      { point_id: "p2", speech_text: "Second point first", wait_sec: 300,
        arrival_motion: { motion: "wave", area: "left_hand" },
        arrival_emoji: { emotion: "happy", mode: "once", priority: 65 } },
      { point_id: "p1", speech_text: "Then the first point", wait_sec: 0 }
    ]
  };
  const saved = await call("POST", "/maps/123/task", task);
  assert.equal(saved.id, task.id);
  assert.equal(saved.loop_count, 3);
  await call("POST", "/maps/123/task", { id: "other", name: "Other", steps: [{ point_id: "p1" }] });
  const map = await call("GET", "/maps/123");
  const restored = map.tour_tasks.find(t => t.id === "persisted");
  assert.equal(restored.steps[0].point_id, "p2");
  assert.equal(restored.steps[0].arrival_motion.area, "left_hand");
  assert.equal(restored.steps[0].arrival_emoji.emotion, "happy");
  assert.equal(restored.steps[0].wait_sec, 300);
  assert.equal(restored.steps[1].wait_sec, 0);
  assert.equal(restored.loop_count, 3);
  const commands = await call("GET", "/task/commands");
  assert.ok(JSON.stringify(commands).includes("带我参观"));
  const started = await call("POST", "/task/start", { task_id: "persisted" });
  assert.equal(started.accepted, true, JSON.stringify(started));
  assert.equal(started.goal_id, "p2");
  assert.equal(started.task_id, "persisted");
  assert.equal(started.loop_count, 3);
  assert.equal((await call("POST", "/task/start", { task_id: "other" })).status, "busy");
  assert.equal((await call("POST", "/task/pause", {})).paused, true);
  assert.equal((await call("POST", "/task/resume", {})).resumed, true);
  assert.equal((await call("POST", "/task/stop", {})).stopped, true);
  assert.equal((await call("POST", "/task/start", { task_id: "missing" })).status, "invalid_task");
  await call("POST", "/maps/123/task", { id: "disabled", name: "Disabled", enabled: false,
    steps: [{ point_id: "p1" }] });
  assert.equal((await call("POST", "/task/start", { task_id: "disabled" })).status, "no_task_steps");
  await call("POST", "/maps/123/task", { id: "empty", name: "Empty", steps: [] });
  assert.equal((await call("POST", "/task/start", { task_id: "empty" })).status, "no_task_steps");
  await call("POST", "/nav/map", { active_map_name: "456" });
  assert.equal((await call("POST", "/task/start", { task_id: "persisted" })).status, "invalid_task");
  await call("POST", "/nav/map", { active_map_name: "123" });
  assert.equal((await call("POST", "/task/start", { task_id: "persisted" })).goal_id, "p2");
  await call("POST", "/task/stop", {});
  console.log(`TASK_HTTP_DISK_START_PASS requests=${requests}`);
} finally {
  await app.close();
}
