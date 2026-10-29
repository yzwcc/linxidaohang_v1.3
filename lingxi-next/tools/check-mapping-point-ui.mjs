import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, openSync, readFileSync, unlinkSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const [url, spki, session, outputArgument] = process.argv.slice(2);
assert.equal(process.argv.length, 6);
const parsed = new URL(url);
assert.equal(parsed.hostname, "127.0.0.1");
assert.equal(parsed.protocol, "https:");
assert.equal(parsed.pathname, "/");
assert(!parsed.username && !parsed.password && !parsed.search && !parsed.hash);
assert.match(spki, /^[A-Za-z0-9+/]{43}=$/);
assert.match(session, /^mapping-point-ui-[a-z0-9-]+$/);
const output = path.resolve(outputArgument);
await mkdir(output, { mode: 0o700 });
const browser = process.env.LINGXI_POINT_BROWSER_BIN ??
  "C:/Users/李伟铭/AppData/Roaming/npm/node_modules/agent-browser/bin/agent-browser-win32-x64.exe";
const run = (...args) => {
  // A newly launched Windows daemon can inherit pipes beyond the CLI's exit.
  const stdoutPath = path.join(output, "cli-stdout.tmp");
  const stderrPath = path.join(output, "cli-stderr.tmp");
  const stdout = openSync(stdoutPath, "w", 0o600), stderr = openSync(stderrPath, "w", 0o600);
  let command;
  try {
    command = spawnSync(browser, ["--session", session, "--allowed-domains", "127.0.0.1,localhost",
      "--args", `--ignore-certificate-errors-spki-list=${spki}`, "--json", ...args], {
      stdio: ["ignore", stdout, stderr], timeout: 40_000, windowsHide: true,
    });
  } finally {
    closeSync(stdout);
    closeSync(stderr);
  }
  const text = readFileSync(stdoutPath, "utf8"), error = readFileSync(stderrPath, "utf8");
  unlinkSync(stdoutPath);
  unlinkSync(stderrPath);
  assert.ifError(command.error);
  assert.equal(command.status, 0, error || text);
  const result = JSON.parse(text);
  assert.equal(result.success, true, JSON.stringify(result));
  return result.data;
};
const evaluate = (source) => run("eval", "-b", Buffer.from(source).toString("base64")).result;
const wait = (source) => run("wait", "--fn", source);
const click = (name) => run("find", "role", "button", "click", "--name", name, "--exact");
const control = async (command) => {
  const result = evaluate(`(async () => {
    const response = await fetch('/__synthetic/${command}', {
      method: 'POST', signal: AbortSignal.timeout(3000),
    });
    return { status: response.status, body: await response.json() };
  })()`);
  assert.equal(result.status, 200);
  assert.equal(result.body.classification, "isolated_synthetic_ui_fixture");
};
const report = { schemaVersion: 1, date: "2026-10-30", classification: "isolated_synthetic_ui_fixture",
  robotContacted: false, nativeSlamExecuted: false, targetRenderingValidated: false,
  motionCommandsSent: false, result: "failed", stages: {}, screenshots: {} };

function metric() {
  const canvas = document.querySelector(".lidar-view canvas");
  const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
  let nonBackground = 0, hash = 2166136261;
  for (let index = 0; index < data.length; index += 4) {
    if (data[index] !== 250 || data[index + 1] !== 251 || data[index + 2] !== 252) nonBackground++;
    hash = Math.imul(hash ^ data[index], 16777619);
  }
  return { viewport: [innerWidth, innerHeight], canvas: [canvas.width, canvas.height],
    nonBackground, hash: hash >>> 0, label: canvas.getAttribute("aria-label"),
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
    syntheticBanner: Boolean(document.querySelector(".demo-banner")),
    footer: document.querySelector(".lidar-footer").innerText,
    socketCount: window.__mappingUi.sockets.length, frames: window.__mappingUi.frames,
    epoch: window.__mappingUi.epoch, previewProvenance: window.__mappingUi.valid };
}
function capture(name, present) {
  const value = evaluate("window.__mappingMetric()");
  assert.equal(value.horizontalOverflow, false);
  assert.equal(value.syntheticBanner, true);
  if (present) {
    assert(value.nonBackground > 100);
    assert.equal(value.label, "录制数据建图点云");
    assert(value.footer.includes("录制数据验证"));
    assert.equal(value.previewProvenance, true);
  } else assert.equal(value.nonBackground, 0);
  report.stages[name] = value;
  const filename = path.join(output, name + ".png");
  run("screenshot", filename);
  return readFile(filename).then((bytes) => {
    report.screenshots[name] = { bytes: bytes.length,
      sha256: createHash("sha256").update(bytes).digest("hex") };
    return value;
  });
}

try {
  run("open", url);
  await control("status");
  run("set", "viewport", "1440", "900");
  run("snapshot", "-i");
  run("fill", "#robot-address", url);
  run("fill", "#pairing-code", "synthetic-mapping-point-ui");
  click("连接机器人");
  wait("Boolean(document.querySelector('.demo-banner'))");
  evaluate(`(() => {
    window.__mappingMetric = ${metric.toString()};
    window.__mappingUi = { sockets: [], frames: 0, valid: true, epoch: null };
    const Original = window.WebSocket;
    window.WebSocket = class extends Original {
      constructor(...args) {
        super(...args);
        if (String(args[0]).endsWith('/api/v1/lidar/events')) {
          window.__mappingUi.sockets.push(this);
          this.addEventListener('message', event => {
            const value = JSON.parse(event.data);
            if (value.type === 'lidar-unavailable') return;
            const state = window.__mappingUi;
            state.valid &&= value.type === 'slam-preview' && value.inputMode === 'recorded' &&
              value.coordinateUnits === 'native-raw';
            state.frames++; state.epoch = value.frame.epoch;
          });
        }
      }
    };
  })()`);
  click("实时建图");
  run("wait", "--text", "开始采集");
  await capture("idle", false);
  click("开始采集");
  wait("window.__mappingUi.frames > 2 && window.__mappingMetric().nonBackground > 100");
  const desktop = await capture("desktop", true);
  wait(`window.__mappingMetric().hash !== ${desktop.hash}`);
  await control("pause");
  wait("window.__mappingMetric().nonBackground === 0");
  await capture("stale", false);
  await control("resume");
  wait("window.__mappingMetric().nonBackground > 100");
  const resumed = await capture("resumed", true);
  evaluate("window.__mappingUi.sockets.at(-1).close(1000)");
  wait(`window.__mappingUi.sockets.length > ${resumed.socketCount} &&
    window.__mappingUi.frames > ${resumed.frames} && window.__mappingMetric().nonBackground > 100`);
  await capture("reconnected", true);
  run("find", "role", "textbox", "fill", "Synthetic mapping UI", "--name", "保存地图名称");
  click("停止并保存");
  run("wait", "--text", "开始采集");
  wait("window.__mappingMetric().nonBackground === 0");
  await capture("savedCleared", false);
  click("地图管理");
  run("wait", "--text", "Synthetic mapping UI");
  report.savedMapVisible = true;
  click("实时建图");
  run("set", "viewport", "844", "390");
  click("开始采集");
  wait(`window.__mappingMetric().nonBackground > 100 && window.__mappingUi.epoch !== '${desktop.epoch}'`);
  const landscape = await capture("landscape", true);
  run("click", 'button[title="放大扫描视图"]');
  wait(`window.__mappingMetric().hash !== ${landscape.hash}`);
  await capture("zoom", true);
  run("click", 'button[title="适应扫描范围"]');
  await control("eof");
  wait("window.__mappingMetric().nonBackground === 0");
  await capture("eof", false);
  click("停止并放弃");
  run("dialog", "accept");
  run("wait", "--text", "开始采集");
  await capture("discarded", false);
  click("地图管理");
  run("wait", "--text", "Synthetic mapping UI");
  report.savedMapPreservedAfterDiscard = true;
  report.storageKeys = evaluate("Object.keys(localStorage)");
  assert.deepEqual(report.storageKeys, ["lingxi.robotAddress"]);
  report.browserErrors = run("errors");
  assert.deepEqual(report.browserErrors.errors, []);
  report.result = "synthetic_mapping_point_browser_checks_passed";
} catch (error) {
  report.failure = error.message;
  try { run("screenshot", path.join(output, "failure.png")); } catch { /* Keep the original failure. */ }
  throw error;
} finally {
  await writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2) + "\n", { flag: "wx" });
  try { run("close"); } catch { /* The fixture has an independent expiry. */ }
}
console.log(JSON.stringify({ result: report.result, stages: Object.keys(report.stages).length }));
