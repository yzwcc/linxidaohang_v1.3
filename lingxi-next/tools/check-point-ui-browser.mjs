import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pairingCode } from "./fixtures/point-ui-server.mjs";

const [url, controlUrl, spki, session, outputArgument] = process.argv.slice(2);
assert.equal(process.argv.length, 7, "explicit synthetic URL/control/SPKI/session/output required");
for (const value of [url, controlUrl]) {
  const parsed = new URL(value);
  assert.equal(parsed.hostname, "127.0.0.1");
  assert.equal(parsed.pathname, "/");
  assert.ok(!parsed.username && !parsed.password && !parsed.search && !parsed.hash);
}
assert.equal(new URL(url).protocol, "https:");
assert.equal(new URL(controlUrl).protocol, "http:");
assert.match(spki, /^[A-Za-z0-9+/]{43}=$/);
assert.match(session, /^point-ui-[a-z0-9-]+$/);
const output = path.resolve(outputArgument);
await mkdir(output, { mode: 0o700 });
const browserArgs = ["--session", session, "--allowed-domains", "127.0.0.1,localhost",
  "--args", `--ignore-certificate-errors-spki-list=${spki}`, "--json"];
const browser = process.env.LINGXI_POINT_BROWSER_BIN ?? "agent-browser";
if (process.platform === "win32") assert.ok(path.isAbsolute(browser), "explicit native browser CLI path required");
const run = (...args) => {
  const result = JSON.parse(execFileSync(browser, [...browserArgs, ...args],
    { encoding: "utf8", timeout: 40_000, maxBuffer: 4 * 1024 * 1024 }));
  assert.equal(result.success, true, JSON.stringify(result.error));
  return result.data;
};
const evaluate = (expression) => run("eval", expression).result;
const wait = (condition) => run("wait", "--fn", condition);
const command = async (name) => {
  const result = await fetch(`${controlUrl}/${name}`, { method: "POST", signal: AbortSignal.timeout(3000) });
  assert.equal(result.status, 200);
  const state = await result.json();
  assert.equal(state.inputClassification, "isolated_synthetic_ui_fixture");
  return state;
};
const evidence = {
  schemaVersion: 1, inputClassification: "isolated_synthetic_ui_fixture",
  robotContacted: false, nativeSlamExecuted: false, targetRenderingValidated: false,
  physicalLatencyValidated: false, motionCommandsSent: false, result: "failed", stages: {}, screenshots: {},
};

function metrics() {
  const c = document.querySelector(".lidar-view canvas");
  const data = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
  let nonBackground = 0, hash = 2166136261, minX = Infinity, maxX = -Infinity;
  for (let index = 0; index < data.length; index += 4) {
    const x = (index / 4) % c.width, y = Math.floor(index / 4 / c.width);
    const changed = data[index] !== 250 || data[index + 1] !== 251 || data[index + 2] !== 252;
    if (changed) nonBackground++;
    if (changed && y < c.height - 50 && x > 24) { minX = Math.min(minX, x); maxX = Math.max(maxX, x); }
    hash = Math.imul(hash ^ data[index], 16777619);
  }
  const rect = c.getBoundingClientRect();
  return {
    viewport: [innerWidth, innerHeight], canvas: [c.width, c.height], nonBackground, hash: hash >>> 0,
    pointSpanX: Number.isFinite(minX) ? maxX - minX : 0,
    pointCenterX: Number.isFinite(minX) ? (minX + maxX) / 2 : 0,
    canvasVisible: rect.top >= 0 && rect.bottom <= innerHeight,
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
    footer: document.querySelector(".lidar-footer").innerText,
    syntheticBanner: Boolean(document.querySelector(".demo-banner")),
    socketCount: window.__pointUi.sockets.length, frames: window.__pointUi.frames,
  };
}

async function capture(name, present) {
  const value = evaluate("window.__pointMetric()");
  assert.equal(value.syntheticBanner, true);
  assert.equal(value.horizontalOverflow, false);
  assert.equal(value.canvasVisible, true);
  if (present) assert.ok(value.nonBackground > 100, name);
  else assert.equal(value.nonBackground, 0, name);
  evidence.stages[name] = value;
  const filename = path.join(output, name + ".png");
  run("screenshot", filename);
  const bytes = await readFile(filename);
  evidence.screenshots[name] = { sha256: createHash("sha256").update(bytes).digest("hex"), bytes: bytes.length };
  return value;
}

try {
  await command("status");
  run("open", url);
  run("set", "viewport", "1440", "900");
  run("snapshot", "-i");
  run("fill", "#robot-address", url);
  run("fill", "#pairing-code", pairingCode);
  run("find", "role", "button", "click", "--name", "连接机器人", "--exact");
  wait("Boolean(document.querySelector('.demo-banner'))");
  evaluate(`(() => {
    window.__pointMetric = ${metrics.toString()};
    window.__pointUi = { sockets: [], frames: 0 };
    const WS = window.WebSocket;
    window.WebSocket = class extends WS {
      constructor(...args) {
        super(...args);
        if (String(args[0]).endsWith('/api/v1/lidar/events')) {
          window.__pointUi.sockets.push(this);
          this.addEventListener('message', event => {
            if (JSON.parse(event.data).type === 'lidar') window.__pointUi.frames++;
          });
        }
      }
    };
  })()`);
  run("find", "role", "button", "click", "--name", "实时建图", "--exact");
  wait("window.__pointUi.frames > 1 && window.__pointMetric().nonBackground > 100");
  const desktop = await capture("desktop", true);
  wait(`window.__pointMetric().hash !== ${desktop.hash}`);
  evidence.stages.moving = evaluate("window.__pointMetric()");
  run("set", "viewport", "844", "390");
  wait("window.__pointMetric().viewport[0] === 844 && window.__pointMetric().canvas[1] < 390");
  const landscape = await capture("landscape", true);
  run("find", "title", "放大扫描视图", "click");
  wait(`window.__pointMetric().pointSpanX > ${landscape.pointSpanX * 1.1}`);
  await capture("zoom", true);
  run("find", "title", "适应扫描范围", "click");
  wait(`Math.abs(window.__pointMetric().pointSpanX - ${landscape.pointSpanX}) < 8`);
  const fitted = await capture("fit", true);
  const rect = evaluate("document.querySelector('.lidar-view canvas').getBoundingClientRect().toJSON()");
  const x = Math.round(rect.x + rect.width / 2), y = Math.round(rect.y + rect.height / 2);
  run("mouse", "move", String(x), String(y));
  run("mouse", "down", "left");
  run("mouse", "move", String(x + 40), String(y + 20));
  run("mouse", "up", "left");
  wait(`window.__pointMetric().pointCenterX > ${fitted.pointCenterX + 25}`);
  await capture("pan", true);
  run("find", "title", "适应扫描范围", "click");
  run("uncheck", ".lidar-footer input");
  assert.equal(evaluate("document.querySelector('.lidar-footer input').checked"), false);
  run("find", "title", "清除累计点迹", "click");
  await command("pause");
  wait("window.__pointMetric().nonBackground === 0");
  await capture("stale", false);
  await command("resume");
  wait("window.__pointMetric().nonBackground > 100");
  const resumed = await capture("resumed", true);
  evaluate("window.__pointUi.sockets.at(-1).close(1000)");
  wait(`window.__pointUi.sockets.length > ${resumed.socketCount} && window.__pointUi.frames > ${resumed.frames} && window.__pointMetric().nonBackground > 100`);
  await capture("reconnected", true);
  await command("eof");
  wait("window.__pointMetric().nonBackground === 0");
  await capture("eof", false);
  evidence.storageKeys = evaluate("Object.keys(localStorage)");
  assert.deepEqual(evidence.storageKeys, ["lingxi.robotAddress"]);
  evidence.browserErrors = run("errors");
  assert.deepEqual(evidence.browserErrors.errors, []);
  evidence.result = "synthetic_browser_checks_passed_not_target_acceptance";
} catch (error) {
  evidence.failure = error.message;
  try { run("screenshot", path.join(output, "failure.png")); } catch { /* Preserve the original failure. */ }
  throw error;
} finally {
  await writeFile(path.join(output, "browser-evidence.json"), JSON.stringify(evidence, null, 2) + "\n", { flag: "wx" });
}
console.log(JSON.stringify({ result: evidence.result, stages: Object.keys(evidence.stages).length }));
