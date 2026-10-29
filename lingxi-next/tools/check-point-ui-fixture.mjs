import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { get } from "node:https";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { session, startSyntheticPreview, syntheticPacket } from "./fixtures/point-ui-server.mjs";

test("synthetic room packets have complete bounded XYZ, identity pose and explicit session", () => {
  const packet = syntheticPacket(7, 123);
  assert.equal(packet.subarray(0, 8).toString(), "LXSPOINT");
  assert.equal(packet.subarray(48, 64).toString("hex"), session);
  assert.equal(packet[10], 1);
  assert.equal(packet.readUInt32LE(12), 1600);
  assert.equal(packet.length, 192 + 1600 * 24);
  assert.equal(packet.readBigUInt64LE(24), 7n);
  assert.equal(packet.readDoubleLE(40), 123);
  for (let index = 0; index < 16; index += 1) assert.equal(packet.readDoubleLE(64 + index * 8), index % 5 === 0 ? 1 : 0);
  for (let index = 0; index < 4800; index += 1) assert.ok(Number.isFinite(packet.readDoubleLE(192 + index * 8)));
});

test("synthetic frames vary visually without reusing frame identities or algorithm times", () => {
  const first = syntheticPacket(1, 1), second = syntheticPacket(2, 1.2);
  assert.notDeepEqual(first.subarray(192), second.subarray(192));
  assert.ok(second.readDoubleLE(32) > first.readDoubleLE(32));
  assert.ok(second.readDoubleLE(40) > first.readDoubleLE(40));
});

test("invalid synthetic fixture arguments fail before startup", async () => {
  for (const [sequence, now] of [[0, 1], [1.5, 1], [1, Infinity], [1, -1]]) {
    assert.throws(() => syntheticPacket(sequence, now));
  }
  for (const seconds of [0, 901, NaN]) {
    await assert.rejects(startSyntheticPreview({ seconds }), /bounded_linux_x64/);
  }
});

const api = process.env.LINGXI_POINT_API_BUNDLE, web = process.env.LINGXI_POINT_WEB_ROOT;
test("actual compiled preview remains synthetic, uses packaged web, handles pause/EOF and exits naturally", {
  skip: process.platform !== "linux" || process.arch !== "x64" || !api || !web
    ? "Explicit Linux x64 compiled API and web root required" : false,
}, async (t) => {
  const root = await mkdtemp(path.join(tmpdir(), "lingxi-point-ui-test-"));
  let runtime;
  t.after(async () => {
    await runtime?.stop();
    assert.ok(root.startsWith(path.join(tmpdir(), "lingxi-point-ui-test-")));
    await rm(root, { recursive: true, force: true });
  });
  runtime = await startSyntheticPreview({ api, web, output: path.join(root, "trial"), seconds: 30 });
  assert.equal(runtime.info.inputClassification, "isolated_synthetic_ui_fixture");
  assert.equal(runtime.info.robotContacted, false);
  const certificate = await readFile(path.join(root, "trial/synthetic-cert.pem"));
  const html = await new Promise((resolve, reject) => {
    const request = get(runtime.info.url, { ca: certificate }, (response) => {
      assert.equal(response.statusCode, 200);
      let text = "";
      response.setEncoding("utf8");
      response.on("data", (data) => { text += data; });
      response.once("end", () => resolve(text));
    });
    request.once("error", reject);
  });
  assert.match(html, /assets\/.*\.js/);
  assert.equal((await fetch(runtime.info.controlUrl + "/unknown", { method: "POST" })).status, 404);
  const paused = await fetch(runtime.info.controlUrl + "/pause", { method: "POST" });
  assert.equal((await paused.json()).paused, true);
  assert.equal(runtime.command("pause").paused, true);
  assert.equal(runtime.command("resume").paused, false);
  assert.equal(runtime.command("eof").ended, true);
  assert.throws(() => runtime.command("resume"));
  const report = await runtime.stop();
  assert.equal(report.forcedTermination, false);
  assert.deepEqual(report.apiExit, { code: 0, signal: null });
  assert.equal(report.targetRenderingValidated, false);
});
