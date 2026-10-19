// Exercise the packaged server and its own production dependencies, with no robot connection.
import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";
const consoleDir = path.resolve(process.argv[2]);
const { buildApp } = await import(pathToFileURL(path.join(consoleDir, "dist/app.js")).href);
const saved = [];
const robot = new Proxy({
  saveTask: async task => { saved.push(task); return { ok: true, ...task }; }
}, {
  get(target, name) {
    if (name in target) return target[name];
    return () => { throw new Error(`Unexpected robot operation: ${String(name)}`); };
  }
});
const app = buildApp({
  deploymentMode: "robot", host: "127.0.0.1", port: 0, publicBaseUrl: "http://127.0.0.1",
  robot: { host: "127.0.0.1", connectionMode: "direct" },
  auth: { devBypass: true, tokenSecret: "isolated-fixture", tokenTtlSec: 3600 }
}, robot);
let requests = 0;
try {
  for (const url of ["/health", "/console/", "/console/app.js", "/console/styles.css", "/console/robot-hero.png"]) {
    const response = await app.inject({ method: "GET", url });
    requests++;
    assert.equal(response.statusCode, 200, url);
    if (url.endsWith(".png")) assert.equal(response.rawPayload.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
  }
  for (const loop_count of [1, 3, 20]) {
    for (const wait_sec of [0, 300]) {
      const response = await app.inject({
        method: "POST", url: "/api/robots/default/maps/123/task",
        payload: { id: "fixture", name: "Fixture", loop_count, command_topic: "开始讲解",
          steps: [{ point_id: "p1", wait_sec, arrival_motion: { motion: "wave", area: "right_hand" } }] }
      });
      requests++;
      assert.equal(response.statusCode, 200);
      assert.equal(saved.at(-1).loop_count, loop_count);
      assert.equal(saved.at(-1).steps[0].wait_sec, wait_sec);
      assert.equal(saved.at(-1).steps[0].arrival_motion.area, "right_hand");
    }
  }
  const before = saved.length;
  for (const invalid of [{ loop_count: 0 }, { loop_count: 21 }, { loop_count: "3" },
    { steps: [{ point_id: "p1", wait_sec: -1 }] }, { steps: [{ point_id: "p1", wait_sec: 301 }] }]) {
    const response = await app.inject({ method: "POST", url: "/api/robots/default/maps/123/task",
      payload: { name: "Fixture", ...invalid } });
    requests++;
    assert.equal(response.statusCode, 400);
  }
  assert.equal(saved.length, before);
  console.log(`PACKAGED_CONSOLE_PASS requests=${requests} mocked_saves=${saved.length}`);
} finally {
  await app.close();
}
